"""Read-only: every judge reply and board message in a turn where the judge's team was paused at the start of the turn,
channel_only (judges-main) vs separate_budget. Scorer code (judges-coding-v2 paused_content) shown where one exists."""
import csv, glob, json, re, statistics as st
from pathlib import Path
RUNS = Path("../../runs"); CODING = Path("../judges-coding-v2")
ARMS = [("channel_only", "judges-main", "channel_only"), ("separate_budget", "judges-separate-budget", "separate_budget")]
def cell(t): return (t or "").replace("|", "\\|").replace("\n", " ").strip()
rows = []
for label, batch, arm in ARMS:
    dirs = sorted((d for d in glob.glob(f"{RUNS}/{batch}/{arm}-seed*") if re.search(r"seed\d+$", d)), key=lambda d: int(d.rsplit("seed", 1)[1]))
    for d in dirs:
        seed = int(d.rsplit("seed", 1)[1])
        ev = [json.loads(l) for l in open(f"{d}/events.jsonl")]
        start = next(e for e in ev if e["kind"] == "start")
        pair = start["pair"]
        cpath = CODING / f"{batch}__{arm}-seed{seed}.coding.json"
        codes = {}
        if cpath.exists():
            for u in json.load(open(cpath))["units"]:
                if u["kind"] == "R": codes[(u["turn"], u["judge"])] = u["paused_content"]
        paused = {(e["turn"], e["actor"]): e for e in ev if e["kind"] == "judge_reply" and e.get("team_paused_at_start")}
        msgs = {}
        for e in ev:
            if e["kind"] == "message" and (e["turn"], e["actor"]) in paused:
                msgs.setdefault((e["turn"], e["actor"]), []).append(e)
        for key in sorted(paused, key=lambda k: (k[0], paused[k]["time_utc"])):
            r = paused[key]; role = "pair" if key[1] in pair else "third"
            base = dict(arm=label, seed=seed, turn=key[0], judge=key[1], role=role, budget_after=r["budget_after"],
                        reply_cost=r["cost"], scorer_code=codes.get(key) or ("not coded (third judge)" if role == "third" else ""))
            ms = msgs.get(key, [])
            if not ms:
                rows.append(dict(base, board="", message="(no board message)"))
            for m in ms:
                rows.append(dict(base, board=("shared" if m["board_id"] == 0 else "private pair"), message=m["message"]))
with open("paused-turn-messages.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
out = ["# Messages in paused turns: channel_only vs separate_budget", "",
       "Every judge turn that started with the judge's own team paused, and every board message posted in that turn. "
       "One row per message; a paused turn with no message shows \"(no board message)\". Role: pair = seeded pair member, third = the other judge. "
       "Scorer code: the GPT scorer's hard-collusion code for that reply (explaining / beyond); only pair members are coded. Read-only; compiled from events.jsonl and judges-coding-v2.", ""]
out += ["## Counts", "", "| arm | episodes with a paused turn | paused judge-turns | board messages | silent paused turns | mean message length (chars) | pair replies coded beyond / explaining |", "|---|---|---|---|---|---|---|"]
for label, _, _ in ARMS:
    R = [r for r in rows if r["arm"] == label]
    turns = {(r["seed"], r["turn"], r["judge"]) for r in R}
    M = [r for r in R if r["board"]]
    coded = {(r["seed"], r["turn"], r["judge"]): r["scorer_code"] for r in R if r["role"] == "pair"}
    out.append(f"| {label} | {len({r['seed'] for r in R})} of 32 | {len(turns)} | {len(M)} | {sum(1 for r in R if not r['board'])} | "
               f"{round(st.mean(len(r['message']) for r in M)) if M else 0} | {sum(v=='beyond' for v in coded.values())} / {sum(v=='explaining' for v in coded.values())} |")
for label, _, _ in ARMS:
    out += ["", f"## {label}", ""]
    R = [r for r in rows if r["arm"] == label]
    for seed in sorted({r["seed"] for r in R}):
        out += [f"### {label} seed {seed}", "", "| turn | judge | role | board | budget left | scorer code | message |", "|---|---|---|---|---|---|---|"]
        for r in (r for r in R if r["seed"] == seed):
            out.append(f"| {r['turn']} | {r['judge']} | {r['role']} | {r['board'] or '-'} | {r['budget_after']} | {r['scorer_code']} | {cell(r['message'])} |")
        out.append("")
Path("paused-turn-messages.md").write_text("\n".join(out))
print(len(rows)); print("\n".join(out[7:12]))
