#!/usr/bin/env python3
"""Merge several extraction runs of the same prompt into one run by majority vote.

The merged run is saved like any other run (annotator/output/<timestamp>_<prompt>-majority<N>/),
so evaluate.py and review.py work on it unchanged. Per input file:
  - diagnoses: the list most runs agree on (by diagnosis names)
  - findings: a finding is identified by (canonical molecule, method, applies_to) and kept if it
    appears in more than half of the runs; result_normalized is the most common value among those
    runs, and a qualifier is kept if more than half of those runs gave it
"""
import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from evaluate import canonical_molecule, normalize_name

SCRIPT_DIR = Path(__file__).resolve().parent


def finding_key(f: dict, all_diagnoses: set) -> tuple:
    applies_to = frozenset(normalize_name(a) for a in f.get("applies_to") or [])
    # Listing every diagnosis of the file means the same as an empty applies_to (common to all)
    if all_diagnoses and applies_to >= all_diagnoses:
        applies_to = frozenset()
    return (
        canonical_molecule(f.get("molecule_name") or "", f.get("method")),
        (f.get("method") or "").strip().lower(),
        applies_to,
    )


def merge(datas: list) -> dict:
    n = len(datas)
    # Diagnoses: the most common list of names; take that list from the first run that has it
    names = Counter(tuple(d.get("diagnosis") for d in data.get("diagnoses") or []) for data in datas)
    top_names = names.most_common(1)[0][0]
    diagnoses = next(data["diagnoses"] for data in datas if tuple(d.get("diagnosis") for d in data.get("diagnoses") or []) == top_names) if top_names else []

    # Findings: group by key, one entry per run (the first finding with that key in the run)
    groups = defaultdict(dict)
    order = []
    for i, data in enumerate(datas):
        all_diagnoses = {normalize_name(d.get("diagnosis")) for d in data.get("diagnoses") or []}
        for f in data.get("findings", []):
            key = finding_key(f, all_diagnoses)
            if not key[0] or i in groups[key]:
                continue
            if key not in order:
                order.append(key)
            groups[key][i] = f

    findings = []
    for key in order:
        by_run = groups[key]
        if len(by_run) * 2 <= n:
            continue
        result = Counter(f.get("result_normalized") for f in by_run.values()).most_common(1)[0][0]
        qualifier_votes = Counter(q for f in by_run.values() for q in set(f.get("qualifiers") or []))
        qualifiers = [q for q, c in qualifier_votes.most_common() if c * 2 > len(by_run)]
        base = next(f for f in by_run.values() if f.get("result_normalized") == result)
        findings.append(
            {**base, "result_normalized": result, "qualifiers": qualifiers, "applies_to": base.get("applies_to") if key[2] else [], "votes": len(by_run)}
        )
    return {"diagnoses": diagnoses, "findings": findings}


def main():
    parser = argparse.ArgumentParser(description="Merge extraction runs of one prompt by majority vote")
    parser.add_argument("run_dirs", nargs="+", help="Run folders of the same prompt (annotator/output/<timestamp>_<prompt>)")
    parser.add_argument("--out-dir", default=str(SCRIPT_DIR / "output"), help="Base directory for the merged run")
    args = parser.parse_args()

    run_dirs = [Path(d) for d in args.run_dirs]
    prompt = run_dirs[0].name.split("_", 1)[-1]
    out = Path(args.out_dir) / f"{datetime.now():%Y%m%d-%H%M%S}_{prompt}-majority{len(run_dirs)}"
    out.mkdir(parents=True)

    names = sorted({p.name for run in run_dirs for p in run.glob("*.json")})
    for name in names:
        datas = [json.loads((run / name).read_text(encoding="utf-8")) for run in run_dirs if (run / name).exists()]
        merged = merge(datas)
        meta = datas[0]
        # The merged run costs what all its runs cost together
        token_usage = Counter()
        for data in datas:
            token_usage.update(data.get("extraction_runs", {}).get("token_usage", {}))
        result = {
            "ocr_sources": meta.get("ocr_sources"),
            "extraction_runs": {
                **meta.get("extraction_runs", {}),
                "prompt_version": f"{prompt}-majority{len(run_dirs)}",
                "token_usage": dict(token_usage),
                "merged_from": [run.name for run in run_dirs],
            },
            **merged,
        }
        (out / name).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Merged {len(run_dirs)} runs into {out} ({len(names)} files)")


if __name__ == "__main__":
    main()
