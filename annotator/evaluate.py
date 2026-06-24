#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
import pandas as pd

def normalize_diagnosis(name: str) -> str:
    """Normalize diagnosis names to handle minor typo differences, casing, and common suffixes."""
    if not name:
        return ""
    name = name.strip().lower()
    # Handle the lowercase 'l' typo in "lnvasive" vs "invasive"
    if name.startswith("lnvasive"):
        name = "invasive" + name[8:]
    # Remove common suffixes like "of the lung", "of the colon", etc.
    for suffix in [" of the lung", " of the colon", " of the stomach", " of the urinary tract", " of lung"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return name.strip()

def normalize_molecule(name: str) -> str:
    """Normalize molecule names by stripping common suffixes and lowercasing."""
    if not name:
        return ""
    name = name.strip().lower()
    # Remove common suffix words
    for suffix in [" mutation", " mutations", " fusion", " fusions", " expression", " translocation", " amplification"]:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
    return "".join(c for c in name if c.isalnum())

def map_result_status(res: str) -> str:
    """Map raw result strings to simplified 'Positive' or 'Negative' states."""
    if not res:
        return "Unknown"
    res_lower = res.strip().lower()
    
    # Check for negative indicators
    if "negative" in res_lower or res_lower in ["rare", "described_not_specific"]:
        return "Negative"
        
    # Check for positive indicators
    if "positive" in res_lower or res_lower in ["positive", "focal_positive", "positive_subset", "weak"]:
        return "Positive"
        
    # Default fallback
    return "Other"

def load_extracted_json(json_path: Path):
    """Load and parse the extracted JSON file."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data

def load_ground_truth_csv(csv_path: Path, target_diagnosis: str) -> pd.DataFrame:
    """Load the ground truth CSV and filter rows matching the target diagnosis."""
    # Read the CSV file. 
    # Use keep_default_na=False to avoid interpreting "NA" (as in Napsin A or similar) as NaN
    df = pd.read_csv(csv_path, keep_default_na=False)
    
    # Check if necessary columns exist
    required_cols = ["Molecules", "Results"]
    for col in required_cols:
        if col not in df.columns:
            print(f"Error: Label CSV '{csv_path.name}' is missing required column '{col}'.", file=sys.stderr)
            sys.exit(1)
            
    # Filter by diagnosis if possible
    diag_col = None
    if "Diagnosis" in df.columns:
        diag_col = "Diagnosis"
    elif "Diagnosis (EN)" in df.columns:
        diag_col = "Diagnosis (EN)"
        
    if diag_col and target_diagnosis:
        norm_target = normalize_diagnosis(target_diagnosis)
        # Filter rows
        filtered_df = df[df[diag_col].apply(lambda x: normalize_diagnosis(str(x)) == norm_target)]
        
        # If no rows match, attempt fuzzy matching (substring match)
        if len(filtered_df) == 0:
            filtered_df = df[df[diag_col].apply(lambda x: len(str(x)) >= 10 and (norm_target in normalize_diagnosis(str(x)) or normalize_diagnosis(str(x)) in norm_target))]
            
        if len(filtered_df) > 0:
            print(f"Filtered CSV to {len(filtered_df)} ground truth rows matching diagnosis '{target_diagnosis}' (using column '{diag_col}')")
            df = filtered_df
        else:
            print(f"Warning: No rows in CSV matched diagnosis '{target_diagnosis}'. Evaluating against all CSV rows.", file=sys.stderr)
            
    # Clean and filter out rows with empty Molecule names
    df = df[df["Molecules"].astype(str).str.strip() != ""]
    return df

def evaluate_extraction(json_path: Path, csv_path: Path):
    print(f"\nEvaluating extraction: {json_path.name} against labels in {csv_path.name}")
    
    # 1. Load data
    extracted_data = load_extracted_json(json_path)
    
    # Get diagnosis name
    diagnosis_info = extracted_data.get("diagnoses", {})
    target_diag = diagnosis_info.get("diagnosis") or diagnosis_info.get("diagnosis_normalized")
    
    gt_df = load_ground_truth_csv(csv_path, target_diag)
    
    # 2. Extract findings and index by normalized molecule
    # For Ground Truth
    gt_findings = {}
    for idx, row in gt_df.iterrows():
        mol_raw = str(row["Molecules"]).strip()
        mol_norm = normalize_molecule(mol_raw)
        result_raw = str(row["Results"]).strip()
        result_mapped = map_result_status(result_raw)
        method_raw = str(row.get("Methods", "")).strip()
        
        if mol_norm:
            # If duplicates, keep positive or first
            if mol_norm in gt_findings:
                if result_mapped == "Positive" and gt_findings[mol_norm]["mapped_result"] != "Positive":
                    gt_findings[mol_norm] = {
                        "raw_molecule": mol_raw,
                        "raw_result": result_raw,
                        "mapped_result": result_mapped,
                        "method": method_raw
                    }
            else:
                gt_findings[mol_norm] = {
                    "raw_molecule": mol_raw,
                    "raw_result": result_raw,
                    "mapped_result": result_mapped,
                    "method": method_raw
                }
                
    # For Extracted JSON
    extracted_findings = {}
    for f in extracted_data.get("findings", []):
        mol_raw = f.get("molecule_name")
        if not mol_raw:
            continue
        mol_norm = normalize_molecule(mol_raw)
        result_raw = f.get("result_normalized") or f.get("result") or ""
        result_mapped = map_result_status(result_raw)
        method_raw = f.get("method") or ""
        
        if mol_norm:
            extracted_findings[mol_norm] = {
                "raw_molecule": mol_raw,
                "raw_result": result_raw,
                "mapped_result": result_mapped,
                "method": method_raw
            }
            
    # 3. Compare findings and calculate metrics
    all_molecules = sorted(list(set(gt_findings.keys()) | set(extracted_findings.keys())))
    
    tp_count = 0
    fp_count = 0
    fn_count = 0
    
    comparison_rows = []
    
    for mol in all_molecules:
        gt_item = gt_findings.get(mol)
        ex_item = extracted_findings.get(mol)
        
        mol_display = gt_item["raw_molecule"] if gt_item else ex_item["raw_molecule"]
        
        gt_method = gt_item["method"] if gt_item else "-"
        gt_result = gt_item["raw_result"] if gt_item else "-"
        gt_mapped = gt_item["mapped_result"] if gt_item else "N/A"
        
        ex_method = ex_item["method"] if ex_item else "-"
        ex_result = ex_item["raw_result"] if ex_item else "-"
        ex_mapped = ex_item["mapped_result"] if ex_item else "N/A"
        
        if gt_item and ex_item:
            if gt_mapped == ex_mapped:
                status = "TP (Match)"
                tp_count += 1
            else:
                status = "Mismatch"
                fp_count += 1
                fn_count += 1
        elif ex_item:
            status = "FP (Extra)"
            fp_count += 1
        else:
            status = "FN (Missing)"
            fn_count += 1
            
        comparison_rows.append({
            "Molecule": mol_display,
            "GT Method": gt_method,
            "GT Result": gt_result,
            "GT Mapped": gt_mapped,
            "Ex Method": ex_method,
            "Ex Result": ex_result,
            "Ex Mapped": ex_mapped,
            "Status": status
        })
        
    # Calculate Precision, Recall, F1
    precision = tp_count / (tp_count + fp_count) if (tp_count + fp_count) > 0 else 0.0
    recall = tp_count / (tp_count + fn_count) if (tp_count + fn_count) > 0 else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    
    # 4. Print detailed report
    print("\n--- EVALUATION REPORT ---")
    print(f"Target Diagnosis: {target_diag}")
    print(f"TP (True Positive):  {tp_count}")
    print(f"FP (False Positive): {fp_count}")
    print(f"FN (False Negative): {fn_count}")
    print(f"Precision:           {precision:.2%}")
    print(f"Recall:              {recall:.2%}")
    print(f"F1 Score:            {f1:.2%}")
    print("\nDetailed Findings Comparison:")
    
    # Print Markdown table
    header = "| Molecule | GT Method | GT Result (Mapped) | Extracted Method | Extracted Result (Mapped) | Status |"
    divider = "| --- | --- | --- | --- | --- | --- |"
    print(header)
    print(divider)
    for r in comparison_rows:
        print(f"| {r['Molecule']} | {r['GT Method']} | {r['GT Result']} ({r['GT Mapped']}) | {r['Ex Method']} | {r['Ex Result']} ({r['Ex Mapped']}) | {r['Status']} |")
        
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "tp": tp_count,
        "fp": fp_count,
        "fn": fn_count
    }

def main():
    parser = argparse.ArgumentParser(description="Evaluate Gemini medical text extractions against CSV label ground truths")
    parser.add_argument(
        "--output-dir", 
        type=str, 
        default="annotator/output",
        help="Directory containing extracted JSON files"
    )
    parser.add_argument(
        "--label-dir", 
        type=str, 
        default="annotator/label",
        help="Directory containing CSV ground truth files"
    )
    parser.add_argument(
        "--single-json",
        type=str,
        default=None,
        help="Evaluate a single specific JSON file"
    )
    parser.add_argument(
        "--single-label",
        type=str,
        default=None,
        help="Use a single specific CSV label file"
    )
    
    args = parser.parse_args()
    
    # Case of evaluating a single file pair
    if args.single_json:
        json_path = Path(args.single_json)
        if not json_path.exists():
            print(f"Error: File '{json_path}' does not exist.", file=sys.stderr)
            sys.exit(1)
            
        if args.single_label:
            csv_path = Path(args.single_label)
        else:
            # Try to guess label path
            csv_path = Path(args.label_dir) / f"{json_path.stem}-label.csv"
            
        if not csv_path.exists():
            print(f"Error: Label file '{csv_path}' does not exist.", file=sys.stderr)
            sys.exit(1)
            
        evaluate_extraction(json_path, csv_path)
        return
        
    # Case of batch evaluation
    out_dir = Path(args.output_dir)
    label_dir = Path(args.label_dir)
    
    if not out_dir.exists():
        print(f"Error: Output directory '{out_dir}' does not exist.", file=sys.stderr)
        sys.exit(1)
        
    json_files = sorted(list(out_dir.glob("*.json")))
    if not json_files:
        print(f"No JSON files found in {out_dir}")
        return
        
    print(f"Found {len(json_files)} extracted JSON files for evaluation.")
    
    summary_results = []
    
    for json_file in json_files:
        # Search for corresponding label CSV file
        csv_file = label_dir / f"{json_file.stem}-label.csv"
        if not csv_file.exists():
            # Try plain stem.csv
            csv_file = label_dir / f"{json_file.stem}.csv"
            
        if csv_file.exists():
            metrics = evaluate_extraction(json_file, csv_file)
            summary_results.append({
                "file": json_file.name,
                "precision": metrics["precision"],
                "recall": metrics["recall"],
                "f1": metrics["f1"],
                "tp": metrics["tp"],
                "fp": metrics["fp"],
                "fn": metrics["fn"]
            })
        else:
            print(f"Skipping evaluation for '{json_file.name}': No matching CSV file found in '{label_dir}'")
            
    if summary_results:
        print("\n=== SUMMARY METRICS OVER ALL EVALUATED FILES ===")
        print("| File | Precision | Recall | F1 Score | TP | FP | FN |")
        print("| --- | --- | --- | --- | --- | --- | --- |")
        for res in summary_results:
            print(f"| {res['file']} | {res['precision']:.2%} | {res['recall']:.2%} | {res['f1']:.2%} | {res['tp']} | {res['fp']} | {res['fn']} |")

if __name__ == "__main__":
    main()
