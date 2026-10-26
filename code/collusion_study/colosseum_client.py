"""A Terrarium/Colosseum LLM client that calls Claude through `claude -p` with text tool calls.

It plugs into Colosseum's agent loop in place of the OpenAI or Anthropic clients:
Terrarium's BaseAgent calls generate_response, then process_tool_calls, up to
max_conversation_steps times per turn, exactly as with the reference clients.

Replay: a branch of a recorded run feeds the recorded responses back, in order,
until the handover point. Every replayed request must hash to the recorded one,
so a branch can never silently drift from the history it claims to share.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .backend import ModelCaller, request_sha256
from .textcalls import TextResponse, parse_tool_calls, render_request, render_tool_results, strip_invented_results

try:  # Terrarium is only needed when the client runs inside Colosseum.
    from llm_server.clients.abstract_client import AbstractClient as _Base
except ImportError:  # pragma: no cover - offline unit tests without terrarium installed
    _Base = object


class ReplayDivergence(RuntimeError):
    """A branch asked for something other than what the recorded run asked for."""


class ReplayCursor:
    """Recorded responses for the part of a run before the handover, consumed in order."""

    def __init__(self, records: List[Dict[str, Any]], is_after_handover: Callable[[Dict[str, Any]], bool]):
        self.records = list(records)
        self.pos = 0
        self.is_after_handover = is_after_handover
        self._lock = threading.Lock()

    @classmethod
    def from_call_log(
        cls,
        path: Path,
        is_before_handover: Callable[[Dict[str, Any]], bool],
        is_after_handover: Callable[[Dict[str, Any]], bool],
    ) -> "ReplayCursor":
        """Read a run's call log and keep its successful calls from before the handover."""
        records = []
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                rec = json.loads(line)
                if rec.get("status") != "ok":
                    continue
                meta = {k[len("meta_"):]: v for k, v in rec.items() if k.startswith("meta_")}
                if is_before_handover(meta):
                    records.append(rec)
        return cls(records, is_after_handover)

    def remaining(self) -> int:
        with self._lock:
            return len(self.records) - self.pos

    def take(self, req_hash: str, meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """The next recorded call if replay is still running, else None."""
        with self._lock:
            if self.pos >= len(self.records):
                return None
            rec = self.records[self.pos]
            if rec.get("request_sha256") != req_hash:
                recorded_meta = {k: v for k, v in rec.items() if k.startswith("meta_")}
                raise ReplayDivergence(
                    f"request {self.pos} differs from the recording: now {meta}, recorded {recorded_meta}"
                )
            self.pos += 1
            return rec

    def check_live(self, meta: Dict[str, Any]) -> None:
        """A live call is only allowed once the recording is used up and the handover is reached."""
        if self.remaining():
            raise ReplayDivergence(f"live call requested while {self.remaining()} recorded calls remain: {meta}")
        if not self.is_after_handover(meta):
            raise ReplayDivergence(f"the recording ended before the handover point: {meta}")


ModelResolver = Callable[[Dict[str, Any]], Tuple[str, Optional[str]]]


class ClaudeCLIClient(_Base):
    """One Colosseum agent's client. Each model call goes through a shared ModelCaller."""

    def __init__(
        self,
        caller: ModelCaller,
        model: str,
        effort: Optional[str] = None,
        *,
        replay: Optional[ReplayCursor] = None,
        model_resolver: Optional[ModelResolver] = None,
        end_turn_without_tool_call: bool = True,
        run_meta: Optional[Dict[str, Any]] = None,
    ):
        self.caller = caller
        self.model = model
        self.effort = effort
        self.replay = replay
        self.model_resolver = model_resolver
        # After a reply with no tool call, Colosseum's Anthropic client calls again with the
        # history ending on that reply, so the model continues its own finished answer, which
        # usually adds nothing. `claude -p` cannot do that, so by default the turn ends instead.
        self.end_turn_without_tool_call = end_turn_without_tool_call
        self.run_meta = dict(run_meta or {})
        self._meta: Dict[str, Any] = {}
        self._step = 0

    def set_meta_context(self, agent_name: str, phase: str, iteration: Optional[int] = None, round_num: Optional[int] = None) -> None:
        self._meta = {"agent": agent_name, "phase": phase, "iteration": iteration, "round": round_num}
        self._step = 0

    @staticmethod
    def init_context(system_prompt: str, user_prompt: str) -> List[Dict[str, Any]]:
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

    @staticmethod
    def get_usage(response: Any, current_usage: Dict[str, int]) -> Dict[str, int]:
        usage = getattr(response, "usage", None) or {}
        prompt = sum(int(usage.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        completion = int(usage.get("output_tokens") or 0)
        current_usage["prompt_tokens"] = current_usage.get("prompt_tokens", 0) + prompt
        current_usage["completion_tokens"] = current_usage.get("completion_tokens", 0) + completion
        current_usage["total_tokens"] = current_usage["prompt_tokens"] + current_usage["completion_tokens"]
        return current_usage

    def _resolve_model(self, meta: Dict[str, Any]) -> Tuple[str, Optional[str]]:
        if self.model_resolver is not None:
            return self.model_resolver(meta)
        return self.model, self.effort

    def generate_response(self, input: List[Any], params: Dict[str, Any]) -> Tuple[TextResponse, str]:
        self._step += 1
        meta = {**self.run_meta, **self._meta, "step": self._step}
        if self.end_turn_without_tool_call and input and input[-1].get("role") == "assistant":
            # The previous reply had no tool call, so this agent's turn is over. No model call.
            return TextResponse(text="", skipped=True, model=self.model), ""
        system, prompt = render_request(input, params.get("tools"))
        model, effort = self._resolve_model(meta)
        req_hash = request_sha256(system, prompt, model, effort)
        try:
            recorded = self.replay.take(req_hash, meta) if self.replay is not None else None
            if recorded is None and self.replay is not None:
                self.replay.check_live(meta)
        except ReplayDivergence as exc:
            # Stop the episode: later calls fail at once instead of drifting from the recording.
            self.caller.fatal = self.caller.fatal or f"ReplayDivergence: {exc}"
            raise
        if recorded is not None:
            self.caller.record_replay(system=system, prompt=prompt, model=model, effort=effort, meta=meta, source=recorded)
            text = strip_invented_results(str(recorded.get("text") or ""))
            response = TextResponse(
                text=text,
                tool_calls=parse_tool_calls(text),
                usage={},
                model=recorded.get("model_id") or model,
                request_sha256=req_hash,
                replayed=True,
            )
            return response, text
        result = self.caller.call(system=system, prompt=prompt, model=model, effort=effort, meta=meta)
        text = strip_invented_results(result.text)
        response = TextResponse(
            text=text,
            tool_calls=parse_tool_calls(text),
            usage=result.usage,
            model=result.model_id or model,
            request_sha256=req_hash,
        )
        return response, text

    async def process_tool_calls(self, response: Any, context: List[Any], execute_tool_callback: Any) -> Tuple[int, List[Any], List[str]]:
        if getattr(response, "skipped", False):
            return 0, context, []
        context.append({"role": "assistant", "content": response.text})
        results: List[Tuple[str, Any]] = []
        step_tools: List[str] = []
        executed = 0
        for call in response.tool_calls:
            if call.error:
                results.append((call.name or "unknown", {"error": f"Tool call not run: {call.error}."}))
                step_tools.append(f"INVALID_TOOL_CALL -- {json.dumps(call.raw[:300], ensure_ascii=False)}")
                continue
            step_tools.append(f"{call.name} -- {json.dumps(call.arguments, ensure_ascii=False)}")
            result = await execute_tool_callback(call.name, call.arguments)
            results.append((call.name, result))
            executed += 1
        if results:
            context.append({"role": "tool_results", "content": render_tool_results(results)})
        return executed, context, step_tools
