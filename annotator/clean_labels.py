#!/usr/bin/env python3
"""Fix obvious typos and formatting noise in the hand-made ground truth CSV.

The raw file (label/example-label.csv) is kept untouched. The cleaned copy is
written to label/example-label-clean.csv, and every changed cell is listed in
label/example-label-changes.csv so the corrections can be reviewed.
"""
import re
from pathlib import Path

import pandas as pd

script_dir = Path(__file__).resolve().parent
RAW_CSV = script_dir / "label" / "example-label.csv"
CLEAN_CSV = script_dir / "label" / "example-label-clean.csv"
CHANGES_CSV = script_dir / "label" / "example-label-changes.csv"

# Columns used for evaluation / DB loading. References, Books, URL, Photos are
# left as-is (URLs are built from the reference text).
TARGET_COLUMNS = [
    "Organs",
    "Primary/Metastasis",
    "Origin",
    "Malignancy",
    "Major classifications",
    "Diagnosis",
    "Diagnosis (EN)",
    "Methods",
    "Molecules",
    "Results",
]

# Obvious misspellings only (whole word, case-insensitive). British/American
# variants such as tumor/tumour are intentionally not unified here.
TYPOS = {
    "atypial": "atypical",
    "carcinioma": "carcinoma",
    "carcinma": "carcinoma",
    "carcionoma": "carcinoma",
    "clrear": "clear",
    "cyroplasm": "cytoplasm",
    "defferentiated": "differentiated",
    "diffentiated": "differentiated",
    "epitherial": "epithelial",
    "epithlioid": "epithelioid",
    "extrandal": "extranodal",
    "frequentry": "frequently",
    "grannulomatosis": "granulomatosis",
    "histocytoosis": "histiocytosis",
    "intraepitherial": "intraepithelial",
    "leqidic": "lepidic",
    "lnvasive": "Invasive",  # capital I mistyped as lowercase l
    "loe-grade": "low-grade",
    "lowe": "low",
    "microystic": "microcystic",
    "mutaiton": "mutation",
    "mycoepithelial": "myoepithelial",
    "neuroendcrine": "neuroendocrine",
    "neuroendocline": "neuroendocrine",
    "occasionaly": "occasionally",
    "pancytokerain": "pancytokeratin",
    "papilary": "papillary",
    "pleomorohic": "pleomorphic",
    "poasitive": "positive",
    "progesteron": "progesterone",
    "rarranged": "rearranged",
    "tertoma": "teratoma",
    "trysomy": "trisomy",
}
TYPO_PATTERN = re.compile(r"(?<![A-Za-z])(" + "|".join(map(re.escape, TYPOS)) + r")(?![A-Za-z])", re.IGNORECASE)

FULLWIDTH = str.maketrans({"（": " (", "）": ")", "，": ", ", "、": ", ", "：": ":", "　": " "})

# Whole-value unification for categorical columns
CATEGORY_VALUES = {
    "Primary/Metastasis": {"Primary tumor": "Primary"},
    "Malignancy": {"malignant": "Malignant"},
    "Methods": {"Chromosomal test": "Chromosome test"},
}


def fix_typo(match: re.Match) -> str:
    word = match.group(0)
    fixed = TYPOS[word.lower()]
    if fixed[0].isupper():
        return fixed
    if word.isupper():
        return fixed.upper()
    if word[0].isupper():
        return fixed[0].upper() + fixed[1:]
    return fixed


def clean_value(value: str, column: str = "") -> str:
    value = value.translate(FULLWIDTH)
    value = re.sub(r"\s+", " ", value).strip()
    value = TYPO_PATTERN.sub(fix_typo, value)
    return CATEGORY_VALUES.get(column, {}).get(value, value)


def main():
    df = pd.read_csv(RAW_CSV, keep_default_na=False, dtype=str)
    clean = df.copy()
    changes = []
    for col in TARGET_COLUMNS:
        clean[col] = df[col].apply(lambda v: clean_value(v, col))
        for idx in df.index[df[col] != clean[col]]:
            changes.append({
                "row": idx + 2,  # 1-based line number in the CSV, after the header
                "column": col,
                "before": df.at[idx, col],
                "after": clean.at[idx, col],
            })

    clean.to_csv(CLEAN_CSV, index=False)
    pd.DataFrame(changes, columns=["row", "column", "before", "after"]).to_csv(
        CHANGES_CSV, index=False, encoding="utf-8-sig"
    )

    print(f"Wrote {CLEAN_CSV} ({len(changes)} cells changed)")
    print(f"Change log: {CHANGES_CSV}")
    if changes:
        print(pd.DataFrame(changes)["column"].value_counts().to_string())


if __name__ == "__main__":
    main()
