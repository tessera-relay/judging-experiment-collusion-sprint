from collusion_study.textcalls import (
    parse_tool_calls,
    render_request,
    render_tool_results,
    strip_invented_results,
    tool_instructions,
)

POST = {
    "type": "function",
    "function": {
        "name": "post_message",
        "description": "Post a communication message to agents on the blackboard",
        "parameters": {
            "type": "object",
            "properties": {"message": {"type": "string"}, "blackboard_id": {"type": "integer"}},
            "required": ["message"],
        },
    },
}


def test_single_call():
    calls = parse_tool_calls('I will post.\n<tool_call>\n{"name": "post_message", "arguments": {"message": "hi", "blackboard_id": 0}}\n</tool_call>')
    assert len(calls) == 1
    assert calls[0].error is None
    assert calls[0].name == "post_message"
    assert calls[0].arguments == {"message": "hi", "blackboard_id": 0}


def test_several_calls_keep_their_order():
    text = (
        '<tool_call>{"name": "a", "arguments": {}}</tool_call> then '
        '<tool_call>{"name": "b", "arguments": {"x": 1}}</tool_call>'
    )
    assert [c.name for c in parse_tool_calls(text)] == ["a", "b"]


def test_fenced_json_is_accepted():
    text = '<tool_call>\n```json\n{"name": "assign_task", "arguments": {"task_id": "T3"}}\n```\n</tool_call>'
    (call,) = parse_tool_calls(text)
    assert call.error is None and call.arguments == {"task_id": "T3"}


def test_missing_arguments_means_empty_arguments():
    (call,) = parse_tool_calls('<tool_call>{"name": "noop"}</tool_call>')
    assert call.error is None and call.arguments == {}


def test_unusable_blocks_are_reported_not_dropped():
    text = (
        "<tool_call>{not json}</tool_call>"
        '<tool_call>{"arguments": {}}</tool_call>'
        '<tool_call>{"name": "x", "arguments": [1, 2]}</tool_call>'
        '<tool_call>["x"]</tool_call>'
    )
    errors = [c.error for c in parse_tool_calls(text)]
    assert errors[0].startswith("invalid JSON")
    assert errors[1] == 'missing "name"'
    assert errors[2] == '"arguments" must be a JSON object'
    assert errors[3] == "the block must hold a JSON object"


def test_unclosed_block_is_an_error():
    calls = parse_tool_calls('<tool_call>{"name": "a", "arguments": {}}</tool_call> <tool_call>{"name": "b"')
    assert [c.error is None for c in calls] == [True, False]
    assert "not closed" in calls[1].error


def test_no_calls():
    assert parse_tool_calls("Nothing to do this round.") == []


def test_tool_instructions_list_each_tool_with_its_schema():
    text = tool_instructions([POST])
    assert '"name": "post_message"' in text
    assert '"blackboard_id"' in text
    assert "<tool_call>" in text
    assert tool_instructions([POST]) == text  # deterministic, so replay hashes are stable


def test_no_tools():
    assert "No tools are available" in tool_instructions([])


def test_first_step_is_the_user_prompt_alone():
    system, prompt = render_request(
        [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}], [POST]
    )
    assert system.startswith("SYS\n\n# Tools")
    assert prompt == "USER"


def test_later_steps_carry_the_turn_so_far():
    context = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "USER"},
        {"role": "assistant", "content": "REPLY1"},
        {"role": "tool_results", "content": render_tool_results([("post_message", {"ok": True})])},
    ]
    _, prompt = render_request(context, [POST])
    assert prompt.startswith("USER\n\n=== Your earlier reply in this turn ===\nREPLY1")
    assert '<tool_result name="post_message">\n{"ok": true}\n</tool_result>' in prompt
    # The prompt ends on the tool results, with no instruction the original never sent.
    assert prompt.endswith("</tool_result>")
    assert "Continue this turn" not in prompt


def test_tool_results_keep_the_environments_key_order():
    rendered = render_tool_results([("get_tasks", {"zeta": 1, "alpha": 2})])
    assert rendered.index('"zeta"') < rendered.index('"alpha"')


def test_results_the_model_invented_are_cut_off():
    text = 'Posting now.\n<tool_call>{"name": "post_message", "arguments": {}}</tool_call>\n<tool_result name="post_message">{"ok": true}</tool_result>'
    assert strip_invented_results(text) == 'Posting now.\n<tool_call>{"name": "post_message", "arguments": {}}</tool_call>'
    assert strip_invented_results("No results here.") == "No results here."
