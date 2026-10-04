#!/usr/bin/env python3
"""Build a review sheet (review.csv) for human checking of one extraction run.

One row per (diagnosis, finding). Rows are sorted by review priority so that findings
likely to be wrong come first. Priority uses only the extraction and the input text,
so it works for chapters without ground truth too.

  高: ambiguous result (equivocal / rare / positive_subset), conflicting results for the
      same molecule within a diagnosis, or evidence_text not found in the input text
  中: evidence_text contains hedging words (may, rarely, reported, ...)
  低: none of the above
"""
import argparse
import re
import sys
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
AMBIGUOUS_RESULTS = {"equivocal", "rare", "positive_subset"}
HEDGE_PATTERN = re.compile(
    r"\b(may|might|can|could|rarely|rare|occasional(?:ly)?|reported|some cases|subset"
    r"|variabl\w*|inconsistent|weak(?:ly)?|usually|often|up to)\b",
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


def review_rows(json_path: Path, label_csv: Path) -> list:
    data = load_extracted_json(json_path)
    source_name = source_file_name(data, json_path)
    input_text = normalize_text(find_input_text(data, json_path) or "")
    findings = data.get("findings", [])

    rows = []
    for diagnosis in data.get("diagnoses") or []:
        name = diagnosis.get("diagnosis") or ""
        key = normalize_name(name)
        # Findings for this diagnosis: listed in applies_to, or common to all (empty applies_to)
        own = [
            f
            for f in findings
            if not f.get("applies_to") or key in {normalize_name(a) for a in f["applies_to"]}
        ]
        categories = {}
        for f in own:
            categories.setdefault(canonical_molecule(f.get("molecule_name") or ""), set()).add(
                map_result_status(f.get("result_normalized") or "")
            )
        gt = ground_truth_for(source_name, diagnosis, label_csv)

        for f in own:
            evidence = f.get("evidence_text") or ""
            mol_key = canonical_molecule(f.get("molecule_name") or "")
            reasons_high, reasons_mid = [], []
            if f.get("result_normalized") in AMBIGUOUS_RESULTS:
                reasons_high.append(f"あいまいな判定({f['result_normalized']})")
            if len(categories[mol_key] - {"Exclude"}) > 1:
                reasons_high.append("同じ分子で結果が矛盾")
            if input_text and normalize_text(evidence) not in input_text:
                reasons_high.append("根拠が本文に見つからない")
            hedges = sorted({m.group(0).lower() for m in HEDGE_PATTERN.finditer(evidence)})
            if hedges:
                reasons_mid.append(f"言い切らない表現({', '.join(hedges)})")
            priority = "高" if reasons_high else "中" if reasons_mid else "低"

            gt_item = gt.get(mol_key)
            rows.append(
                {
                    "優先度": priority,
                    "理由": " / ".join(reasons_high + reasons_mid),
                    "ファイル": source_name,
                    "診断": name,
                    "ICD-O": diagnosis.get("icd_o") or "",
                    "分子": f.get("molecule_name") or "",
                    "検査方法": f.get("method") or "",
                    "結果": f.get("result") or "",
                    "判定": f.get("result_normalized") or "",
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
    parser = argparse.ArgumentParser(description="Build review.csv for human checking of an extraction run")
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Run folder with extracted JSON files (default: latest run folder in annotator/output)",
    )
    parser.add_argument(
        "--label-csv",
        type=str,
        default=str(SCRIPT_DIR / "label" / "example-label-clean.csv"),
        help="Ground truth CSV, used only to show the existing result next to each finding",
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir) if args.output_dir else latest_run_dir(SCRIPT_DIR / "output")
    label_csv = Path(args.label_csv)

    rows = []
    for json_path in sorted(out_dir.glob("*.json")):
        rows += review_rows(json_path, label_csv)

    df = pd.DataFrame(rows)
    df = df.sort_values(
        ["優先度", "ファイル", "診断"], key=lambda s: s.map(PRIORITY_ORDER) if s.name == "優先度" else s
    )
    review_path = out_dir / "review.csv"
    df.to_csv(review_path, index=False, encoding="utf-8-sig")

    print(f"Saved: {review_path} ({len(df)} rows)")
    print(df["優先度"].value_counts().reindex(["高", "中", "低"]).to_string())
    print(f"新規（正解データにない）: {(df['新規'] == '○').sum()}")


if __name__ == "__main__":
    main()
