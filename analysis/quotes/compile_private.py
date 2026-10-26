"""Read-only: every message on the private pair board (Board 1), all turns, for each arm that has one."""
import glob, json, re, statistics as st
from pathlib import Path
RUNS = Path("../../runs")
ARMS = [("channel_only", "judges-main", "channel_only"), ("separate_budget", "judges-separate-budget", "separate_budget"),
        ("instructed", "judges-instructed", "instructed")]
def cell(t): return (t or "").replace("|", "\\|").replace("\n", " ").strip()
summary, sections = [], []
for label, batch, arm in ARMS:
    dirs = sorted((d for d in glob.glob(f"{RUNS}/{batch}/{arm}-seed*") if re.search(r"seed\d+$", d)), key=lambda d: int(d.rsplit("seed", 1)[1]))
    tot_shared = tot_priv = eps_priv = 0; first = []; pair_shared = 0
    sec = [f"## {label}", ""]
    for d in dirs:
        seed = int(d.rsplit("seed", 1)[1])
        ev = [json.loads(l) for l in open(f"{d}/events.jsonl")]
        pair = ev[0]["pair"]
        msgs = [e for e in ev if e["kind"] == "message"]
        priv = [e for e in msgs if e["board_id"] == 1]
        tot_priv += len(priv); tot_shared += len(msgs) - len(priv)
        pair_shared += sum(1 for e in msgs if e["board_id"] == 0 and e["actor"] in pair)
        turns = int(json.load(open(f"{d}/summary.json"))["turns_started"])
        if priv:
            eps_priv += 1; first.append(int(priv[0]["turn"]))
            sec += [f"### {label} seed {seed} (pair {' + '.join(pair)}, {turns} turns, {len(priv)} private messages)", "",
                    "| turn | from | team paused | message |", "|---|---|---|---|"]
            sec += [f"| {e['turn']} | {e['actor']} | {'yes' if e.get('team_paused') else 'no'} | {cell(e['message'])} |" for e in priv]
            sec.append("")
        else:
            sec += [f"### {label} seed {seed} (pair {' + '.join(pair)}, {turns} turns): no private messages", ""]
    summary.append(f"| {label} | {len(dirs)} | {eps_priv} | {tot_priv} | {pair_shared} | {round(100*tot_priv/max(1,tot_priv+pair_shared),1)}% | "
                   f"{round(tot_priv/len(dirs),1)} | {st.median(first) if first else '-'} |")
    sections += sec
out = ["# Private pair board (Board 1): every message", "",
       "All turns, every episode of each arm with a private board. Only the seeded pair can read or post on Board 1. "
       "\"Team paused\" is the sender's team state when the message was posted. Read-only; compiled from events.jsonl.", "",
       "| arm | episodes | episodes using Board 1 | Board 1 messages | pair's messages on the shared board | Board 1 share of the pair's messages | Board 1 messages per episode | median first turn used |",
       "|---|---|---|---|---|---|---|---|"] + summary + [""] + sections
Path("private-board-messages.md").write_text("\n".join(out))
print("\n".join(out[5:10]))
