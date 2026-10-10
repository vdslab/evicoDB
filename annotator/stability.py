#!/usr/bin/env python3
"""Compare several extraction runs of the same prompt to see how much the results vary.

Writes stability_<prompt>_<timestamp>.md (summary) and .csv (per molecule) to annotator/output:
  - metrics per run (TP / FP / FN, precision, recall, F1, qualifier agreement), with mean and spread
  - scored molecules whose status changes between runs (TP in some runs, Mismatch / FN in others)
  - per extracted molecule: in how many runs it appears, and how often the runs agree on
    result_normalized and on the qualifiers
"""
import argparse
import contextlib
import io
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd

from evaluate import canonical_molecule, evaluate_run, prf, source_file_name

SCRIPT_DIR = Path(__file__).resolve().parent


def qualifier_set(mapped: str) -> set:
    """Qualifiers from a "Positive・ほぼ、条件付き:細胞" style label."""
    return set(mapped.split("・", 1)[1].split("、")) if "・" in mapped else set()


def run_metrics(rows: list) -> dict:
    status = Counter(r["Status"] for r in rows)
    mismatch = status["Mismatch"] + status["Mismatch (qualified)"]
    tp, fp, fn = status["TP (Match)"], mismatch, mismatch + status["FN (Missing)"]
    p, r, f1 = prf(tp, fp, fn)
    scored = [x for x in rows if x["GT Mapped"] != "N/A" and x["Ex Mapped"] != "N/A"]
    gt_q = [x for x in scored if qualifier_set(x["GT Mapped"])]
    q_match = sum(1 for x in gt_q if qualifier_set(x["GT Mapped"]) & qualifier_set(x["Ex Mapped"]))
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1, "q_match": q_match, "gt_q": len(gt_q)}


def run_cost(run: Path) -> dict:
    """Total tokens from each JSON's token_usage, and wall-clock minutes of the run.

    Run time is measured from the timestamp in the folder name to the last JSON written, so it
    includes waiting when several runs share the API at the same time.
    """
    jsons = sorted(run.glob("*.json"))
    tokens = Counter()
    for path in jsons:
        usage = json.loads(path.read_text(encoding="utf-8")).get("extraction_runs", {}).get("token_usage", {})
        tokens.update({k: usage.get(k, 0) for k in ["prompt_tokens", "output_tokens", "thinking_tokens", "total_tokens"]})
    try:
        start = datetime.strptime(run.name.split("_", 1)[0], "%Y%m%d-%H%M%S")
        minutes = (datetime.fromtimestamp(max(p.stat().st_mtime for p in jsons)) - start).total_seconds() / 60
    except (ValueError, OSError):
        minutes = float("nan")
    return {"minutes": minutes, **tokens}


def finding_table(run_dirs: list) -> pd.DataFrame:
    """Per (file, molecule): presence and agreement of result_normalized / qualifiers across runs."""
    seen = defaultdict(dict)  # (file, molecule) -> run -> (result, qualifiers)
    names = {}
    for run in run_dirs:
        for path in sorted(run.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            source = source_file_name(data, path)
            for f in data.get("findings", []):
                key = (source, canonical_molecule(f.get("molecule_name") or ""))
                if not key[1]:
                    continue
                names.setdefault(key, f.get("molecule_name"))
                # Several findings for one molecule (e.g. IHC and genetic test) are kept together
                prev = seen[key].get(run.name, ((), ()))
                seen[key][run.name] = (
                    tuple(sorted(set(prev[0]) | {f.get("result_normalized") or ""})),
                    tuple(sorted(set(prev[1]) | set(f.get("qualifiers") or []))),
                )
    n = len(run_dirs)
    rows = []
    for (source, mol), by_run in seen.items():
        results = Counter(v[0] for v in by_run.values())
        qualifiers = Counter(v[1] for v in by_run.values())
        top_result, top_result_n = results.most_common(1)[0]
        top_q, top_q_n = qualifiers.most_common(1)[0]
        rows.append(
            {
                "file": source,
                "molecule": names[(source, mol)],
                "runs_present": len(by_run),
                "presence": len(by_run) / n,
                "result_agreement": top_result_n / len(by_run),
                "results": "; ".join(f"{'/'.join(k)}×{c}" for k, c in results.most_common()),
                "top_result": "/".join(top_result),
                "qualifier_agreement": top_q_n / len(by_run),
                "qualifiers": "; ".join(f"{'、'.join(k) or '(なし)'}×{c}" for k, c in qualifiers.most_common()),
            }
        )
    return pd.DataFrame(rows).sort_values(["file", "molecule"])


def main():
    parser = argparse.ArgumentParser(description="Measure how much repeated extraction runs vary")
    parser.add_argument("run_dirs", nargs="+", help="Run folders of the same prompt (annotator/output/<timestamp>_<prompt>)")
    parser.add_argument(
        "--label-csv",
        default=str(SCRIPT_DIR / "label" / "example-label-clean.csv"),
        help="Ground truth CSV (same as evaluate.py)",
    )
    parser.add_argument("--out-dir", default=str(SCRIPT_DIR / "output"), help="Where to save the report (default: annotator/output)")
    args = parser.parse_args()

    run_dirs = [Path(d) for d in args.run_dirs]
    prompt = run_dirs[0].name.split("_", 1)[-1]
    label_csv = Path(args.label_csv)

    # Score every run with evaluate.py, without its console report
    metrics, statuses = [], defaultdict(dict)
    for run in run_dirs:
        with contextlib.redirect_stdout(io.StringIO()):
            rows = evaluate_run(sorted(run.glob("*.json")), label_csv)
        metrics.append({"run": run.name, **run_metrics(rows), **run_cost(run)})
        for r in rows:
            if "excluded" not in r["Status"]:
                statuses[(r["File"], r["Diagnosis"], r["Molecule"])][run.name] = r["Status"]

    findings = finding_table(run_dirs)
    stamp = f"{datetime.now():%Y%m%d-%H%M%S}"
    out_base = Path(args.out_dir) / f"stability_{prompt}_{stamp}"
    findings.to_csv(out_base.with_suffix(".csv"), index=False, encoding="utf-8-sig")

    lines = [f"# Stability of {prompt} over {len(run_dirs)} runs", ""]
    lines += ["## Metrics per run", "", "| Run | TP | FP | FN | Precision | Recall | F1 | Qualifier agreement | Minutes | Total tokens |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for m in metrics:
        lines.append(
            f"| {m['run']} | {m['tp']} | {m['fp']} | {m['fn']} | {m['precision']:.2%} | {m['recall']:.2%} | {m['f1']:.2%} | {m['q_match']} / {m['gt_q']} | {m['minutes']:.1f} | {m['total_tokens']:,} |"
        )
    lines.append("")
    for key in ["precision", "recall", "f1"]:
        vals = [m[key] for m in metrics]
        sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
        lines.append(f"- {key}: mean {statistics.mean(vals):.2%}, sd {sd:.2%}, min {min(vals):.2%}, max {max(vals):.2%}")
    lines.append(f"- minutes per run (wall clock): mean {statistics.mean(m['minutes'] for m in metrics):.1f}")
    for key in ["prompt_tokens", "output_tokens", "thinking_tokens", "total_tokens"]:
        vals = [m[key] for m in metrics]
        lines.append(f"- {key} per run: mean {statistics.mean(vals):,.0f}, total {sum(vals):,}")

    n = len(run_dirs)
    unstable = {k: v for k, v in statuses.items() if len(set(v.values())) > 1 or len(v) < n}
    lines += ["", f"## Scored molecules whose status changes between runs ({len(unstable)} / {len(statuses)})", ""]
    lines += ["| File | Diagnosis | Molecule | Statuses |", "| --- | --- | --- | --- |"]
    for (file, diag, mol), v in sorted(unstable.items()):
        counts = Counter(v.values())
        if len(v) < n:
            counts["(not scored)"] = n - len(v)
        lines.append(f"| {file} | {diag} | {mol} | {', '.join(f'{s}×{c}' for s, c in counts.most_common())} |")

    always = findings[findings["runs_present"] == n]
    lines += [
        "",
        "## Extracted molecules",
        "",
        f"- molecules seen in any run: {len(findings)}",
        f"- present in all {n} runs: {len(always)} ({len(always) / len(findings):.0%})",
        f"- of those, same result_normalized in every run: {(always['result_agreement'] == 1).sum()} "
        f"({(always['result_agreement'] == 1).mean():.0%})",
        f"- of those, same qualifiers in every run: {(always['qualifier_agreement'] == 1).sum()} "
        f"({(always['qualifier_agreement'] == 1).mean():.0%})",
        "",
        f"Per-molecule details: {out_base.with_suffix('.csv').name}",
    ]
    out_base.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nSaved: {out_base.with_suffix('.md')}")


if __name__ == "__main__":
    main()
