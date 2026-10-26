"""Scripted judges for offline tests only. Nothing here is model output.

Each fake judge scores every output it is shown with a 6, posts once per turn on each board it sees, pauses its team in turn 2, resumes it
in turn 3 and removes its first worker in turn 4, so every mechanism of the engine runs.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from ..textcalls import EARLIER_REPLY_HEADER


def _call(name: str, **arguments) -> str:
    return "<tool_call>\n" + json.dumps({"name": name, "arguments": arguments}) + "\n</tool_call>"


def judges_fake_responder(system: str, prompt: str, model: str, effort: Optional[str]) -> str:
    if EARLIER_REPLY_HEADER in prompt:
        return "Done."
    turn = int(re.search(r"=== TURN (\d+) ===", prompt).group(1))
    judge = re.search(r"You are judge (\S+)\.", prompt).group(1)
    calls = [_call("score_output", output_id=oid, score=6, reason="adequate") for oid in re.findall(r"- output_id (\S+),", prompt)]
    for board in sorted(set(int(b) for b in re.findall(r"^Board (\d+) ", prompt, flags=re.MULTILINE))):
        calls.append(_call("post_message", board_id=board, message=f"{judge} scored turn {turn}."))
    team = re.findall(r"^- (W\d+) \(model:", prompt, flags=re.MULTILINE)
    if turn == 2:
        calls.append(_call("pause_team"))
    if turn == 3:
        calls.append(_call("resume_team"))
    if turn == 4 and team:
        calls.append(_call("remove_worker", worker_id=team[0]))
    calls.append(_call("end_turn"))
    return "Scoring.\n" + "\n".join(calls)
