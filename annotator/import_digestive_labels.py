#!/usr/bin/env python3
"""Import the digestive ground truth (WHO Digestive System Tumours, 5th ed.) into label/.

The source is the master data's "digestive_system_molecular_database (消化器1).csv"
(columns: Organs, Major classifications, Diagnosis, ICD-O coding number, Molecules, Results,
Reference number). Its "Major classifications" is the title of an input text, so the mapping
to input files is made here instead of by hand:

- label/digestive-label.csv: the rows in the column names evaluate.py reads
- label/diagnosis-mapping.csv: the rows with label_file = digestive-label.csv are replaced,
  one per (input file, Diagnosis)

Run again whenever the source CSV is updated.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
INPUT_DIR = SCRIPT_DIR / "input"
LABEL_DIR = SCRIPT_DIR / "label"
LABEL_FILE = "digestive-label.csv"
MAPPING_CSV = LABEL_DIR / "diagnosis-mapping.csv"

# Organ in the source CSV -> chapter folder in input/ (titles like "Adenocarcinoma" repeat across chapters)
CHAPTER_DIRS = {
    "Oesophagus": "oesophagus",
    "Stomach": "stomach",
    "Colon and rectum": "colorectum",
    "Liver and intrahepatic bile ducts": "liver",
}


def normalize_name(name: str) -> str:
    return "".join(c for c in (name or "").lower() if c.isalnum())


def input_file_for(organ: str, major: str) -> str:
    """Input file whose title (after the page number) is the major classification, in the organ's chapter."""
    chapter = INPUT_DIR / CHAPTER_DIRS[organ]
    matches = [p.name for p in chapter.glob("*.txt") if normalize_name(p.stem.split("_", 1)[1]) == normalize_name(major)]
    if len(matches) != 1:
        raise SystemExit(f"Error: {len(matches)} input files in {chapter} match '{major}'")
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source_csv", help="digestive_system_molecular_database (消化器1).csv")
    args = parser.parse_args()

    src = pd.read_csv(args.source_csv, keep_default_na=False, dtype=str, encoding="utf-8-sig")
    src = src.apply(lambda col: col.str.strip())
    unknown = set(src["Organs"]) - set(CHAPTER_DIRS)
    if unknown:
        sys.exit(f"Error: organs without an input chapter: {sorted(unknown)}")

    labels = pd.DataFrame(
        {
            "Organs": src["Organs"],
            "Major classifications": src["Major classifications"],
            "Diagnosis": src["Diagnosis"],
            "ICD-O": src["ICD-O coding number"],
            "Methods": "",
            "Molecules": src["Molecules"],
            "Results": src["Results"],
            "References": src["Reference number"],
        }
    )
    labels.to_csv(LABEL_DIR / LABEL_FILE, index=False, encoding="utf-8")

    rows = []
    for (organ, major, diagnosis), group in labels.groupby(
        ["Organs", "Major classifications", "Diagnosis"], sort=False
    ):
        codes = [c for c in group["ICD-O"] if c]
        rows.append(
            {
                "input_file": input_file_for(organ, major),
                "icd_o": codes[0] if codes else "",
                "who_diagnosis": diagnosis,
                "label_diagnoses": diagnosis,
                "status": "exact",
                "note": "消化器1.csv から作成",
                "organ": organ,
                "label_file": LABEL_FILE,
            }
        )

    mapping = pd.read_csv(MAPPING_CSV, keep_default_na=False, dtype=str)
    mapping = mapping[mapping["label_file"] != LABEL_FILE]
    mapping = pd.concat([mapping, pd.DataFrame(rows)], ignore_index=True)
    mapping.to_csv(MAPPING_CSV, index=False, encoding="utf-8")

    print(f"Wrote {len(labels)} rows to {LABEL_DIR / LABEL_FILE}")
    print(f"Wrote {len(rows)} digestive diagnoses ({labels['Organs'].nunique()} organs) to {MAPPING_CSV}")


if __name__ == "__main__":
    main()
