"""Full readable transcript of one Judging episode, from runs/<batch>/<arm>-seed<n>/ (calls.jsonl + events.jsonl). Verbatim; nothing summarised."""
import json, re, sys
from pathlib import Path
batch, ep, out = sys.argv[1], sys.argv[2], sys.argv[3]
d = Path(__file__).resolve().parents[2] / "runs" / batch / ep
calls = [json.loads(l) for l in open(d / "calls.jsonl")]
ev = [json.loads(l) for l in open(d / "events.jsonl")]
s = ev[0]
L = [f"# {s['arm']['name']} seed {d.name.split('seed')[1]}: full transcript", "",
     f"Source: runs/{batch}/{d.name}/ (calls.jsonl for prompts and replies, events.jsonl for what the engine did). Everything below is verbatim.", "",
     f"Judges: {', '.join(s['judges'])}. Seeded pair (has private Board 1): {' and '.join(s['pair'])}. Judges' budget: {s['budget_total']} tokens.", "",
     "Workers: " + "; ".join(f"{w} ({v['family']}) on {v['judge']}'s team" for w, v in s["workers"].items()), "",
     "Each judge turn shows: the state block of the judge's prompt from 'Budget remaining' through the outputs to score (the board history and team lists that follow are left out, because the boards are shown as they happen below), then the judge's full reply, then what the engine recorded.", "",
     "## System prompt (first call, Mayola)", "", "```", calls[0]["system"].split("# Tools")[0].strip(), "```", ""]
ci = 0; turn = 0; i = 1
def fmt(e):
    k = e["kind"]
    if k == "score": return f"- score {e['output_id']} ({e['worker']}, {e['family']}, {e['version']} output): **{e['score']}**"
    if k == "message": return f"- **Board {e['board_id']} post by {e['actor']}:** \"{e['message']}\""
    if k == "pause": return f"- **{e['actor']} paused their team**"
    if k in ("resume", "remove"): return f"- **{e['actor']} {k}** {json.dumps({x:y for x,y in e.items() if x not in ('turn','kind','actor','time_utc')})}"
    if k == "flat_scoring_charge": return None
    if k == "output": return None
    if k == "end": return f"\n**Run ended:** {json.dumps({x:y for x,y in e.items() if x not in ('kind','actor','time_utc')})}"
    return f"- {k}: {json.dumps({x:y for x,y in e.items() if x not in ('time_utc',)})[:300]}"
for e in ev[1:]:
    if e["turn"] != turn and e["kind"] != "end":
        turn = e["turn"]; L += ["", f"## Turn {turn}", ""]
    if e["kind"] == "judge_reply":
        c = calls[ci]; ci += 1
        assert c["meta_agent"] == e["actor"] and c["meta_turn"] == e["turn"], (c["meta_agent"], e["actor"])
        p = c["prompt"]; blk = p[p.rfind("=== TURN"):]
        state = blk.split("=== ALL TEAMS ===")[0].strip()
        L += [f"### {e['actor']}" + (" (team paused at start of turn)" if e.get("team_paused_at_start") else ""), "", "Prompt state:", "", "```", state, "```", "", "Reply (charged " + str(e["charged"]) + " tokens):", "", "```", e["text"].strip(), "```", "", "Recorded:"]
        continue
    line = fmt(e)
    if line: L.append(line)
assert ci == len(calls)
Path(out).write_text("\n".join(L) + "\n")
print("ok", ci, "calls", len(L), "lines")
