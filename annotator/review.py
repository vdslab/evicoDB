#!/usr/bin/env python3
"""Build a review sheet for human checking of extraction runs.

Pass one run folder, or several runs of the same prompt. One row per (diagnosis, molecule,
method) over all runs; the result and qualifiers shown are the majority over the runs that
found it. Rows are sorted by review priority so that findings likely to be wrong come first.
Priority uses only the extractions and the input text, so it works without ground truth too.

  高: the runs disagree (different results, or found in only some runs), the evidence reads
      like a note on how a test is used rather than a result, the evidence is not in the
      input text, or one run gives conflicting results for the molecule
  中: "altered" although the evidence has no word for a gene alteration (e.g. "repression")
  低: none of the above

Hedging words (may, usually, ...) are listed for reference but do not raise the priority:
they are common in textbook text and did not single out wrong findings.
"""
import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from evaluate import (
    DIAGNOSIS_MAPPING,
    canonical_molecule,
    find_input_text,
    index_ground_truth,
    load_extracted_json,
    load_ground_truth_rows,
    map_result_status,
    normalize_name,
    source_file_name,
)

SCRIPT_DIR = Path(__file__).resolve().parent
HEDGE_PATTERN = re.compile(
    r"\b(may|might|can|could|rarely|rare|occasional(?:ly)?|reported|some cases|subset"
    r"|variabl\w*|inconsistent|weak(?:ly)?|usually|often|up to)\b",
    re.IGNORECASE,
)
# Notes on how a test is used ("useful to identify", "required to confirm") rather than a result
USAGE_PATTERN = re.compile(
    r"\b(useful|helpful|help(?:s)? (?:to )?(?:distinguish|identify|confirm|highlight)|aid(?:s)? in"
    r"|(?:required|needed|necessary) to|to (?:distinguish|confirm|identify|exclude|highlight)"
    r"|misleading|can be used|is performed)\b",
    re.IGNORECASE,
)
GENETIC_WORDS = re.compile(
    r"mutat|fusion|amplif|delet|rearrang|transloc|\bloss|alter|variant|inactivat|copy|gain"
    r"|t\(\d|heterozygosity|splic|skipping|insertion|duplicat|methylat",
    re.IGNORECASE,
)
PRIORITY_ORDER = {"高": 0, "中": 1, "低": 2}


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def ground_truth_for(source_name: str, diagnosis: dict, label_csv: Path) -> dict:
    """Ground-truth findings (canonical molecule -> item) of the mapped diagnosis, or {} if none."""
    entries = DIAGNOSIS_MAPPING.get(source_name, [])
    key = normalize_name(diagnosis.get("diagnosis"))
    entry = next((e for e in entries if normalize_name(e["who_diagnosis"]) == key), None)
    if entry is None:
        same_code = [e for e in entries if e["icd_o"] == diagnosis.get("icd_o")]
        entry = same_code[0] if len(same_code) == 1 else None
    if entry is None or not entry["labels"]:
        return {}
    return index_ground_truth(load_ground_truth_rows(label_csv, entry["labels"]))


def run_findings(json_path: Path) -> dict:
    """(diagnosis key, molecule key) -> (diagnosis, [findings]) for one run of one input file."""
    data = load_extracted_json(json_path)
    out = {}
    for diagnosis in data.get("diagnoses") or []:
        dkey = normalize_name(diagnosis.get("diagnosis"))
        for f in data.get("findings", []):
            # Findings for this diagnosis: listed in applies_to, or common to all (empty applies_to)
            if f.get("applies_to") and dkey not in {normalize_name(a) for a in f["applies_to"]}:
                continue
            mkey = canonical_molecule(f.get("molecule_name") or "", f.get("method"))
            if mkey:
                out.setdefault((dkey, mkey), (diagnosis, []))[1].append(f)
    return out


def review_rows(name: str, run_dirs: list, label_csv: Path) -> list:
    """Rows for one input file (e.g. '044_Alveolar adenoma.json') over all runs."""
    paths = [run / name for run in run_dirs if (run / name).exists()]
    n = len(paths)
    data = load_extracted_json(paths[0])
    source_name = source_file_name(data, paths[0])
    input_text = normalize_text(find_input_text(data, paths[0]) or "")

    per_run = [run_findings(p) for p in paths]
    rows = []
    for key in sorted({k for r in per_run for k in r}):
        found = [r[key] for r in per_run if key in r]
        diagnosis = found[0][0]
        # One result per run: several findings for the key in a run are joined ("negative/positive")
        run_results = ["/".join(sorted({f.get("result_normalized") or "" for f in fs})) for _, fs in found]
        result, result_votes = Counter(run_results).most_common(1)[0]
        base = next(fs[0] for (_, fs), r in zip(found, run_results) if r == result)
        qualifier_votes = Counter(q for _, fs in found for q in {q for f in fs for q in f.get("qualifiers") or []})
        qualifiers = [q for q, c in qualifier_votes.most_common() if c * 2 > len(found)]
        evidence = base.get("evidence_text") or ""

        reasons_high, reasons_mid = [], []
        if len(found) < n:
            reasons_high.append(f"{n}回中{len(found)}回だけ")
        if len(set(run_results)) > 1:
            reasons_high.append("判定が割れている")
        if any(len({map_result_status(f.get("result_normalized") or "") for f in fs} - {"Exclude"}) > 1 for _, fs in found):
            reasons_high.append("同じ分子で結果が矛盾")
        if USAGE_PATTERN.search(evidence):
            reasons_high.append("検査の使い方の説明かも")
        if input_text and normalize_text(evidence) not in input_text:
            reasons_high.append("根拠が本文に見つからない")
        if result == "altered" and not GENETIC_WORDS.search(evidence):
            reasons_mid.append("遺伝子異常の言葉がない")
        priority = "高" if reasons_high else "中" if reasons_mid else "低"

        gt_item = ground_truth_for(source_name, diagnosis, label_csv).get(key[1])
        hedges = sorted({m.group(0).lower() for m in HEDGE_PATTERN.finditer(evidence)})
        rows.append(
            {
                "優先度": priority,
                "理由": " / ".join(reasons_high + reasons_mid),
                "ファイル": source_name,
                "診断": diagnosis.get("diagnosis") or "",
                "ICD-O": diagnosis.get("icd_o") or "",
                "分子": base.get("molecule_name") or "",
                "検査方法": base.get("method") or "",
                "判定": result,
                "回数": f"{len(found)}/{n}",
                "判定の内訳": ", ".join(f"{r}×{c}" for r, c in Counter(run_results).most_common()),
                "注釈": "、".join(qualifiers),
                "注釈の中身": base.get("qualifier_note") or "",
                "結果": base.get("result") or "",
                "確信度": base.get("confidence", ""),
                "言い切らない表現": ", ".join(hedges),
                "根拠の本文": evidence,
                "新規": "" if gt_item else "○",
                "正解データの結果": gt_item["raw_result"] if gt_item else "",
                "確認結果": "",
            }
        )
    return rows


def latest_run_dir(base: Path) -> Path:
    # Run folders are named <timestamp>_<prompt_version>, so name order is time order
    run_dirs = sorted(d for d in base.glob("*") if d.is_dir())
    if not run_dirs:
        print(f"Error: No run folders found in '{base}'. Pass --output-dir.", file=sys.stderr)
        sys.exit(1)
    return run_dirs[-1]


def main():
    parser = argparse.ArgumentParser(description="Build review.csv for human checking of extraction runs")
    parser.add_argument(
        "--output-dir",
        nargs="+",
        default=None,
        help="Run folder(s) with extracted JSON files; several runs of one prompt are merged "
        "(default: latest run folder in annotator/output)",
    )
    parser.add_argument(
        "--label-csv",
        type=str,
        default=str(SCRIPT_DIR / "label" / "example-label-clean.csv"),
        help="Ground truth CSV, used only to show the existing result next to each finding",
    )
    args = parser.parse_args()

    run_dirs = [Path(d) for d in args.output_dir] if args.output_dir else [latest_run_dir(SCRIPT_DIR / "output")]
    label_csv = Path(args.label_csv)

    rows = []
    for name in sorted({p.name for run in run_dirs for p in run.glob("*.json")}):
        rows += review_rows(name, run_dirs, label_csv)

    df = pd.DataFrame(rows)
    df = df.sort_values(
        ["優先度", "ファイル", "診断"], key=lambda s: s.map(PRIORITY_ORDER) if s.name == "優先度" else s
    )
    # One run: save next to its JSONs. Several runs: save one sheet for all of them.
    if len(run_dirs) == 1:
        review_path = run_dirs[0] / "review.csv"
    else:
        prompt = run_dirs[0].name.split("_", 1)[-1]
        review_path = SCRIPT_DIR / "output" / f"review_{prompt}_{datetime.now():%Y%m%d-%H%M%S}.csv"
    df.to_csv(review_path, index=False, encoding="utf-8-sig")

    print(f"Saved: {review_path} ({len(df)} rows from {len(run_dirs)} run(s))")
    print(df["優先度"].value_counts().reindex(["高", "中", "低"]).fillna(0).astype(int).to_string())
    print(df[df["理由"] != ""]["理由"].str.split(" / ").explode().value_counts().to_string())
    print(f"新規（正解データにない）: {(df['新規'] == '○').sum()}")


if __name__ == "__main__":
    main()
