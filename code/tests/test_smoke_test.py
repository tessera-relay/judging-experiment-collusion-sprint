import json

from collusion_study.smoke_test import compare_with_baseline, describe_call, model_unavailable, run_checks


def record(call_id, status="ok", prompt="P" * 4000, text='<tool_call>{"name": "post_message", "arguments": {"message": "hi"}}</tool_call>', blocks=("text",), model="claude-sonnet-4-5-20250929", output_tokens=200, key="none"):
    builtin = [{"name": "cc-plugin-telemetry", "path": "builtin", "source": "cc-plugin-telemetry@builtin"}]
    init = {"type": "system", "subtype": "init", "model": "claude-sonnet-4-5", "apiKeySource": key, "tools": [], "mcp_servers": [], "plugins": builtin, "skills": []}
    reply = {"type": "assistant", "message": {"model": model, "content": [{"type": b} for b in blocks]}}
    return {
        "call_id": call_id,
        "request_sha256": "sha-" + call_id,
        "status": status,
        "system": "S" * 2000,
        "prompt": prompt,
        "text": text,
        "usage": {"input_tokens": 300 + (2000 + len(prompt)) // 4, "output_tokens": output_tokens},
        "raw_events": [init, reply, {"type": "result", "duration_api_ms": 4000}],
        "failed_attempts": [],
        "meta_agent": "Agent 1",
    }


def checks_for(records, model="claude-sonnet-4-5"):
    return run_checks([describe_call(r) for r in records], records, model)


def test_three_clean_calls_pass_every_check():
    records = [record("a"), record("b", prompt="P" * 6000), record("c"), {"call_id": "d", "status": "call_cap_reached"}]
    result = checks_for(records)
    assert all(result["checks"].values()), result["checks"]
    assert result["measures"]["calls_with_a_tool_call"] == 3
    assert result["measures"]["context_overhead_estimate_tokens"] == 300.0


def test_each_problem_fails_its_own_check():
    assert not checks_for([record("a", key="ANTHROPIC_API_KEY")] * 3)["checks"]["subscription_login"]
    assert not checks_for([record("a", blocks=("thinking", "text"))] * 3)["checks"]["no_thinking"]
    assert not checks_for([record("a", output_tokens=1501)] * 3)["checks"]["output_within_cap"]
    assert not checks_for([record("a", model="claude-sonnet-5-5")] * 3)["checks"]["model_accepted"]
    assert not checks_for([record("a", text="<tool_call>{not json</tool_call>")] * 3)["checks"]["tool_calls_parse"]
    assert not checks_for([record("a"), record("b")])["checks"]["three_calls_ok"]
    padded = record("a")
    padded["usage"]["input_tokens"] += 1400  # what the operator's CLAUDE.md would add
    assert not checks_for([padded, record("b"), record("c")])["checks"]["no_large_added_context"]


def test_a_user_plugin_fails_the_plugin_check():
    extra = record("a")
    extra["raw_events"][0]["plugins"].append({"name": "chrome-devtools-mcp", "path": "/Users/x/.claude/plugins/c", "source": "user"})
    assert not checks_for([extra, record("b"), record("c")])["checks"]["only_builtin_plugins_no_skills"]


def test_identical_requests_are_compared_with_the_baseline(tmp_path):
    before = [describe_call(record("a")), describe_call(record("b"))]
    (tmp_path / "smoke_summary.json").write_text(json.dumps({"calls": before}))
    now = record("a")
    now["usage"]["input_tokens"] -= 200
    rows = compare_with_baseline([describe_call(now)], tmp_path / "smoke_summary.json")
    assert [r["tokens_removed"] for r in rows] == [200]


def test_only_a_refused_model_triggers_the_fallback():
    refused = {"status": "failed", "error": "BackendError: There's an issue with the selected model (claude-sonnet-4-5)."}
    timeout = {"status": "failed", "error": "BackendError: timed out after 300s"}
    assert model_unavailable([refused]) and not model_unavailable([timeout]) and not model_unavailable([record("a")])
