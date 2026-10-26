"""Evaluation coverage and dead time per Judging arm (asked 25 Oct 23:43 UTC). Read-only; outside the package so CODE_SHA256 is unchanged.
Per valid episode (validity from the scoring CSVs, as in the main analysis), all six workers:
coverage = outputs scored / (6 x turns_started), the outputs the six workers would hand in at one per turn if never paused or removed;
handed_in = outputs handed in / (6 x turns_started); scored_of_handed = outputs scored / outputs handed in;
dead_share = share of turns_started in which no worker handed in an output (every team paused or emptied).
The budget can run out before the last turn's outputs are scored, so a shortfall of up to one turn's outputs is structural in every arm;
coverage_excl_last repeats coverage with the last turn left out of both counts."""
import json, sys, statistics as st
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
from collusion_study.analysis import read_csv, index, truthy  # noqa: E402

RUNS, AN = ROOT / "runs", ROOT / "analysis"
rows = {}
for d in ("judges-coding-v2", "judges-coding-v2-followup", "judges-coding-v2-neutral"):
    rows.update(index(read_csv(AN / d / "judges-coding.csv")))
ARMS = [("C4", "judges-main", "C4"), ("channel_only", "judges-main", "channel_only"), ("separate_budget", "judges-separate-budget", "separate_budget"),
        ("instructed", "judges-instructed", "instructed"), ("all_good", "judges-all-good", "all_good"),
        ("neutral_pause", "judges-neutral-pause", "neutral_pause"), ("no_scarcity", "judges-no-scarcity", "no_scarcity")]

def episode(batch, arm, seed):
    ev = [json.loads(l) for l in open(RUNS / batch / f"{arm}-seed{seed}" / "events.jsonl")]
    turns = max(e["turn"] for e in ev)
    out_turns = {e["turn"] for e in ev if e["kind"] == "output"}
    outs = {e["output_id"]: e["turn"] for e in ev if e["kind"] == "output"}
    scored = [e["output_id"] for e in ev if e["kind"] == "score"]
    n = len(ev[0]["workers"])
    s_excl = sum(outs[o] < turns for o in scored); h_excl = sum(t < turns for t in outs.values())
    dead = [t for t in range(1, turns + 1) if t not in out_turns]
    return {"arm": arm, "seed": seed, "turns": turns, "workers": n, "possible": n * turns, "handed_in": len(outs), "scored": len(scored),
            "coverage": len(scored) / (n * turns), "handed_in_share": len(outs) / (n * turns),
            "scored_of_handed": len(scored) / len(outs) if outs else None,
            "coverage_excl_last": s_excl / (n * (turns - 1)) if turns > 1 else None,
            "dead_turns": len(dead), "dead_share": len(dead) / turns, "first_dead_turn": dead[0] if dead else None,
            "dead_to_end": bool(dead) and dead == list(range(dead[0], turns + 1)), "unscored_handed_excl_last": h_excl - s_excl}

E, skipped = [], []
for name, batch, arm in ARMS:
    for d in sorted((RUNS / batch).glob(f"{arm}-seed*")):
        tail = d.name.split("-seed")[1]
        if not tail.isdigit():
            continue
        r = rows.get((batch, arm, int(tail)))
        if r is None or not truthy(r.get("episode_valid")):
            skipped.append(f"{batch}/{d.name}"); continue
        E.append(episode(batch, arm, int(tail)))

def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None
L = ["# Evaluation coverage and dead time, Judging arms", "", "Six workers per episode; possible outputs = 6 x turns started (one per worker per turn). Means are over episodes.", "",
     "| arm | episodes | mean turns | possible | handed in | scored | coverage (scored/possible) | median coverage | coverage excl. last turn | scored / handed in | episodes with dead time | mean dead share | median dead share | dead time runs to the end |",
     "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
S = {}
for name, _, arm in ARMS:
    X = [e for e in E if e["arm"] == arm]
    if not X: continue
    S[name] = {"episodes": len(X), "turns_mean": mean([e["turns"] for e in X]), "possible": sum(e["possible"] for e in X),
               "handed_in": sum(e["handed_in"] for e in X), "scored": sum(e["scored"] for e in X),
               "coverage_mean": mean([e["coverage"] for e in X]), "coverage_median": st.median(e["coverage"] for e in X),
               "coverage_excl_last_mean": mean([e["coverage_excl_last"] for e in X]), "scored_of_handed_mean": mean([e["scored_of_handed"] for e in X]),
               "with_dead": sum(e["dead_turns"] > 0 for e in X), "dead_share_mean": mean([e["dead_share"] for e in X]),
               "dead_share_median": st.median(e["dead_share"] for e in X), "dead_to_end": sum(e["dead_to_end"] for e in X)}
    s = S[name]
    L.append(f"| {name} | {s['episodes']} | {s['turns_mean']:.3g} | {s['possible']} | {s['handed_in']} | {s['scored']} | {s['coverage_mean']:.3f} | {s['coverage_median']:.3f} | "
             f"{s['coverage_excl_last_mean']:.3f} | {s['scored_of_handed_mean']:.3f} | {s['with_dead']} | {s['dead_share_mean']:.3f} | {s['dead_share_median']:.3f} | {s['dead_to_end']} |")
L += ["", f"Skipped (not a valid scored episode): {len(skipped)}" + (": " + ", ".join(skipped) if skipped else "")]
here = Path(__file__).parent
(here / "coverage-results.md").write_text("\n".join(L) + "\n")
(here / "coverage-results.json").write_text(json.dumps({"arms": S, "episodes": E, "skipped": skipped}, indent=1))
import csv
with open(here / "coverage-episodes.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(E[0])); w.writeheader(); w.writerows(E)
print("\n".join(L))
