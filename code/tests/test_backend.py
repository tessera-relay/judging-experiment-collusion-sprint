import json

import pytest

from collusion_study.backend import (
    CHILD_ENV_FIXED,
    BackendError,
    CHILD_ENV_KEEP,
    CallBudget,
    CallBudgetExceeded,
    CallLog,
    ClaudeCLIBackend,
    FakeBackend,
    ModelCaller,
    RunAborted,
    child_env,
)


def test_child_env_keeps_only_the_allowlist_and_pins_the_fixed_settings(monkeypatch):
    inherited = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OTHER")
    for name in inherited:
        monkeypatch.setenv(name, "x")
    monkeypatch.setenv("HOME", "/home/test")
    monkeypatch.setenv("MAX_THINKING_TOKENS", "32000")
    env = child_env()
    assert set(env) <= set(CHILD_ENV_KEEP) | set(CHILD_ENV_FIXED)
    assert not any(name in env for name in inherited)
    assert env["HOME"] == "/home/test"
    assert {name: env[name] for name in CHILD_ENV_FIXED} == CHILD_ENV_FIXED


def test_command_shuts_out_the_operators_own_setup():
    cmd = ClaudeCLIBackend(claude_bin="claude").command("SYSTEM", "claude-sonnet-4-5", None)
    for flag in ("--safe-mode", "--disable-slash-commands", "--strict-mcp-config", "--no-session-persistence"):
        assert flag in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "project"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-4-5"
    assert "--effort" not in cmd


def _statuses(path):
    return [json.loads(line)["status"] for line in path.read_text().splitlines()]


def test_a_failed_call_stops_the_run_and_both_are_logged(tmp_path):
    def broken(system, prompt, model, effort):
        raise RuntimeError("boom")

    caller = ModelCaller(FakeBackend(broken), CallLog(tmp_path / "calls.jsonl"), CallBudget(None))
    with pytest.raises(RuntimeError):
        caller.call(system="S", prompt="P", model="m", effort=None, meta={})
    with pytest.raises(RunAborted):
        caller.call(system="S", prompt="P", model="m", effort=None, meta={})
    assert _statuses(tmp_path / "calls.jsonl") == ["failed", "refused_after_failure"]
    assert caller.failed_calls == 1


def test_the_call_cap_is_logged_when_reached(tmp_path):
    caller = ModelCaller(FakeBackend(lambda *a: "Done."), CallLog(tmp_path / "calls.jsonl"), CallBudget(1))
    caller.call(system="S", prompt="P", model="m", effort=None, meta={})
    with pytest.raises(CallBudgetExceeded):
        caller.call(system="S", prompt="P", model="m", effort=None, meta={})
    assert _statuses(tmp_path / "calls.jsonl") == ["ok", "call_cap_reached"]
    assert caller.budget.used == 1


def test_every_call_uses_the_same_empty_working_directory(tmp_path):
    first, second = ClaudeCLIBackend(claude_bin="claude"), ClaudeCLIBackend(claude_bin="claude")
    assert first.workdir == second.workdir
    busy = ClaudeCLIBackend(claude_bin="claude", workdir=tmp_path)
    (tmp_path / "CLAUDE.md").write_text("stray")
    with pytest.raises(BackendError):
        busy.complete(system="S", prompt="P", model="m", effort=None)
