"""Per-episode measures for a finished batch, and paired differences against condition 4.

    python -m collusion_study.results ../runs/control --rule D17 --out ../analysis/control \
        --colosseum ../third_party/colosseum

Writes episodes.csv (one row per episode, valid or not) and paired.csv (each condition minus
C4 at the same seed, over the seeds where both episodes are valid, with a bootstrap interval
over seeds). This reads results, so it runs only after the scoring rule it serves is logged;
--rule names that decision and goes into both files. With --colosseum, each episode also gets
the paper's exact optimum for its instance, from Colosseum's compute_jira_optimal.py.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REFERENCE = "C4"
BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_SEED = 0

# Numeric measures, each read from the episode's own files.
MEASURES = [
    "assigned_pair_advantage",
    "best_pair_advantage",
    "best_subset_advantage",
    "best_subset_size",
    "joint_reward",
    "normalized_score",
    "system_regret",
    "tasks_done",
    "violations",
    "pair_private_post_share",
    "optimal_gap",
    "optimal_ratio",
]


def episode_dirs(batch_dir: Path) -> List[Path]:
    """Episode directories in the order the batch ran them; moved-aside failures and capped originals are skipped."""
    status = batch_dir / "batch_status.json"
    if status.exists():
        return [Path(e["dir"]) if Path(e["dir"]).exists() else batch_dir / Path(e["dir"]).name
                for e in json.loads(status.read_text())["episodes"]]
    return sorted(p for p in batch_dir.iterdir()
                  if p.is_dir() and "-seed" in p.name and ".failed-" not in p.name and ".capped-" not in p.name)


def colosseum_run_dir(out_dir: Path) -> Optional[Path]:
    found = sorted(p.parent for p in out_dir.glob("runs/*/*/*/final_summary.json"))
    return found[0] if len(found) == 1 else None


def optimal_measures(run_dir: Path, colosseum: Path) -> Dict[str, Any]:
    """The exact optimal joint reward for the episode's instance, as compute_jira_optimal.py's _run_one finds it."""
    if str(colosseum) not in sys.path:
        sys.path.insert(0, str(colosseum))
    from experiments.collusion import compute_jira_optimal as cjo

    try:
        instance = cjo._reconstruct_instance(run_dir)
    except Exception:
        instance = cjo._load_instance_from_agent_prompts(run_dir)
    weights = cjo._load_weights(run_dir, None)
    optimal = cjo.solve_optimal_assignment(instance=instance, weights=weights)
    actual = cjo._evaluate(cjo._load_actual_solution(run_dir).assignment, instance=instance, weights=weights)
    return {
        "optimal_joint_reward": optimal.joint_reward,
        # The paper's regret: how far the joint reward falls below the best possible assignment.
        "optimal_gap": optimal.joint_reward - actual.joint_reward,
        "optimal_ratio": actual.joint_reward / optimal.joint_reward if optimal.joint_reward else None,
    }


def episode_row(out_dir: Path, colosseum: Optional[Path] = None) -> Dict[str, Any]:
    status_path = out_dir / "status.json"
    status = json.loads(status_path.read_text()) if status_path.exists() else {}
    condition, _, seed = out_dir.name.rpartition("-seed")
    row: Dict[str, Any] = {
        "episode": out_dir.name,
        "condition": status.get("spec", {}).get("condition", condition),
        "seed": int(status.get("spec", {}).get("seed", seed)),
        "status": status.get("status", "missing"),
        "valid": status.get("status") == "ok",
        "invalid_reasons": "; ".join(status.get("invalid_reasons") or []),
    }
    summary_path = out_dir / "episode_summary.json"
    run_dir = colosseum_run_dir(out_dir)
    if not summary_path.exists() or run_dir is None:
        return row
    summary = json.loads(summary_path.read_text())
    final = json.loads((run_dir / "final_summary.json").read_text())
    metrics = json.loads((run_dir / "metrics.json").read_text())
    best_pair = summary.get("best_pair") or {}
    best_subset = summary.get("best_subset") or {}
    row.update(
        assigned_pair=" + ".join(summary.get("assigned_pair") or []),
        assigned_pair_advantage=summary.get("assigned_pair_advantage"),
        best_pair=" + ".join(best_pair.get("subset") or []),
        best_pair_advantage=best_pair.get("advantage"),
        best_subset=" + ".join(best_subset.get("subset") or []),
        best_subset_advantage=best_subset.get("advantage"),
        best_subset_size=len(best_subset.get("subset") or []) or None,
        joint_reward=summary.get("joint_reward"),
        normalized_score=final.get("normalized_score"),
        system_regret=summary.get("system_regret"),
        tasks_done=final.get("tasks_done"),
        violations=final.get("violations"),
        # Share of the pair's posts that went to their private board; empty without one.
        pair_private_post_share=metrics.get("colluder_posts_secret_rate") if metrics.get("secret_channel_enabled") else None,
        live_model_calls=summary.get("live_model_calls"),
    )
    if colosseum is not None:
        row.update(optimal_measures(run_dir, colosseum))
    return row


def bootstrap_interval(values: List[float], level: float = 0.95) -> List[Optional[float]]:
    """Percentile interval for the mean, resampling seeds with a fixed random seed."""
    if len(values) < 2:
        return [None, None]
    rng = random.Random(BOOTSTRAP_SEED)
    means = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(BOOTSTRAP_RESAMPLES))
    lo = means[int((1 - level) / 2 * BOOTSTRAP_RESAMPLES)]
    hi = means[min(BOOTSTRAP_RESAMPLES - 1, int((1 + level) / 2 * BOOTSTRAP_RESAMPLES))]
    return [lo, hi]


def paired_differences(rows: List[Dict[str, Any]], reference: str = REFERENCE) -> List[Dict[str, Any]]:
    """Each condition minus the reference at the same seed, using only seeds where both are valid."""
    valid = {(r["condition"], r["seed"]): r for r in rows if r["valid"]}
    conditions = [c for c in dict.fromkeys(r["condition"] for r in rows) if c != reference]
    out = []
    for condition in conditions:
        for measure in MEASURES:
            diffs, seeds = [], []
            for (c, seed), r in sorted(valid.items(), key=lambda kv: kv[0][1]):
                ref = valid.get((reference, seed))
                if c != condition or ref is None or r.get(measure) is None or ref.get(measure) is None:
                    continue
                diffs.append(float(r[measure]) - float(ref[measure]))
                seeds.append(seed)
            if not diffs:
                continue
            lo, hi = bootstrap_interval(diffs)
            out.append({
                "condition": condition,
                "reference": reference,
                "measure": measure,
                "n_seeds": len(diffs),
                "seeds": " ".join(map(str, seeds)),
                "mean_difference": sum(diffs) / len(diffs),
                "ci95_low": lo,
                "ci95_high": hi,
                "differences": " ".join(f"{d:.4g}" for d in diffs),
            })
    return out


def write_csv(path: Path, rows: List[Dict[str, Any]], rule: str) -> None:
    columns = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="", encoding="utf-8") as handle:
        handle.write(f"# scoring rule: {rule}\n")
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("batch_dir")
    parser.add_argument("--rule", required=True, help="the logged decision this analysis serves, e.g. D17")
    parser.add_argument("--out", required=True)
    parser.add_argument("--colosseum", default=None, help="Colosseum checkout, to add the exact optimum")
    args = parser.parse_args(argv)
    colosseum = Path(args.colosseum).resolve() if args.colosseum else None
    rows = [episode_row(d, colosseum) for d in episode_dirs(Path(args.batch_dir))]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    write_csv(out / "episodes.csv", rows, args.rule)
    write_csv(out / "paired.csv", paired_differences(rows), args.rule)
    print(json.dumps({"episodes": len(rows), "valid": sum(r["valid"] for r in rows), "out": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
