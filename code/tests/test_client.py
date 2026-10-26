import asyncio
import json

import pytest

from collusion_study.backend import CallLog, FakeBackend, ModelCaller, sha256_text
from collusion_study.colosseum_client import ClaudeCLIClient, ReplayCursor, ReplayDivergence

POST = {
    "type": "function",
    "function": {
        "name": "post_message",
        "description": "Post a message",
        "parameters": {"type": "object", "properties": {"message": {"type": "string"}}},
    },
}
PARAMS = {"model": "ignored", "tools": [POST]}


def scripted(system, prompt, model, effort):
    """Deterministic fake replies: one post per turn, then a reply with no tool call."""
    if "=== Your earlier reply in this turn ===" in prompt:
        return "Done for this round."
    tag = sha256_text(prompt)[:8]
    return f'Posting.\n<tool_call>{{"name": "post_message", "arguments": {{"message": "note {tag}"}}}}</tool_call>'


def make_caller(path, responder=scripted):
    return ModelCaller(FakeBackend(responder), CallLog(path))


class Tools:
    def __init__(self):
        self.calls = []

    async def __call__(self, name, args):
        self.calls.append((name, args))
        return {"status": "posted", "n": len(self.calls)}


def one_turn(client, agent, round_num, user_prompt, tools, steps=2):
    """A minimal copy of Terrarium's per-turn loop: call, run tools, call again."""
    client.set_meta_context(agent_name=agent, phase="planning", iteration=1, round_num=round_num)
    context = client.init_context(f"You are {agent}.", user_prompt)
    response, _ = client.generate_response(input=context, params=PARAMS)
    for step in range(steps):
        _, context, _ = asyncio.run(client.process_tool_calls(response, context, tools))
        if step >= steps - 1:
            break
        response, _ = client.generate_response(input=context, params=PARAMS)
    return context


def episode(client, tools, rounds=(1, 2, 3)):
    for r in rounds:
        for agent in ("A", "B"):
            one_turn(client, agent, r, f"Round {r}. Board so far: {len(tools.calls)} posts.", tools)


def read_log(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_tools_run_and_the_turn_is_carried_forward(tmp_path):
    client = ClaudeCLIClient(make_caller(tmp_path / "calls.jsonl"), model="claude-test")
    tools = Tools()
    context = one_turn(client, "A", 1, "Round 1.", tools)
    assert tools.calls and tools.calls[0][0] == "post_message"
    roles = [item["role"] for item in context]
    assert roles == ["system", "user", "assistant", "tool_results", "assistant"]
    records = read_log(tmp_path / "calls.jsonl")
    assert [r["meta_step"] for r in records] == [1, 2]
    assert records[0]["meta_agent"] == "A" and records[0]["meta_round"] == 1


def test_invalid_call_is_reported_back_and_not_run(tmp_path):
    caller = make_caller(tmp_path / "calls.jsonl", lambda *a: "<tool_call>{oops}</tool_call>")
    client = ClaudeCLIClient(caller, model="claude-test")
    client.set_meta_context(agent_name="A", phase="planning", iteration=1, round_num=1)
    context = client.init_context("S", "U")
    response, _ = client.generate_response(input=context, params=PARAMS)
    tools = Tools()
    executed, context, step_tools = asyncio.run(client.process_tool_calls(response, context, tools))
    assert executed == 0 and tools.calls == []
    assert step_tools[0].startswith("INVALID_TOOL_CALL")
    assert "Tool call not run: invalid JSON" in context[-1]["content"]


def test_usage_is_summed():
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    class R:
        usage = {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7}

    ClaudeCLIClient.get_usage(R(), total)
    ClaudeCLIClient.get_usage(R(), total)
    assert total == {"prompt_tokens": 30, "completion_tokens": 14, "total_tokens": 44}


def before(meta):
    return meta.get("round") is not None and meta["round"] <= 2


def after(meta):
    return meta.get("round") is not None and meta["round"] >= 3


def test_branch_replays_the_record_then_goes_live(tmp_path):
    base_log = tmp_path / "base.jsonl"
    episode(ClaudeCLIClient(make_caller(base_log), model="claude-test"), Tools())
    base = read_log(base_log)
    assert len(base) == 12  # 3 rounds x 2 agents x 2 calls

    live_prompts = []

    def counting(system, prompt, model, effort):
        live_prompts.append(prompt)
        return scripted(system, prompt, model, effort)

    branch_log = tmp_path / "branch.jsonl"
    cursor = ReplayCursor.from_call_log(base_log, before, after)
    assert cursor.remaining() == 8
    client = ClaudeCLIClient(make_caller(branch_log, counting), model="claude-test", replay=cursor)
    episode(client, Tools())
    assert len(live_prompts) == 4  # only round 3 is live
    branch = read_log(branch_log)
    replayed = [r for r in branch if r["replayed"]]
    assert len(replayed) == 8
    assert [r["replay_of_call_id"] for r in replayed] == [r["call_id"] for r in base[:8]]
    assert [r["request_sha256"] for r in branch] == [r["request_sha256"] for r in base]


def test_a_changed_history_is_caught(tmp_path):
    base_log = tmp_path / "base.jsonl"
    episode(ClaudeCLIClient(make_caller(base_log), model="claude-test"), Tools())
    cursor = ReplayCursor.from_call_log(base_log, before, after)
    client = ClaudeCLIClient(make_caller(tmp_path / "b.jsonl"), model="claude-test", replay=cursor)
    with pytest.raises(ReplayDivergence):
        one_turn(client, "A", 1, "Round 1. A different board.", Tools())


def test_a_different_model_is_caught(tmp_path):
    base_log = tmp_path / "base.jsonl"
    episode(ClaudeCLIClient(make_caller(base_log), model="claude-test"), Tools())
    cursor = ReplayCursor.from_call_log(base_log, before, after)
    client = ClaudeCLIClient(make_caller(tmp_path / "b.jsonl"), model="claude-other", replay=cursor)
    with pytest.raises(ReplayDivergence):
        episode(client, Tools())


def test_no_live_call_before_the_handover(tmp_path):
    base_log = tmp_path / "base.jsonl"
    episode(ClaudeCLIClient(make_caller(base_log), model="claude-test"), Tools())
    cursor = ReplayCursor.from_call_log(base_log, before, after)
    cursor.records = cursor.records[:5]  # a recording that stops early
    client = ClaudeCLIClient(make_caller(tmp_path / "b.jsonl"), model="claude-test", replay=cursor)
    with pytest.raises(ReplayDivergence, match="before the handover"):
        episode(client, Tools())


def test_model_resolver_switches_the_seat_after_the_handover(tmp_path):
    def resolver(meta):
        if meta["agent"] == "B" and meta["round"] >= 3:
            return "claude-newcomer", "low"
        return "claude-test", None

    log = tmp_path / "calls.jsonl"
    episode(ClaudeCLIClient(make_caller(log), model="claude-test", model_resolver=resolver), Tools())
    used = {(r["meta_agent"], r["meta_round"], r["model_requested"], r["effort_requested"]) for r in read_log(log)}
    assert ("B", 3, "claude-newcomer", "low") in used
    assert ("B", 2, "claude-test", None) in used
    assert ("A", 3, "claude-test", None) in used


def test_ending_the_turn_without_a_tool_call_saves_calls(tmp_path):
    log = tmp_path / "calls.jsonl"
    client = ClaudeCLIClient(
        make_caller(log, lambda *a: "Nothing to add."), model="claude-test", end_turn_without_tool_call=True
    )
    one_turn(client, "A", 1, "Round 1.", Tools(), steps=3)
    assert len(read_log(log)) == 1


def test_inside_terrariums_agent_loop(tmp_path):
    """The client drives Terrarium's real BaseAgent loop: post, then an env action that ends the turn."""
    BaseAgent = pytest.importorskip("terrarium.agents.base").BaseAgent

    ASSIGN = {
        "type": "function",
        "function": {"name": "assign_task", "description": "Take a task", "parameters": {"type": "object"}},
    }

    def responder(system, prompt, model, effort):
        if "=== Tool results ===" not in prompt:
            return '<tool_call>{"name": "post_message", "arguments": {"message": "I take T1", "blackboard_id": 0}}</tool_call>'
        return '<tool_call>{"name": "assign_task", "arguments": {"task_id": "T1"}}</tool_call>'

    class Discovery:
        def get_tools_for_environment(self, env_name, phase):
            return [ASSIGN]

        def get_env_tool_names(self, env_name):
            return {"assign_task"}

        def get_blackboard_tool_names(self):
            return {"post_message"}

        def get_tools_for_blackboard(self, phase):
            return [POST]

    class Protocol:
        def __init__(self):
            self.seen = []

        async def blackboard_handle_tool_call(self, name, agent, args, phase=None, iteration=None):
            self.seen.append(("blackboard", name, args))
            return {"result": "posted"}

        async def environment_handle_tool_call(self, name, agent, args, phase=None, iteration=None):
            self.seen.append(("env", name, args))
            return {"result": {"state_updates": {"T1": agent}}}

    log = tmp_path / "calls.jsonl"
    client = ClaudeCLIClient(make_caller(log, responder), model="claude-test")
    agent = BaseAgent(client, "Alice", model_name="claude-test", max_conversation_steps=3, environment_name="JiraTicketEnvironment")
    agent.toolset_discovery = Discovery()
    agent.communication_protocol = Protocol()
    agent.set_meta_context(agent_name="Alice", phase="execution", iteration=1, round_num=None)
    client.set_meta_context(agent_name="Alice", phase="execution", iteration=1, round_num=None)
    out = asyncio.run(agent._multi_step_response_generation(system_prompt="S", user_prompt="U"))
    assert [s[:2] for s in agent.communication_protocol.seen] == [("blackboard", "post_message"), ("env", "assign_task")]
    assert out["conversation_steps"] == 2  # the env action committed state, so the turn ended
    assert len(read_log(log)) == 2
