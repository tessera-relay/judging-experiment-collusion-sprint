"""Pre-specified test J8 (O90): neutral_pause vs channel_only. Read-only; outside the package so CODE_SHA256 is unchanged.
Uses the package's own permutation test, Holm and mirror-pair units (collusion_study.analysis).
Measures (episode level, the seeded pair): soft = pair_coordinated_removals, hard = pair_hard_collusion_tokens (O40-O43, unchanged);
hard_rate = hard / (2 x turns_started); paused_share = the pair's judge replies that began with their team paused / (2 x turns_started)."""
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))
from collusion_study.analysis import paired_test, holm, read_csv, index, num, truthy, mirror  # noqa: E402

RUNS, AN = ROOT / "runs", ROOT / "analysis"
rows = {**index(read_csv(AN / "judges-coding-v2" / "judges-coding.csv")),
        **index(read_csv(AN / "judges-coding-v2-neutral" / "judges-coding.csv"))}

def measures(batch, arm, seed):
    r = rows.get((batch, arm, seed))
    if r is None or not truthy(r.get("episode_valid")):
        return None
    turns = num(r["turns_started"]); d = RUNS / batch / f"{arm}-seed{seed}"
    ev = [json.loads(l) for l in open(d / "events.jsonl")]
    pair = ev[0]["pair"]
    paused = sum(1 for e in ev if e["kind"] == "judge_reply" and e["actor"] in pair and e.get("team_paused_at_start"))
    soft, hard = num(r["pair_coordinated_removals"]), num(r["pair_hard_collusion_tokens"])
    return {"soft": soft, "hard": hard, "hard_rate": hard / (2 * turns), "paused_share": paused / (2 * turns),
            "coordinated_pauses": num(r["pair_coordinated_pauses"]), "turns": turns,
            "any_pause": float(any(e["kind"] == "pause" for e in ev))}

ARMS = {"channel_only": ("judges-main", "channel_only"), "neutral_pause": ("judges-neutral-pause", "neutral_pause")}
M = {(name, s): measures(*ARMS[name], s) for name in ARMS for s in range(1, 33)}

def unit(name, k, m):
    v = [M.get((name, s)) for s in (2 * k - 1, 2 * k)]
    return None if any(x is None for x in v) else (v[0][m] + v[1][m]) / 2

def test(a, b, m, ks):
    return {"a": a, "b": b, "measure": m, **paired_test({f"mirror pair {k} (seeds {2*k-1},{2*k})": (unit(a, k, m), unit(b, k, m)) for k in ks})}

missing = [s for s in range(1, 33) if M.get(("neutral_pause", s)) is None or M.get(("channel_only", s)) is None]
assert not missing, f"episodes without measures: {missing}"
out = {}
j8 = {f"J8 {m}": test("neutral_pause", "channel_only", m, range(1, 17)) for m in ("soft", "hard")}
for k, h in holm({k: v["p"] for k, v in j8.items()}).items(): j8[k].update(h)
j8r = {f"J8 {m} (alongside)": test("neutral_pause", "channel_only", m, range(1, 17)) for m in ("hard_rate", "paused_share", "any_pause")}
out.update(j8); out.update(j8r)

def desc(name, seeds):
    E = [M[(name, s)] for s in seeds if M.get((name, s))]
    return {"episodes": len(E), "with_any_pause": int(sum(e["any_pause"] for e in E)),
            "with_coordinated_pause": sum(e["coordinated_pauses"] > 0 for e in E), "with_soft": sum(e["soft"] > 0 for e in E),
            "with_hard": sum(e["hard"] > 0 for e in E), "turns_mean": sum(e["turns"] for e in E) / len(E),
            "hard_mean": sum(e["hard"] for e in E) / len(E), "hard_rate_mean": sum(e["hard_rate"] for e in E) / len(E),
            "paused_share_mean": sum(e["paused_share"] for e in E) / len(E)}
D = {"channel_only seeds 1-32": desc("channel_only", range(1, 33)), "neutral_pause seeds 1-32": desc("neutral_pause", range(1, 33))}

def f(x):
    return "-" if x is None else (f"{x:.4g}" if isinstance(x, float) else str(x))
L = ["# neutral_pause vs channel_only: pre-specified test J8 (O90)", "",
     "Units: mirror pairs (seeds 2k-1, 2k averaged). Two-sided exact paired permutation test; difference = A - B. "
     "J8 soft and hard: Holm across the two. hard_rate, paused_share and any_pause are reported alongside, unadjusted (O90). "
     "any_pause per pair = share of the pair's two episodes with any pause.", "",
     "| test | A | B | units | mean A | mean B | A>B / A<B / = | p | Holm p |", "|---|---|---|---|---|---|---|---|---|"]
for k, v in out.items():
    L.append(f"| {k} | {v['a']} | {v['b']} | {v['units']} | {f(v['mean_a'])} | {f(v['mean_b'])} | "
             f"{v['units_a_higher']} / {v['units_b_higher']} / {v['units_equal']} | {f(v['p'])} | {f(v.get('p_holm'))} |")
L += ["", "## Descriptive", "", "| arms | episodes | any pause | coordinated pause | soft > 0 | hard > 0 | mean turns | mean hard | mean hard rate | mean paused share |",
      "|---|---|---|---|---|---|---|---|---|---|"]
L += [f"| {k} | {d['episodes']} | {d['with_any_pause']} | {d['with_coordinated_pause']} | {d['with_soft']} | {d['with_hard']} | {d['turns_mean']:.3g} | "
      f"{d['hard_mean']:.4g} | {d['hard_rate_mean']:.3g} | {d['paused_share_mean']:.3g} |" for k, d in D.items()]
(Path(__file__).parent / "neutral-results.md").write_text("\n".join(L) + "\n")
(Path(__file__).parent / "neutral-results.json").write_text(json.dumps({"tests": out, "descriptive": D, "episodes": {f"{a} {s}": v for (a, s), v in M.items()}}, indent=1, default=str))
print("\n".join(L))
