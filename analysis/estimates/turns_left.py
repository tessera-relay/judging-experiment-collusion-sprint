"""Read-only, post hoc (Artemiy 23:36 UTC 25 Oct): judges' explicit "N turns left" estimates on the boards vs (a) the turns the run
actually lasted after that turn and (b) a naive projection at that moment: budget left / spend in the previous full turn.
An estimate's range ("3-4 turns") is taken at its upper bound, so underestimates are counted conservatively."""
import csv, glob, json, re, statistics as st
from pathlib import Path
RUNS = Path("../../runs")
ARMS = [("C4", "judges-main", "C4"), ("channel_only", "judges-main", "channel_only"), ("separate_budget", "judges-separate-budget", "separate_budget"),
        ("instructed", "judges-instructed", "instructed"), ("all_good", "judges-all-good", "all_good"), ("neutral_pause", "judges-neutral-pause", "neutral_pause")]
W = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
N = r"(\d+|one|two|three|four|five|six|seven|eight|nine|ten)"
PAT = re.compile(rf"(?i){N}(?:\s*(?:-|–|to)\s*{N})?\s*(?:more\s+|full\s+)?turns?\b\s+(?:left|remaining|max|maximum|of runway|before)")
num = lambda s: W.get(s.lower()) if s.lower() in W else int(s)
rows = []
for label, batch, arm in ARMS:
    for d in sorted(glob.glob(f"{RUNS}/{batch}/{arm}-seed*")):
        if not re.search(r"seed\d+$", d): continue
        seed = int(d.rsplit("seed", 1)[1]); ev = [json.loads(l) for l in open(f"{d}/events.jsonl")]
        last = max(e["turn"] for e in ev)
        end_budget = {}
        for e in ev:
            if "budget_after" in e: end_budget[e["turn"]] = e["budget_after"]
        for e in ev:
            if e["kind"] != "message": continue
            for m in PAT.finditer(e["message"]):
                lo = num(m.group(1)); hi = num(m.group(2)) if m.group(2) else lo
                t = e["turn"]; actual = last - t
                prev = (end_budget.get(t - 2, ev[0]["budget_after"]) - end_budget[t - 1]) if t >= 2 and (t - 1) in end_budget else None
                proj = (e["budget_after"] // prev) if prev and prev > 0 else None
                rows.append(dict(arm=label, seed=seed, turn=t, judge=e["actor"], board=e.get("board_id"), match=m.group(0), est_low=lo, est_high=hi,
                                 actual_turns_after=actual, projection=proj, run_last_turn=last, message=e["message"]))
with open("turns-left-estimates.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
L = ["# Judges' \"turns left\" estimates vs what happened (post hoc, read-only)", "",
     "Every board message with an explicit estimate such as \"~3 turns left\", \"2-3 turns max\" or \"1 turn remaining\" (regex in turns_left.py; vaguer phrases like \"final turn\" are not counted). "
     "A range is taken at its upper bound. **Actual** = turns the run lasted after the turn of the message. **Projection** = the budget left right after the message divided by the total spend of the previous full turn, rounded down: what the burn rate at that moment implied. "
     "Under = estimate (upper bound) below the comparison. The actual figure includes the effect of pausing after the estimate, so the projection column is the fairer test of a pessimistic bias.", "",
     "| arm | estimates | episodes | under actual | median actual − estimate | with projection | under projection | over projection | median projection − estimate |", "|---|---|---|---|---|---|---|---|---|"]
for label, _, _ in ARMS:
    R = [r for r in rows if r["arm"] == label]
    if not R: L.append(f"| {label} | 0 | 0 | - | - | - | - | - | - |"); continue
    P = [r for r in R if r["projection"] is not None]
    L.append(f"| {label} | {len(R)} | {len({r['seed'] for r in R})} | {sum(r['est_high'] < r['actual_turns_after'] for r in R)} ({sum(r['est_high'] < r['actual_turns_after'] for r in R)/len(R):.0%}) | "
             f"{st.median(r['actual_turns_after'] - r['est_high'] for r in R):g} | {len(P)} | {sum(r['est_high'] < r['projection'] for r in P)} | {sum(r['est_high'] > r['projection'] for r in P)} | "
             f"{(st.median(r['projection'] - r['est_high'] for r in P) if P else float('nan')):g} |")
L += ["", "## Every estimate", "", "| arm | seed | turn | judge | board | estimate | actual after | projection | message |", "|---|---|---|---|---|---|---|---|---|"]
L += [f"| {r['arm']} | {r['seed']} | {r['turn']} | {r['judge']} | {r['board']} | {r['match']} | {r['actual_turns_after']} | {r['projection']} | {r['message'].replace('|', '/').replace(chr(10), ' ')} |" for r in rows]
Path("turns-left-estimates.md").write_text("\n".join(L) + "\n")
print("\n".join(L[:14]))
