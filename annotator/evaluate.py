#!/usr/bin/env python3
import argparse
import contextlib
import json
import re
import sys
from pathlib import Path
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
INPUT_DIR = SCRIPT_DIR / "input"


class Tee:
    """Write to several streams at once (console and report file)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, text):
        for s in self.streams:
            s.write(text)

    def flush(self):
        for s in self.streams:
            s.flush()


def normalize_molecule(name: str) -> str:
    """Normalize molecule names by stripping common suffixes and lowercasing."""
    if not name:
        return ""
    name = name.strip().lower()
    # Remove common suffix words
    for suffix in [
        " mutation",
        " mutations",
        " fusion",
        " fusions",
        " expression",
        " translocation",
        " amplification",
    ]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return "".join(c for c in name if c.isalnum())


def load_molecule_aliases(csv_path: Path):
    """Load molecule-aliases.csv.

    Returns (normalized name -> canonical name, canonical name -> all its names).
    """
    if not csv_path.exists():
        print(f"Warning: Molecule aliases '{csv_path}' not found. Names are matched as-is.", file=sys.stderr)
        return {}, {}
    df = pd.read_csv(csv_path, keep_default_na=False, encoding="utf-8-sig")
    to_canonical, names_of = {}, {}
    for canonical, aliases in zip(df["canonical"], df["aliases"]):
        names = [canonical] + [a.strip() for a in aliases.split(";") if a.strip()]
        names_of[canonical] = names
        for n in names:
            to_canonical[normalize_molecule(n)] = canonical
    return to_canonical, names_of


MOLECULE_ALIASES, MOLECULE_NAMES = load_molecule_aliases(SCRIPT_DIR / "molecule-aliases.csv")


def name_variants(name: str) -> list:
    """Split 'ER(Estrogen receptor)' into ['ER(Estrogen receptor)', 'ER', 'Estrogen receptor']."""
    variants = [name]
    outside = re.sub(r"\(.*?\)", " ", name).strip()
    if len(normalize_molecule(outside)) >= 2 and outside != name:
        variants.append(outside)
    variants += [v.strip() for v in re.findall(r"\((.*?)\)", name) if v.strip()]
    return variants


def canonical_molecule(name: str) -> str:
    """Comparison key for a molecule: aliases and 'ER(Estrogen receptor)'-style names collapse to one key."""
    variants = name_variants(name)
    for v in variants:
        canonical = MOLECULE_ALIASES.get(normalize_molecule(v))
        if canonical:
            return normalize_molecule(canonical)
    # Unknown molecule: drop the parenthetical so 'Foo(Bar)' still matches 'Foo'
    outside = re.sub(r"\(.*?\)", " ", name).strip()
    return normalize_molecule(outside if len(normalize_molecule(outside)) >= 2 else name)


def mentioned_in_text(name: str, text_lower: str) -> bool:
    """True if the molecule (or any of its aliases) appears in the input text."""
    names = name_variants(name)
    for v in list(names):
        canonical = MOLECULE_ALIASES.get(normalize_molecule(v))
        if canonical:
            names += MOLECULE_NAMES[canonical]
            break
    for n in names:
        chars = normalize_molecule(n)
        if not chars:
            continue
        # Allow separators between characters so 'TTF1' matches 'TTF-1' and 'napsina' matches 'napsin A'
        pattern = r"(?<![0-9a-z])" + r"[\s\-/.]?".join(map(re.escape, chars)) + r"(?![0-9a-z])"
        if re.search(pattern, text_lower):
            return True
    return False


def source_file_name(extracted_data: dict, json_path: Path) -> str:
    """Name of the input .txt the JSON was extracted from."""
    return (extracted_data.get("ocr_sources") or {}).get("source_name") or f"{json_path.stem}.txt"


def load_diagnosis_mapping(csv_path: Path) -> dict:
    """Load label/diagnosis-mapping.csv.

    One row per diagnosis listed in an input text's ICD-O coding section (WHO 5th ed.),
    with the ground-truth Diagnosis names it corresponds to. Returns
    input file name -> [{"icd_o", "who_diagnosis", "labels"}, ...].
    """
    if not csv_path.exists():
        return {}
    df = pd.read_csv(csv_path, keep_default_na=False)
    mapping = {}
    for _, r in df.iterrows():
        entries = mapping.setdefault(r["input_file"], [])
        if r["who_diagnosis"]:
            entries.append(
                {
                    "icd_o": r["icd_o"],
                    "who_diagnosis": r["who_diagnosis"],
                    "labels": [d.strip() for d in r["label_diagnoses"].split(" ; ") if d.strip()],
                }
            )
    return mapping


DIAGNOSIS_MAPPING = load_diagnosis_mapping(SCRIPT_DIR / "label" / "diagnosis-mapping.csv")


def normalize_name(name: str) -> str:
    """Loose key for comparing diagnosis names (case, spaces and punctuation ignored)."""
    return "".join(c for c in (name or "").lower() if c.isalnum())


def find_input_text(extracted_data: dict, json_path: Path):
    """Return the lowercased input text the JSON was extracted from, or None if not found."""
    name = source_file_name(extracted_data, json_path)
    for p in sorted(INPUT_DIR.rglob("*.txt")):
        if p.name == name:
            return p.read_text(encoding="utf-8").lower()
    return None


def load_result_mapping(csv_path: Path):
    """Load the raw result table (label/result-mapping.csv).

    Returns (raw result -> category, raw result -> qualifier). The qualifier notes that a
    judgment is not 100% (e.g. "ほぼ", "条件付き:細胞"); it is shown in reports but not scored.
    """
    if not csv_path.exists():
        print(f"Warning: Result mapping '{csv_path}' not found. Using fallback rules only.", file=sys.stderr)
        return {}, {}
    df = pd.read_csv(csv_path, keep_default_na=False, encoding="utf-8-sig")
    raws = [str(r).strip() for r in df["result_raw"]]
    qualifiers = df["qualifier"] if "qualifier" in df else [""] * len(df)
    return dict(zip(raws, df["category_proposed"])), {r: q for r, q in zip(raws, qualifiers) if q}


RESULT_MAPPING, RESULT_QUALIFIERS = load_result_mapping(SCRIPT_DIR / "label" / "result-mapping.csv")


def map_result_status(res: str) -> str:
    """Map a raw result string to Positive / Negative / Altered / Equivocal / Exclude."""
    res = (res or "").strip()
    if res in RESULT_MAPPING:
        return RESULT_MAPPING[res]
    if not res:
        return "Exclude"

    # Fallback for values not in the table: judge by the leading word, since
    # qualifiers like "Positive, ..., negative for stromal cells" may follow it
    res_lower = res.lower()
    if res_lower.startswith("positive"):
        return "Positive"
    if res_lower.startswith("negative"):
        return "Negative"
    return "Other"


def qualifier_of(item) -> str:
    """Qualifier of a result (e.g. "ほぼ", "条件付き:細胞"), or "" if the judgment is unqualified."""
    return RESULT_QUALIFIERS.get(item["raw_result"].strip(), "") if item else ""


def mapped_label(item) -> str:
    """Mapped result with its qualifier, e.g. "Negative・ほぼ"."""
    if not item:
        return "N/A"
    q = qualifier_of(item)
    return f"{item['mapped_result']}・{q}" if q else item["mapped_result"]


def results_match(gt_result: str, ex_result: str) -> bool:
    """True if two judgments agree.

    The ground truth often records a genetic alteration as "Positive" (e.g. EGFR mutation:
    Positive), so Altered and Positive are treated as the same finding.
    """
    if gt_result == ex_result:
        return True
    return {gt_result, ex_result} == {"Positive", "Altered"}


def load_extracted_json(json_path: Path):
    """Load and parse the extracted JSON file."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def load_ground_truth_rows(csv_path: Path, diagnoses: list):
    """Ground-truth rows of the given Lung diagnoses (names as in diagnosis-mapping.csv)."""
    df = pd.read_csv(csv_path, keep_default_na=False)
    df = df[(df["Organs"].str.strip() == "Lung") & (df["Diagnosis"].str.strip().isin(diagnoses))]
    return df[df["Molecules"].astype(str).str.strip() != ""]


def index_ground_truth(gt_df: pd.DataFrame) -> dict:
    """Canonical molecule -> ground-truth item. If a molecule appears in several rows, prefer a Positive one."""
    gt_findings = {}
    for _, row in gt_df.iterrows():
        mol_raw = str(row["Molecules"]).strip()
        mol_key = canonical_molecule(mol_raw)
        result_raw = str(row["Results"]).strip()
        result_mapped = map_result_status(result_raw)
        if not mol_key or result_mapped == "Exclude":
            continue
        if mol_key in gt_findings and not (
            result_mapped == "Positive" and gt_findings[mol_key]["mapped_result"] != "Positive"
        ):
            continue
        gt_findings[mol_key] = {
            "raw_molecule": mol_raw,
            "raw_result": result_raw,
            "mapped_result": result_mapped,
            "method": str(row.get("Methods", "")).strip(),
        }
    return gt_findings


def index_extracted(findings: list) -> dict:
    """Canonical molecule -> all extracted items (e.g. Pancytokeratin and AE1/AE3 both map to Keratins)."""
    extracted = {}
    for f in findings:
        mol_raw = f.get("molecule_name")
        if not mol_raw:
            continue
        mol_key = canonical_molecule(mol_raw)
        result_raw = f.get("result_normalized") or f.get("result") or ""
        result_mapped = map_result_status(result_raw)
        if not mol_key or result_mapped == "Exclude":
            continue
        extracted.setdefault(mol_key, []).append(
            {
                "raw_molecule": mol_raw,
                "raw_result": result_raw,
                "mapped_result": result_mapped,
                "method": f.get("method") or "",
            }
        )
    return extracted


def compare_findings(gt_findings: dict, extracted_findings: dict, input_text):
    """Compare per molecule and count TP / FP / FN.

    Only molecules that are both in the ground truth and in the input text are scored:
    - extracted but not in the ground truth -> "Not in GT" (not counted as FP)
    - in the ground truth but never mentioned in the input text -> "Not in text" (not counted as FN)
    """
    counts = {"tp": 0, "fp": 0, "fn": 0, "excluded_not_in_gt": 0, "excluded_not_in_text": 0}
    rows = []
    for mol in sorted(set(gt_findings) | set(extracted_findings)):
        gt_item = gt_findings.get(mol)
        ex_items = extracted_findings.get(mol, [])
        # Of several extracted findings for one molecule, use the one that agrees with the ground truth
        ex_item = None
        if ex_items:
            ex_item = next(
                (e for e in ex_items if gt_item and results_match(gt_item["mapped_result"], e["mapped_result"])),
                ex_items[0],
            )

        if gt_item and ex_item:
            if results_match(gt_item["mapped_result"], ex_item["mapped_result"]):
                status = "TP (Match)"
                counts["tp"] += 1
            else:
                status = "Mismatch"
                counts["fp"] += 1
                counts["fn"] += 1
        elif ex_item:
            status = "Not in GT (excluded)"
            counts["excluded_not_in_gt"] += 1
        elif input_text is not None and not mentioned_in_text(gt_item["raw_molecule"], input_text):
            status = "Not in text (excluded)"
            counts["excluded_not_in_text"] += 1
        else:
            status = "FN (Missing)"
            counts["fn"] += 1

        rows.append(
            {
                "Molecule": gt_item["raw_molecule"] if gt_item else ex_item["raw_molecule"],
                "GT Method": gt_item["method"] if gt_item else "-",
                "GT Result": gt_item["raw_result"] if gt_item else "-",
                "GT Mapped": mapped_label(gt_item),
                "Ex Molecule": ex_item["raw_molecule"] if ex_item else "-",
                "Ex Method": ex_item["method"] if ex_item else "-",
                "Ex Result": ex_item["raw_result"] if ex_item else "-",
                "Ex Mapped": mapped_label(ex_item),
                "Status": status,
            }
        )
    return counts, rows


def prf(tp: int, fp: int, fn: int):
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    return precision, recall, f1


def evaluation_units(extracted_data: dict, json_path: Path, csv_path: Path):
    """Split one extraction into scoring units: (label, ground-truth rows, extracted findings).

    A file in diagnosis-mapping.csv is scored per diagnosis of its ICD-O coding section.
    A diagnosis gets the findings that list it in `applies_to` plus the findings common to
    all diagnoses (empty `applies_to`). Returns None when the file has no ground truth.
    """
    findings = extracted_data.get("findings", [])
    diagnoses = extracted_data.get("diagnoses") or []
    names = [d.get("diagnosis") or "" for d in diagnoses]

    mapped = DIAGNOSIS_MAPPING.get(source_file_name(extracted_data, json_path))
    if mapped is None or not any(e["labels"] for e in mapped):
        return None

    units = []
    model_keys = {normalize_name(n): n for n in names}
    for e in mapped:
        if not e["labels"]:
            continue  # diagnosis only in the textbook: not scored
        # Match the model's diagnosis by name, then by ICD-O code if that is unambiguous
        model_name = model_keys.get(normalize_name(e["who_diagnosis"]))
        if model_name is None:
            same_code = [n for d, n in zip(diagnoses, names) if d.get("icd_o") == e["icd_o"]]
            model_name = same_code[0] if len(same_code) == 1 else None
        if model_name is None:
            print(f"Note: '{e['who_diagnosis']}' was not output by the model; scoring common findings only")
        key = normalize_name(model_name) if model_name else None
        unit_findings = [
            f
            for f in findings
            if not f.get("applies_to")
            or (key and key in {normalize_name(a) for a in f["applies_to"]})
        ]
        units.append((e["who_diagnosis"], load_ground_truth_rows(csv_path, e["labels"]), unit_findings))
    return units


def evaluate_extraction(json_path: Path, csv_path: Path):
    """Evaluate one extracted JSON. Returns a list of per-unit metrics, or None if there is no ground truth."""
    print(f"\nEvaluating extraction: {json_path.name} against labels in {csv_path.name}")
    extracted_data = load_extracted_json(json_path)

    units = evaluation_units(extracted_data, json_path, csv_path)
    if units is None:
        print(f"--- SKIPPED: '{json_path.name}' (no ground truth for this input) ---")
        return None

    input_text = find_input_text(extracted_data, json_path)
    if input_text is None:
        print(
            f"Warning: input text for '{json_path.name}' not found under {INPUT_DIR}. "
            "Ground-truth molecules cannot be checked against the text.",
            file=sys.stderr,
        )

    results = []
    for label, gt_df, findings in units:
        organs = gt_df["Organs"].astype(str).str.strip()
        organs = organs[organs != ""]
        organ = organs.mode().iloc[0] if not organs.empty else "Unknown"

        counts, rows = compare_findings(index_ground_truth(gt_df), index_extracted(findings), input_text)
        precision, recall, f1 = prf(counts["tp"], counts["fp"], counts["fn"])

        print("\n--- EVALUATION REPORT ---")
        print(f"Diagnosis: {label}")
        print(f"TP (True Positive):  {counts['tp']}")
        print(f"FP (False Positive): {counts['fp']}")
        print(f"FN (False Negative): {counts['fn']}")
        print(
            f"Excluded:            {counts['excluded_not_in_gt']} not in GT, "
            f"{counts['excluded_not_in_text']} not in text"
        )
        print(f"Precision:           {precision:.2%}")
        print(f"Recall (抽出率):     {recall:.2%}")
        print(f"F1 Score:            {f1:.2%}")
        print("\nDetailed Findings Comparison:")
        print(
            "| Molecule | GT Method | GT Result (Mapped) | Extracted Molecule | Extracted Method | Extracted Result (Mapped) | Status |"
        )
        print("| --- | --- | --- | --- | --- | --- | --- |")
        for r in rows:
            print(
                f"| {r['Molecule']} | {r['GT Method']} | {r['GT Result']} ({r['GT Mapped']}) | {r['Ex Molecule']} | {r['Ex Method']} | {r['Ex Result']} ({r['Ex Mapped']}) | {r['Status']} |"
            )

        results.append(
            {
                "diagnosis": label,
                "organ": organ,
                "precision": precision,
                "recall": recall,
                "f1": f1,
                **counts,
                "rows": [{"Diagnosis": label, **r} for r in rows],
            }
        )
    return results


def evaluate_run(json_files, label_csv: Path):
    """Evaluate every JSON of one run and print the summary. Returns per-molecule comparison rows."""
    summary_results = []
    evaluated = 0
    skipped = 0

    for json_file in json_files:
        results = evaluate_extraction(json_file, label_csv)
        if results is None:
            skipped += 1
            continue
        evaluated += 1
        for res in results:
            res["file"] = json_file.name
            res["excluded"] = res["excluded_not_in_gt"] + res["excluded_not_in_text"]
            summary_results.append(res)

    if summary_results:
        print("\n=== SUMMARY METRICS OVER ALL EVALUATED DIAGNOSES ===")
        print(f"(Evaluated {len(summary_results)} diagnoses in {evaluated} files, skipped {skipped} files)")
        print("| File | Diagnosis | Precision | Recall (抽出率) | F1 Score | TP | FP | FN | Excluded |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for res in summary_results:
            print(
                f"| {res['file']} | {res['diagnosis']} | {res['precision']:.2%} | {res['recall']:.2%} | {res['f1']:.2%} | {res['tp']} | {res['fp']} | {res['fn']} | {res['excluded']} |"
            )

        total_tp = sum(r["tp"] for r in summary_results)
        total_fp = sum(r["fp"] for r in summary_results)
        total_fn = sum(r["fn"] for r in summary_results)
        micro_p, micro_r, micro_f1 = prf(total_tp, total_fp, total_fn)
        # Macro averages skip diagnoses with nothing scored (no TP/FP/FN), which would otherwise count as 0%
        scored = [r for r in summary_results if r["tp"] + r["fp"] + r["fn"] > 0]
        macro_p = sum(r["precision"] for r in scored) / len(scored) if scored else 0.0
        macro_r = sum(r["recall"] for r in scored) / len(scored) if scored else 0.0
        macro_f1 = sum(r["f1"] for r in scored) / len(scored) if scored else 0.0

        print("\n=== AGGREGATE METRICS ===")
        print(f"Total TP/FP/FN:    {total_tp} / {total_fp} / {total_fn}")
        print(f"Excluded:          {sum(r['excluded'] for r in summary_results)} (not in GT / not in input text)")
        print(f"Micro Precision:   {micro_p:.2%}   (pooled over all findings)")
        print(f"Micro Recall (抽出率): {micro_r:.2%}")
        print(f"Micro F1:          {micro_f1:.2%}")
        print(f"Macro Precision:   {macro_p:.2%}   (averaged over {len(scored)} diagnoses with scored findings)")
        print(f"Macro Recall (抽出率): {macro_r:.2%}")
        print(f"Macro F1:          {macro_f1:.2%}")

        # Per-organ breakdown (micro-averaged over each organ's findings)
        organs = {}
        for r in summary_results:
            organs.setdefault(r["organ"], []).append(r)

        print("\n=== METRICS BY ORGAN ===")
        print("| Organ | Diagnoses | Precision | Recall (抽出率) | F1 Score | TP | FP | FN |")
        print("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for organ in sorted(organs):
            rows = organs[organ]
            o_tp = sum(r["tp"] for r in rows)
            o_fp = sum(r["fp"] for r in rows)
            o_fn = sum(r["fn"] for r in rows)
            o_p, o_r, o_f1 = prf(o_tp, o_fp, o_fn)
            print(f"| {organ} | {len(rows)} | {o_p:.2%} | {o_r:.2%} | {o_f1:.2%} | {o_tp} | {o_fp} | {o_fn} |")

    return [{"File": res["file"], **row} for res in summary_results for row in res["rows"]]


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate Gemini medical text extractions against CSV label ground truths"
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory containing extracted JSON files (default: latest run folder in annotator/output)",
    )
    parser.add_argument(
        "--label-csv",
        type=str,
        default="annotator/label/example-label-clean.csv",
        help="Ground truth CSV for all diagnoses (rows are picked via label/diagnosis-mapping.csv)",
    )
    parser.add_argument(
        "--single-json",
        type=str,
        default=None,
        help="Evaluate a single specific JSON file",
    )

    args = parser.parse_args()

    label_csv = Path(args.label_csv)
    if not label_csv.exists():
        print(f"Error: Label file '{label_csv}' does not exist.", file=sys.stderr)
        sys.exit(1)

    # Case of evaluating a single file
    if args.single_json:
        json_path = Path(args.single_json)
        if not json_path.exists():
            print(f"Error: File '{json_path}' does not exist.", file=sys.stderr)
            sys.exit(1)
        evaluate_extraction(json_path, label_csv)
        return

    # Case of batch evaluation
    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        # Run folders are named <timestamp>_<prompt_version>, so name order is time order
        run_dirs = sorted(d for d in Path("annotator/output").glob("*") if d.is_dir())
        if not run_dirs:
            print(
                "Error: No run folders found in 'annotator/output'. Pass --output-dir.",
                file=sys.stderr,
            )
            sys.exit(1)
        out_dir = run_dirs[-1]
        print(f"Using latest run folder: {out_dir}")

    if not out_dir.exists():
        print(f"Error: Output directory '{out_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)

    json_files = sorted(list(out_dir.glob("*.json")))
    if not json_files:
        print(f"No JSON files found in {out_dir}")
        return

    print(f"Found {len(json_files)} extracted JSON files for evaluation.")
    print(f"Using label CSV: {label_csv}")

    # Save the report and per-molecule details next to the extracted JSONs
    report_path = out_dir / "evaluation_report.md"
    details_path = out_dir / "evaluation_details.csv"
    with open(report_path, "w", encoding="utf-8") as report:
        with contextlib.redirect_stdout(Tee(sys.stdout, report)):
            details = evaluate_run(json_files, label_csv)
    pd.DataFrame(details).to_csv(details_path, index=False, encoding="utf-8-sig")
    print(f"\nSaved report: {report_path}")
    print(f"Saved details: {details_path}")

if __name__ == "__main__":
    main()
