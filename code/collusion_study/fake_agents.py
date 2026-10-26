"""Scripted stand-ins for model replies, for offline tests only. Nothing here is model output.

Each responder reads the rendered prompt and writes text tool calls the way a model would,
so the whole pipeline (prompts, parsing, tools, environment, scoring) can be exercised
without calling a model.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import List, Optional, Tuple

EARLIER_REPLY = "=== Your earlier reply in this turn ==="


def _call(name: str, **arguments) -> str:
    return "<tool_call>\n" + json.dumps({"name": name, "arguments": arguments}) + "\n</tool_call>"


def _agent(prompt: str) -> str:
    match = re.search(r"You are agent (\S+)", prompt)
    return match.group(1) if match else "unknown"


def _jira_costs(prompt: str) -> List[Tuple[str, float]]:
    section = prompt.split("=== YOUR COSTS (PRIVATE) ===", 1)[-1].split("===", 1)[0]
    return [(m.group(1), float(m.group(2))) for m in re.finditer(r"- (\S+): cost=([\d.]+)", section)]


def _collusion_board(prompt: str) -> Optional[int]:
    match = re.search(r"Collusion blackboard_id: (\d+)", prompt)
    return int(match.group(1)) if match else None


def _boards(prompt: str) -> List[int]:
    return sorted({int(b) for b in re.findall(r"^Blackboard (\d+):", prompt, flags=re.MULTILINE)})


def jira_fake_responder(system: str, prompt: str, model: str, effort: Optional[str]) -> str:
    """Posts a plan on every board it can see, then claims one of its cheaper tasks."""
    if EARLIER_REPLY in prompt:
        return "Nothing more this turn."
    agent = _agent(prompt)
    costs = sorted(_jira_costs(prompt), key=lambda item: item[1])
    if not costs:
        return "I cannot see any tasks."
    # Spread choices a little so that fake agents do not all claim the same task.
    pick = costs[int(hashlib.sha256(agent.encode()).hexdigest(), 16) % min(3, len(costs))][0]
    if "Phase: EXECUTION" in prompt or "=== CURRENT PHASE: EXECUTION" in prompt:
        return f"Claiming {pick}.\n" + _call("assign_task", task_id=pick)
    secret = _collusion_board(prompt)
    calls = []
    for board in _boards(prompt):
        if board == secret:
            calls.append(_call("post_message", message=f"[coalition] {agent} will take {pick}.", blackboard_id=board))
        else:
            calls.append(_call("post_message", message=f"{agent} plans to take {pick}.", blackboard_id=board))
    return "Posting my plan.\n" + "\n".join(calls)


def _library_share(prompt: str) -> List[str]:
    section = prompt.split("=== YOUR SHARE (papers you file) ===", 1)[-1].strip().split("\n", 1)[0]
    return [pid.strip() for pid in section.split(",") if pid.strip()]


def library_fake_responder(system: str, prompt: str, model: str, effort: Optional[str]) -> str:
    """Proposes a scheme on every board it can see, then files its share under the first word of each title."""
    if EARLIER_REPLY in prompt:
        return "Nothing more this turn."
    agent = _agent(prompt)
    share = _library_share(prompt)
    if "=== CURRENT PHASE: EXECUTION" in prompt:
        titles = dict(re.findall(r"^- (\w+): (\S+)", prompt, flags=re.MULTILINE))
        return "Filing.\n" + _call("file_papers", assignments={pid: titles.get(pid, "General") for pid in share})
    calls = [_call("post_message", message=f"{agent} will file {', '.join(share)}.", blackboard_id=b) for b in _boards(prompt)]
    return "Posting my plan.\n" + "\n".join(calls)


def fake_responder_for(config_path: str):
    """The scripted responder that matches a config's environment."""
    import yaml

    with open(config_path, encoding="utf-8") as handle:
        env = (yaml.safe_load(handle) or {}).get("environment") or {}
    if "library" in str(env.get("import_path", "")):
        return library_fake_responder
    return jira_fake_responder
