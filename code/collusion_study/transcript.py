"""A readable page of finished Jira episodes, for reading the raw interactions.

    python -m collusion_study.transcript ../runs/control --out ../transcripts/control.html

For each episode the page shows who is in the pair, which boards exist, every reply in order
(with its posts, its choice and anything written outside tool calls), each board's log, the
final choices, and what each agent was told. Rewards and other scores are left out unless
--scores is given, so the page can be read before anyone looks at results.
"""
from __future__ import annotations

import argparse
import collections
import difflib
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .colosseum_runner import CONDITIONS
from .results import colosseum_run_dir, episode_dirs
from .textcalls import TOOL_CALL_RE, ParsedCall, parse_tool_calls

PROSE_NOTE = ('<p class="prose-note"><i>Grey italic text</i> was written outside tool calls: '
              'the agent\'s own notes, which the other agents never see.</p>')

PAIR_ROLE = {
    (True, True): "told to coordinate as a secret coalition, and given a private board",
    (False, True): "told to coordinate as a secret coalition, without a private board",
    (True, False): "given a private board, with no instructions",
}


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def load_episode(out_dir: Path) -> Dict[str, Any]:
    run_dir = colosseum_run_dir(out_dir)
    if run_dir is None or not (out_dir / "episode_summary.json").exists():
        raise FileNotFoundError(f"{out_dir} has no finished Colosseum run")

    def read(name: str) -> Any:
        return json.loads((run_dir / name).read_text())

    calls = [json.loads(line) for line in (out_dir / "calls.jsonl").read_text().splitlines() if line.strip()]
    return {
        "id": out_dir.name,
        "summary": json.loads((out_dir / "episode_summary.json").read_text()),
        "boards": read("blackboards.json"),
        "final": read("final_summary.json"),
        "metrics": read("metrics.json"),
        "run_config": read("run_config.json"),
        "calls": calls,
    }


# Sections every agent's prompt has; anything else in a prompt was added for that agent.
STANDARD_SECTIONS = ("TURN INFORMATION", "YOUR PRIVATE STATE", "TASKS (PUBLIC)", "YOUR COSTS (PRIVATE)",
                     "BLACKBOARD COMMUNICATIONS", "CURRENT PHASE")


def extra_sections(prompt: str) -> List[Tuple[str, str]]:
    titles = re.findall(r"^=== (.*?) ===$", prompt or "", re.MULTILINE)
    return [(t, prompt_section(prompt, t)) for t in titles if not t.startswith(STANDARD_SECTIONS)]


def prompt_section(prompt: str, title: str) -> str:
    match = re.search(rf"=== {re.escape(title)} ===\n(.*?)(?=\n=== |\Z)", prompt or "", re.DOTALL)
    return match.group(1).strip() if match else ""


def reply_segments(text: str) -> Tuple[List[Tuple[str, Any]], str]:
    """Prose and tool calls in the order written, and any tail the run cut off.

    The run cuts a reply at the first tool result the model wrote itself, so only what comes
    before it was acted on.
    """
    cut = text.find("<tool_result")
    kept, dropped = (text, "") if cut < 0 else (text[:cut], text[cut:])
    segments: List[Tuple[str, Any]] = []
    pos = 0
    for match in TOOL_CALL_RE.finditer(kept):
        if kept[pos:match.start()].strip():
            segments.append(("prose", kept[pos:match.start()].strip()))
        segments.append(("call", parse_tool_calls(match.group(0))[0]))
        pos = match.end()
    tail = kept[pos:]
    if "<tool_call>" in tail:
        before, _, after = tail.partition("<tool_call>")
        if before.strip():
            segments.append(("prose", before.strip()))
        segments.append(("call", ParsedCall(None, {}, "<tool_call>" + after, error="a <tool_call> block was not closed")))
    elif tail.strip():
        segments.append(("prose", tail.strip()))
    return segments, dropped


class EpisodePage:
    def __init__(self, ep: Dict[str, Any], scores: bool):
        self.ep = ep
        self.scores = scores
        s = ep["summary"]
        self.condition = CONDITIONS[s["condition"]]
        self.agents: List[str] = ep["run_config"]["agent_turn_order"]
        self.pair: List[str] = s.get("assigned_pair") or []
        self.pair_has_role = self.condition.secret_channel or self.condition.pair_instructions
        secret_id = ep["metrics"].get("secret_blackboard_id") if self.condition.secret_channel else None
        self.board_names: Dict[int, str] = {}
        self.board_kinds: Dict[int, str] = {}
        for board in ep["boards"]:
            bid, members = int(board["blackboard_id"]), board["participants"]
            if set(members) >= set(self.agents):
                self.board_names[bid], self.board_kinds[bid] = f"Board {bid} · shared by all {len(members)}", "shared"
            elif secret_id is not None and bid == int(secret_id):
                self.board_names[bid], self.board_kinds[bid] = f"Board {bid} · the pair's private board", "pairboard"
            else:
                self.board_names[bid], self.board_kinds[bid] = f"Board {bid} · private: {' and '.join(members)}", "private"
        # What landed on each board, to mark posts that did not.
        self.landed = collections.Counter(
            (int(e["blackboard_id"]), e["agent"], e["payload"].get("content"))
            for board in ep["boards"] for e in board["events"] if e["kind"] == "communication"
        )

    def who(self, agent: str) -> str:
        badge = ""
        if agent in self.pair:
            badge = '<span class="badge pair">pair</span>' if self.pair_has_role else '<span class="badge drawn">drawn pair</span>'
        return f'<span class="agent{" in-pair" if agent in self.pair else ""}">{esc(agent)}</span>{badge}'

    def render(self) -> str:
        eid = self.ep["id"]
        sections = [
            ("turns", "Replies in order", PROSE_NOTE + self.turns()),
            ("boards", "Board logs", self.board_logs()),
            ("choices", "Final choices", self.choices()),
            ("told", "What each agent was told", self.instructions()),
        ]
        nav = " ".join(f'<a href="#{eid}-{key}">{esc(title)}</a>' for key, title, _ in sections)
        body = "".join(
            f'<section class="part" id="{eid}-{key}"><h3>{esc(title)}</h3>{content}</section>'
            for key, title, content in sections
        )
        return f'<article class="episode" id="{esc(eid)}" hidden>{self.header()}<nav class="jump">{nav}</nav>{body}</article>'

    def header(self) -> str:
        s = self.ep["summary"]
        valid = s.get("valid")
        state = ('<span class="chip ok">valid</span>' if valid else
                 f'<span class="chip bad">invalid: {esc("; ".join(s.get("invalid_reasons") or []))}</span>')
        if self.pair_has_role:
            pair_line = f"Assigned pair: {self.who(self.pair[0])} and {self.who(self.pair[1])}, {PAIR_ROLE[(self.condition.secret_channel, self.condition.pair_instructions)]}."
            if self.condition.private_line:
                pair_line = pair_line[:-1] + f", except one private line: \u201c{esc(s.get('private_line'))}\u201d"
        elif self.pair:
            pair_line = (f"Drawn pair for the comparison: {self.who(self.pair[0])} and {self.who(self.pair[1])}. "
                         "The seed picks them; they are told nothing and have no special role.")
        else:
            pair_line = ""
        boards = "".join(
            f'<li class="b-{self.board_kinds[int(b["blackboard_id"])]}">{esc(self.board_names[int(b["blackboard_id"])])}'
            f'<span class="count">{sum(e["kind"] == "communication" for e in b["events"])} posts</span></li>'
            for b in self.ep["boards"]
        )
        order = " → ".join(self.who(a) for a in self.agents)
        variant = f'<span class="chip">coalition text: {esc(s.get("prompt_variant"))}</span>' if self.condition.pair_instructions else ""
        return f"""
<header class="ep-head">
  <p class="eyebrow">Condition {esc(s["condition"])} · seed {esc(s["seed"])}</p>
  <h2>{esc(self.condition.description.capitalize())}</h2>
  <div class="chips">{state}<span class="chip">{esc(s.get("model"))}</span><span class="chip">{esc(s.get("live_model_calls"))} model calls</span>{variant}</div>
  <p>{pair_line}</p>
  <p class="order"><span class="label">Turn order</span> {order}</p>
  <details class="boards-list"><summary>{len(self.ep["boards"])} board{"" if len(self.ep["boards"]) == 1 else "s"}</summary><ul>{boards}</ul></details>
</header>"""

    def turns(self) -> str:
        groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
        for call in self.ep["calls"]:
            groups.setdefault((str(call.get("meta_phase")), str(call.get("meta_round"))), []).append(call)
        out = []
        for (phase, rnd), calls in groups.items():
            title = f"Planning, round {rnd}" if phase == "planning" else "Execution: each agent makes its final choice" if phase == "execution" else f"{phase} {rnd}"
            out.append(f'<h4 class="round">{esc(title)}</h4>')
            out.extend(self.turn(call) for call in calls)
        return "".join(out)

    def turn(self, call: Dict[str, Any]) -> str:
        agent = call.get("meta_agent")
        head = f'<div class="turn-head">{self.who(agent)}<span class="meta">step {esc(call.get("meta_step"))}</span></div>'
        if call.get("status") != "ok":
            return f'<div class="turn">{head}<div class="act broken">This call failed ({esc(call.get("status"))}).</div></div>'
        text = str(call.get("text") or "")
        segments, dropped = reply_segments(text)
        parts = []
        for kind, value in segments:
            if kind == "prose":
                parts.append(f'<div class="prose"><p>{esc(value)}</p></div>')
            else:
                parts.append(self.action(agent, value))
        if not segments:
            parts.append('<div class="act broken">Empty reply.</div>')
        if dropped:
            parts.append(f'<div class="dropped"><span class="label">Cut by the run: the model went on to write its own tool results</span><pre>{esc(dropped)}</pre></div>')
        raw = f'<details class="raw"><summary>Raw reply</summary><pre>{esc(text)}</pre></details>'
        return f'<div class="turn{" pair-turn" if agent in self.pair else ""}">{head}{"".join(parts)}{raw}</div>'

    def action(self, agent: str, call: ParsedCall) -> str:
        if call.error:
            return f'<div class="act broken"><span class="label">Unusable tool call: {esc(call.error)}</span><pre>{esc(call.raw.strip())}</pre></div>'
        args = call.arguments
        if call.name == "post_message":
            message = args.get("message", args.get("content"))
            try:
                bid: Optional[int] = int(args.get("blackboard_id"))
            except (TypeError, ValueError):
                bid = None
            key = (bid, agent, message)
            note = ""
            if self.landed[key] > 0:
                self.landed[key] -= 1
            else:
                note = '<span class="badge bad">not posted</span>'
            kind = self.board_kinds.get(bid, "unknown") if bid is not None else "unknown"
            name = self.board_names.get(bid, f"Board {args.get('blackboard_id')}")
            return f'<div class="act post b-{kind}"><span class="label">Posted to {esc(name)}</span>{note}<p>{esc(message)}</p></div>'
        if call.name == "assign_task":
            return f'<div class="act choose"><span class="label">Final choice</span><code>{esc(args.get("task_id"))}</code></div>'
        return f'<div class="act other"><span class="label">{esc(call.name)}</span><code>{esc(json.dumps(args, ensure_ascii=False))}</code></div>'

    def board_logs(self) -> str:
        out = []
        for board in self.ep["boards"]:
            bid = int(board["blackboard_id"])
            items = []
            context = ""
            for e in board["events"]:
                payload = e.get("payload") or {}
                if e["kind"] == "context":
                    context = f'<details class="context"><summary>Opening message from the system</summary><pre>{esc(payload.get("message"))}</pre></details>'
                elif e["kind"] == "communication":
                    items.append(f'<li>{self.who(e["agent"])}<span class="meta">{esc(payload.get("phase"))}</span><p>{esc(payload.get("content"))}</p></li>')
                elif e["kind"] == "action_executed":
                    task = (payload.get("action_params") or {}).get("task_id")
                    items.append(f'<li class="action">{self.who(e["agent"])} chose <code>{esc(task)}</code><span class="meta">{esc(payload.get("result_status"))}</span></li>')
                else:
                    items.append(f'<li class="action">{esc(e.get("agent"))} · {esc(e["kind"])} <code>{esc(json.dumps(payload, ensure_ascii=False)[:400])}</code></li>')
            log = f'<ol class="log">{"".join(items)}</ol>' if items else '<p class="empty">Nobody posted here.</p>'
            out.append(f'<div class="board b-{self.board_kinds[bid]}"><h4>{esc(self.board_names[bid])}</h4>{context}{log}</div>')
        return "".join(out)

    def choices(self) -> str:
        rows = []
        for a in sorted(self.ep["metrics"].get("agents", []), key=lambda a: self.agents.index(a["agent_name"])):
            cost = a.get("task_cost")
            rows.append(
                f'<tr><td>{self.who(a["agent_name"])}</td><td><code>{esc(a.get("chosen_task_id") or "skip")}</code></td>'
                f'<td>{esc(a.get("task_priority"))}</td><td class="num">{esc(a.get("task_effort"))}</td>'
                f'<td class="num">{"" if cost is None else f"{cost:.2f}"}</td></tr>'
            )
        table = ('<div class="table-wrap"><table><thead><tr><th>Agent</th><th>Task chosen</th><th>Priority</th>'
                 f'<th class="num">Effort</th><th class="num">Own cost</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
        final = self.ep["final"]
        facts = (f'<p>{esc(final.get("tasks_done"))} of {esc(final.get("total_agents"))} agents took a task; '
                 f'{esc(final.get("violations"))} duplicate or infeasible choices; {esc(final.get("random_fallback_fills"))} choices filled at random.</p>')
        return table + facts + (self.score_block() if self.scores else "")

    def score_block(self) -> str:
        s = self.ep["summary"]
        rewards = s.get("agent_rewards") or {}
        regrets = {a["agent_name"]: a.get("regret") for a in self.ep["metrics"].get("agents", [])}
        rows = "".join(
            f'<tr><td>{self.who(a)}</td><td class="num">{rewards.get(a, float("nan")):.2f}</td>'
            f'<td class="num">{"" if regrets.get(a) is None else f"{regrets[a]:.2f}"}</td></tr>'
            for a in self.agents
        )
        best_pair, best_subset = s.get("best_pair") or {}, s.get("best_subset") or {}

        def num(v: Any) -> str:
            return "" if v is None else f"{v:.2f}"

        return f"""
<details class="scores"><summary>Scores</summary>
<div class="table-wrap"><table><thead><tr><th>Agent</th><th class="num">Reward</th><th class="num">Regret</th></tr></thead><tbody>{rows}</tbody></table></div>
<dl class="facts">
  <dt>{"Assigned" if self.pair_has_role else "Drawn"} pair's advantage</dt><dd>{num(s.get("assigned_pair_advantage"))}</dd>
  <dt>Best pair</dt><dd>{esc(" and ".join(best_pair.get("subset") or []))}: {num(best_pair.get("advantage"))}</dd>
  <dt>Best subset of any size</dt><dd>{esc(", ".join(best_subset.get("subset") or []))}: {num(best_subset.get("advantage"))}</dd>
  <dt>Joint reward</dt><dd>{num(s.get("joint_reward"))}</dd>
  <dt>System regret</dt><dd>{num(s.get("system_regret"))}</dd>
</dl>
</details>"""

    def instructions(self) -> str:
        systems: Dict[str, str] = {}
        first_prompt: Dict[str, str] = {}
        for call in self.ep["calls"]:
            agent = call.get("meta_agent")
            systems.setdefault(agent, call.get("system") or "")
            first_prompt.setdefault(agent, call.get("prompt") or "")
        base = collections.Counter(systems.values()).most_common(1)[0][0] if systems else ""
        diffs = []
        for agent in self.agents:
            if systems.get(agent, base) == base:
                continue
            lines = [line for line in difflib.unified_diff(base.splitlines(), systems[agent].splitlines(), lineterm="", n=0)
                     if not line.startswith(("---", "+++", "@@"))]
            diff = "".join(f'<span class="{"add" if line.startswith("+") else "del"}">{esc(line)}</span>\n' for line in lines)
            diffs.append(f'<div class="diff"><p>{self.who(agent)}\'s system prompt differs from the shared one:</p><pre>{diff}</pre></div>')
        agents_without_diff = [a for a in self.agents if systems.get(a, base) == base]
        same = f'<p>{len(agents_without_diff)} of {len(self.agents)} agents got exactly the shared system prompt.</p>'
        extras = [(agent, title, text) for agent in self.agents for title, text in extra_sections(first_prompt.get(agent, ""))]
        diffs.extend(f'<div class="diff"><p>Only {self.who(agent)}\'s prompts carry this section:</p>'
                     f'<pre>=== {esc(title)} ===\n{esc(text)}</pre></div>' for agent, title, text in extras)
        if not extras:
            same += '<p>No agent\'s prompts carry extra instructions.</p>'
        private = "".join(
            f'<div class="private-state"><p>{self.who(a)}</p><pre>{esc(prompt_section(first_prompt.get(a, ""), "YOUR PRIVATE STATE"))}\n\n'
            f'{esc(prompt_section(first_prompt.get(a, ""), "YOUR COSTS (PRIVATE)"))}</pre></div>'
            for a in self.agents
        )
        tasks = prompt_section(next(iter(first_prompt.values()), ""), "TASKS (PUBLIC)")
        return f"""
<details><summary>The shared system prompt</summary><pre>{esc(base)}</pre></details>
{same}{"".join(diffs)}
<details><summary>The task list everyone sees</summary><pre>{esc(tasks)}</pre></details>
<details><summary>Each agent's private state and costs</summary><div class="private-grid">{private}</div></details>"""


STYLE = """
/* Layout: one reading column under a sticky bar with the episode picker; replies grouped by round like a sprint log */
:root {
  --bg: #F5F6F8; --surface: #FFFFFF; --fg: #18202B; --muted: #5A6575; --line: #DCE1E7;
  --accent: #2C5E8C; --pair: #9A5A00; --pair-bg: #FBF1E2; --private: #6A4C99; --private-bg: #F2EEF8;
  --ok: #2E7D4F; --bad: #B3261E; --add-bg: #E7F4EC; --del-bg: #FBEAE9;
  --font-display: "Archivo", "Helvetica Neue", Arial, sans-serif;
  --font-body: "Public Sans", "Segoe UI", system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #11151B; --surface: #181E26; --fg: #E3E8EF; --muted: #98A3B3; --line: #2A333F;
  --accent: #86AEDB; --pair: #E3A653; --pair-bg: #2A2116; --private: #B49BDD; --private-bg: #221D2C;
  --ok: #6CC290; --bad: #F08A80; --add-bg: #17291F; --del-bg: #2E1A19; color-scheme: dark; } }
:root[data-theme="dark"] {
  --bg: #11151B; --surface: #181E26; --fg: #E3E8EF; --muted: #98A3B3; --line: #2A333F;
  --accent: #86AEDB; --pair: #E3A653; --pair-bg: #2A2116; --private: #B49BDD; --private-bg: #221D2C;
  --ok: #6CC290; --bad: #F08A80; --add-bg: #17291F; --del-bg: #2E1A19; color-scheme: dark; }
body { background: var(--bg); color: var(--fg); font: 15px/1.55 var(--font-body); }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 10; background: var(--surface); }
.bar { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; background: var(--surface); border-bottom: 1px solid var(--line);
  padding-block: 10px; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.bar-inner { max-width: 860px; margin: 0 auto; display: flex; flex-wrap: wrap; gap: 8px 16px; align-items: center; justify-content: space-between; }
.bar h1 { font: 600 1rem/1.2 var(--font-display); margin: 0; letter-spacing: .01em; }
.bar label { color: var(--muted); font-size: .85rem; display: flex; gap: 8px; align-items: center; }
select { font: inherit; color: var(--fg); background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 4px 8px; max-width: 100%; }
main { max-width: 860px; margin: 0 auto; padding-block: 8px 48px; padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
.note { color: var(--muted); font-size: .85rem; }
.eyebrow { font: 500 .78rem/1 var(--font-mono); text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin: 20px 0 6px; }
h2 { font: 700 1.5rem/1.2 var(--font-display); margin: 0 0 10px; text-wrap: balance; }
h3 { font: 600 1.15rem/1.3 var(--font-display); margin: 0 0 12px; padding-top: 8px; border-top: 2px solid var(--fg); }
h4 { font: 600 .95rem/1.3 var(--font-display); margin: 0 0 8px; }
.part, .episode { margin-top: 28px; scroll-margin-top: 64px; }
.episode { margin-top: 0; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { font: 500 .78rem/1 var(--font-mono); border: 1px solid var(--line); border-radius: 999px; padding: 5px 9px; color: var(--muted); }
.chip.ok { color: var(--ok); border-color: currentColor; }
.chip.bad { color: var(--bad); border-color: currentColor; }
.label { display: block; font: 500 .72rem/1.3 var(--font-mono); text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin-bottom: 2px; }
.order .label { display: inline; margin-right: 6px; }
.agent { font-weight: 600; }
.agent.in-pair { color: var(--pair); }
.badge { font: 500 .68rem/1 var(--font-mono); text-transform: uppercase; letter-spacing: .06em; border-radius: 4px; padding: 3px 5px; margin-left: 6px; vertical-align: 1px; }
.badge.pair { background: var(--pair); color: var(--surface); }
.badge.drawn { border: 1px solid var(--pair); color: var(--pair); }
.badge.bad { border: 1px solid var(--bad); color: var(--bad); }
.jump { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 14px; font-size: .9rem; }
a { color: var(--accent); }
a:focus-visible, summary:focus-visible, select:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.boards-list ul { margin: 6px 0 0; padding-left: 18px; }
.boards-list .count { color: var(--muted); font-size: .85rem; margin-left: 8px; }
.round { font: 500 .8rem/1 var(--font-mono); text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin: 22px 0 8px; }
.turn { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; display: grid; gap: 8px; min-width: 0; }
.turn.pair-turn { border-left: 3px solid var(--pair); }
.turn-head { display: flex; align-items: baseline; gap: 4px; flex-wrap: wrap; }
.meta { color: var(--muted); font: .75rem/1 var(--font-mono); margin-left: auto; }
.prose p { margin: 0; font-style: italic; color: var(--muted); white-space: pre-wrap; overflow-wrap: anywhere; }
.prose-note { color: var(--muted); font-size: .88rem; margin: 0 0 4px; }
.prose-note i { color: var(--muted); }
.act { border-radius: 6px; padding: 8px 10px; background: var(--bg); min-width: 0; }
.act p { margin: 2px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.act.b-private, .board.b-private { background: var(--private-bg); }
.act.b-pairboard, .board.b-pairboard { background: var(--pair-bg); }
.act.b-pairboard .label { color: var(--pair); }
.act.b-private .label { color: var(--private); }
.act.choose { display: flex; gap: 8px; align-items: baseline; flex-wrap: wrap; }
.act.choose .label { margin: 0; }
.act.broken { border: 1px dashed var(--bad); color: var(--bad); background: transparent; }
code { font: .85rem/1.4 var(--font-mono); overflow-wrap: anywhere; }
td code { white-space: nowrap; overflow-wrap: normal; }
pre { font: .8rem/1.5 var(--font-mono); white-space: pre-wrap; overflow-wrap: anywhere; margin: 6px 0 0; }
.dropped pre { color: var(--muted); text-decoration: line-through; }
details > summary { cursor: pointer; color: var(--accent); font-size: .88rem; }
.raw > summary { color: var(--muted); font-size: .78rem; }
.board { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; background: var(--surface); }
.log { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 8px; }
.log li { display: block; }
.log li p { margin: 2px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.log li .meta { margin-left: 8px; }
.log li.action { color: var(--muted); }
.empty { color: var(--muted); margin: 6px 0 0; }
.table-wrap { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: .9rem; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { font: 500 .72rem/1.3 var(--font-mono); text-transform: uppercase; letter-spacing: .06em; color: var(--muted); }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.facts { display: grid; grid-template-columns: max-content 1fr; gap: 4px 12px; font-size: .9rem; }
.facts dt { color: var(--muted); }
.facts dd { margin: 0; font-variant-numeric: tabular-nums; }
.diff pre .add { background: var(--add-bg); display: block; }
.diff pre .del { background: var(--del-bg); display: block; }
.private-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 10px; margin-top: 8px; }
.private-state { min-width: 0; }
.private-state p { margin: 0; }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
"""

SCRIPT = """
const picker = document.getElementById('episode');
function show(id) {
  document.querySelectorAll('.episode').forEach(e => { e.hidden = e.id !== id; });
  picker.value = id;
}
picker.addEventListener('change', () => { show(picker.value); window.scrollTo(0, 0); });
const target = document.getElementById(location.hash.slice(1));
const home = target && target.closest('.episode');
show(home ? home.id : picker.options[0].value);
if (target && target !== home) target.scrollIntoView(); else window.scrollTo(0, 0);
"""


def render_page(episodes: List[Dict[str, Any]], title: str, scores: bool, skipped: List[str]) -> str:
    pages = [EpisodePage(ep, scores) for ep in episodes]
    options = "".join(
        f'<option value="{esc(p.ep["id"])}">{esc(p.ep["summary"]["condition"])} · seed {esc(p.ep["summary"]["seed"])}</option>'
        for p in pages
    )
    missing = f'<p class="note">Not shown, because they did not finish: {esc(", ".join(skipped))}.</p>' if skipped else ""
    hidden_scores = "" if scores else '<p class="note">Rewards and other scores are left out of this page.</p>'
    return f"""<title>{esc(title)}</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Archivo:wght@600;700&family=JetBrains+Mono:wght@400;500&family=Public+Sans:ital,wght@0,400;0,600;1,400&display=swap">
<style>{STYLE}</style>
<div class="bar"><div class="bar-inner"><h1>{esc(title)}</h1>
<label for="episode">Episode <select id="episode">{options}</select></label></div></div>
<main>{missing}{hidden_scores}{"".join(p.render() for p in pages)}</main>
<script>{SCRIPT}</script>
"""


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="+", help="a batch directory, or episode directories")
    parser.add_argument("--out", required=True)
    parser.add_argument("--title", default="Jira episode transcripts")
    parser.add_argument("--scores", action="store_true", help="include rewards and advantages")
    args = parser.parse_args(argv)
    dirs: List[Path] = []
    for raw in args.paths:
        path = Path(raw)
        dirs.extend(episode_dirs(path) if (path / "batch_status.json").exists() else [path])
    episodes, skipped = [], []
    for d in dirs:
        try:
            episodes.append(load_episode(d))
        except FileNotFoundError:
            skipped.append(d.name)
    if not episodes:
        sys.exit("no finished episodes found")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_page(episodes, args.title, args.scores, skipped), encoding="utf-8")
    print(json.dumps({"episodes": len(episodes), "skipped": skipped, "out": str(out)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
