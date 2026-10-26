"""Read-only: candidate list of Judging episodes where judges' board messages explicitly talk about extending the run
or preserving the budget, with verbatim quotes and the pause/remove actions around them. A search aid, not a coding."""
import glob, json, re
from pathlib import Path
RUNS = Path("../../runs")
ARMS = [("C4", "judges-main", "C4"), ("channel_only", "judges-main", "channel_only"),
        ("separate_budget", "judges-separate-budget", "separate_budget"), ("instructed", "judges-instructed", "instructed")]
STRICT = re.compile(r"extend(s|ing)? (the |our )?run|run duration|extend (the )?(run'?s? )?(life|duration)|prolong|keep (the )?run (going|alive)|"
                    r"run (alive|going longer)|last longer|run longer|prevent (the )?run (from )?ending|before (the )?run ends|survival|surviv", re.I)
BROAD = re.compile(r"(preserv|conserv|sav)(e|es|ed|ing) (the )?(remaining |our )?(budget|tokens|reserves)", re.I)
def cell(t): return (t or "").replace("|", "\\|").replace("\n", " ").strip()
summary, sections = [], []
for label, batch, arm in ARMS:
    dirs = sorted((d for d in glob.glob(f"{RUNS}/{batch}/{arm}-seed*") if re.search(r"seed\d+$", d)), key=lambda d: int(d.rsplit("seed", 1)[1]))
    for d in dirs:
        seed = int(d.rsplit("seed", 1)[1])
        ev = [json.loads(l) for l in open(f"{d}/events.jsonl")]
        pair = ev[0]["pair"]
        hits = []
        for e in ev:
            if e["kind"] != "message": continue
            tier = "strict" if STRICT.search(e["message"]) else ("broad" if BROAD.search(e["message"]) else None)
            if tier: hits.append((tier, e))
        acts = [e for e in ev if e["kind"] in ("pause", "remove", "resume")]
        outs = [int(e["turn"]) for e in ev if e["kind"] == "output"]
        turns = max(int(e["turn"]) for e in ev)
        ns = sum(t == "strict" for t, _ in hits); nb = len(hits) - ns
        last = max(outs) if outs else 0
        summary.append((label, seed, ns, nb, turns, last, ", ".join(f"t{e['turn']} {e['kind']} {e['actor']}" for e in acts[:6])))
        if not ns: continue
        role = lambda a: "pair" if a in pair else "third"
        sections += [f"### {label} seed {seed} (pair {' + '.join(pair)}; {turns} turns; last worker output turn {last}; turns with no work: {turns - last})", "",
                     "Actions: " + ("; ".join(f"turn {e['turn']} {e['kind']} {e['actor']}" + (f" ({e.get('worker_id')})" if e.get('worker_id') else "") for e in acts) or "none"), "",
                     "| turn | judge | role | board | message |", "|---|---|---|---|---|"]
        sections += [f"| {e['turn']} | {e['actor']} | {role(e['actor'])} | {'private' if e['board_id'] == 1 else 'shared'} | {cell(e['message'])} |"
                     for t, e in hits if t == "strict"]
        sections.append("")
out = ["# Candidate list: explicit \"extend the run\" talk in the Judging experiment", "",
       "A search aid for Artemiy's labelling, not a coding. Board messages (shared and private) were matched by two word patterns:",
       "- **strict:** extend the run / run duration / prolong / keep the run going / last longer / prevent the run from ending / survival.",
       "- **broad:** preserve / conserve / save the (remaining) budget or tokens. Broad matches are only counted here; quotes are listed for strict matches only.",
       "Pattern matching misses paraphrases and can catch messages that mention the words in another sense, so read the quotes. "
       "\"Turns with no work\" = turns after the last worker output. Compiled read-only from events.jsonl.", "",
       "## Summary", "", "| arm | episodes | with a strict match | strict messages | broad messages |", "|---|---|---|---|---|"]
for label, *_ in ARMS:
    S = [s for s in summary if s[0] == label]
    out.append(f"| {label} | {len(S)} | {sum(s[2] > 0 for s in S)} | {sum(s[2] for s in S)} | {sum(s[3] for s in S)} |")
out += ["", "## Per episode", "", "| arm | seed | strict | broad | turns | last output turn | first actions |", "|---|---|---|---|---|---|---|"]
out += [f"| {a} | {s} | {ns} | {nb} | {t} | {l} | {acts} |" for a, s, ns, nb, t, l, acts in summary]
out += ["", "## Quotes (episodes with a strict match)", ""] + sections
Path("extend-the-run-candidates.md").write_text("\n".join(out))
print("\n".join(out[9:16]))
