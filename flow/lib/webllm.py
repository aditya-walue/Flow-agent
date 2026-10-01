# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Browser-run models (WebLLM).

A Flow Model whose id starts with `webllm/` is not called from the server. Each model
call is handed to the chat panel that started the run: the SSE stream carries a
`BrowserRequest` with plain chat messages, the panel runs the model locally on WebGPU
(@mlc-ai/web-llm), and posts the raw reply back through `submit_browser_reply`. The
server side of the stream waits for that reply, then parses it into a ChatResponse so
the agent loop — tools, approvals, persistence — runs exactly as for a hosted model.

WebLLM has no reliable native tool calling across models, so tools are described in
the system prompt and called with `<tool_call>{json}</tool_call>` blocks (the Hermes /
Qwen 2.5 convention). The stored transcript keeps the standard OpenAI shape; the
plain-text rendering here is only what the browser model sees.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any

import frappe
from frappe import _

from flow.lib.model import ChatResponse, ToolCall, _build_tool_call

BROWSER_PROVIDER = "webllm"
# Keep in sync with DEFAULT_CONTEXT_WINDOW in frontend/src/lib/webllm.js.
DEFAULT_CONTEXT_WINDOW = 16384

# The first call in a browser downloads the model weights (GBs), so allow minutes.
REPLY_TIMEOUT = 900
POLL_INTERVAL = 0.2
# Keeps the otherwise idle SSE connection alive through proxies while the browser works.
HEARTBEAT_INTERVAL = 15

# A block ends at its closing tag — or, when the model runs on without one, at the next call or
# at a <tool_response> it starts to invent.
TOOL_CALL_PATTERN = re.compile(r"<tool_call>\s*(.*?)\s*(?=</tool_call>|<tool_call>|<tool_response|$)", re.DOTALL)
# An unquoted object key: `name:` (only treated as a key right after `{` or `,`).
BARE_KEY_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*:")


@dataclass
class BrowserRequest:
	"""Stream event: run one model call in the browser and post the reply back under `id`."""

	id: str
	model: str
	messages: list[dict[str, str]]
	params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Heartbeat:
	"""Stream event with no content, sent while waiting on the browser."""


def is_browser_model(model_id: str | None) -> bool:
	return (model_id or "").split("/", 1)[0] == BROWSER_PROVIDER


def browser_context_window(params: dict[str, Any]) -> int:
	"""The context window the panel loads a browser model with: the model's
	`context_window_size` param, else the panel default (frontend/src/lib/webllm.js)."""
	return int(params.get("context_window_size") or DEFAULT_CONTEXT_WINDOW)


def chat_stream(
	model_id: str,
	messages: list[dict[str, Any]],
	tools: list[dict[str, Any]] | None,
	params: dict[str, Any] | None,
) -> Generator[BrowserRequest | Heartbeat, None, ChatResponse]:
	"""Hand one model call to the browser and wait for its reply. Same contract as
	`Model.chat(stream=True)`: yields stream items, returns the ChatResponse."""
	request_id = frappe.generate_hash(length=20)
	frappe.cache.set_value(
		_owner_key(request_id), frappe.session.user, expires_in_sec=REPLY_TIMEOUT + 60
	)
	yield BrowserRequest(
		id=request_id,
		model=model_id.split("/", 1)[1],
		messages=to_plain_messages(messages, tools),
		params=params or {},
	)

	reply = yield from _wait_for_reply(request_id)
	if reply.get("error"):
		raise RuntimeError(reply["error"])
	return parse_reply(reply.get("content") or "", reply.get("usage") or {})


def submit_reply(request_id: str, reply: dict[str, Any]) -> None:
	"""Store the browser's reply for the waiting stream. Only the user the request was
	issued to may answer it."""
	owner = frappe.cache.get_value(_owner_key(request_id), use_local_cache=False)
	if owner != frappe.session.user:
		frappe.throw(_("Unknown or expired model request."), frappe.PermissionError)
	frappe.cache.set_value(_reply_key(request_id), reply, expires_in_sec=300)


def _wait_for_reply(request_id: str) -> Generator[Heartbeat, None, dict[str, Any]]:
	key = _reply_key(request_id)
	deadline = time.monotonic() + REPLY_TIMEOUT
	next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL
	try:
		while time.monotonic() < deadline:
			# Bypass the per-request local cache: the reply lands from another request.
			reply = frappe.cache.get_value(key, use_local_cache=False)
			if reply is not None:
				return reply
			if time.monotonic() >= next_heartbeat:
				next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL
				yield Heartbeat()
			time.sleep(POLL_INTERVAL)
	finally:
		frappe.cache.delete_value([key, _owner_key(request_id)])
	raise TimeoutError(_("The browser model did not reply in time."))


def to_plain_messages(
	messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
) -> list[dict[str, str]]:
	"""Render an OpenAI-style transcript as system/user/assistant text messages, with the
	tool schemas in the system prompt and tool calls/results as tagged text. Consecutive
	messages of the same role are merged, since chat templates expect turns to alternate."""
	system_parts: list[str] = []
	out: list[dict[str, str]] = []

	def add(role: str, content: str) -> None:
		if out and out[-1]["role"] == role:
			out[-1]["content"] = f"{out[-1]['content']}\n\n{content}".strip()
		else:
			out.append({"role": role, "content": content})

	for message in messages:
		role = message.get("role")
		content = _text(message.get("content"))
		if role == "system":
			system_parts.append(content)
		elif role == "user":
			add("user", content)
		elif role == "assistant":
			calls = [
				"<tool_call>\n"
				+ json.dumps(
					{
						"name": tc["function"]["name"],
						"arguments": json.loads(tc["function"].get("arguments") or "{}"),
					}
				)
				+ "\n</tool_call>"
				for tc in message.get("tool_calls") or []
			]
			add("assistant", "\n".join(filter(None, [content, *calls])))
		elif role == "tool":
			add("user", f"<tool_response>\n{content}\n</tool_response>")

	if tools:
		system_parts.append(_tools_prompt(tools))
	if system_parts:
		out.insert(0, {"role": "system", "content": "\n\n".join(p for p in system_parts if p)})
	return out


def parse_reply(text: str, usage: dict[str, Any]) -> ChatResponse:
	"""Split a browser model's raw reply into prose and `<tool_call>` blocks."""
	tool_calls: list[ToolCall] = []
	for raw in TOOL_CALL_PATTERN.findall(text):
		call_id = f"call_{frappe.generate_hash(length=12)}"
		payload = _loads_lenient(raw)
		if not isinstance(payload, dict) or not isinstance(payload.get("name"), str):
			tool_calls.append(
				ToolCall(
					id=call_id,
					name="invalid_tool_call",
					arguments={},
					error=f"Malformed <tool_call> block: {raw[:200]}. Resend it as "
					'{"name": ..., "arguments": {...}} inside <tool_call></tool_call>.',
				)
			)
			continue
		arguments = payload.get("arguments")
		if isinstance(arguments, str):
			raw_args = arguments
		else:
			raw_args = json.dumps(arguments if arguments is not None else {})
		tool_calls.append(_build_tool_call(call_id, payload["name"], raw_args))

	content = text.split("<tool_call>", 1)[0].strip() or None
	return ChatResponse(
		content=content,
		tool_calls=tool_calls,
		finish_reason="tool_calls" if tool_calls else "stop",
		usage={
			key: int(usage.get(key) or 0) for key in ("prompt_tokens", "completion_tokens", "total_tokens")
		},
	)


def _loads_lenient(raw: str) -> Any:
	"""Parse a tool call's JSON, repairing the slips small models make: unbalanced brackets
	(`[{...}}}` with the list's `]` missing), unquoted keys (`{arguments: ...}`) and trailing
	commas — and trailing junk after a complete object (`{...}}��` when the model ran on).
	Returns None if still unparseable."""
	try:
		return json.loads(raw)
	except ValueError:
		pass
	first = _first_object(raw)
	if first is not None:
		return first
	try:
		return json.loads(_balance_brackets(_fix_keys_and_commas(raw)))
	except ValueError:
		return None


def _first_object(raw: str) -> Any:
	"""The complete JSON object at the start of `raw`, ignoring whatever follows it."""
	start = raw.find("{")
	if start == -1:
		return None
	try:
		value, _end = json.JSONDecoder().raw_decode(raw[start:])
	except ValueError:
		return None
	return value if isinstance(value, dict) else None


def _fix_keys_and_commas(raw: str) -> str:
	"""Quote bare object keys and drop trailing commas, leaving string contents untouched."""
	out: list[str] = []
	last = ""  # last non-whitespace character emitted
	i, n = 0, len(raw)
	while i < n:
		ch = raw[i]
		if ch == '"':
			end = _string_end(raw, i)
			out.append(raw[i:end])
			last = '"'
			i = end
			continue
		if ch == ",":
			nxt = raw[i + 1 :].lstrip()
			if nxt[:1] in ("}", "]"):
				i += 1
				continue
		match = BARE_KEY_PATTERN.match(raw, i)
		if match and last in ("{", ","):
			out.append(f'"{match.group(1)}"')
			last = '"'
			i = match.end(1)
			continue
		out.append(ch)
		if not ch.isspace():
			last = ch
		i += 1
	return "".join(out)


def _string_end(raw: str, start: int) -> int:
	"""Index just past the JSON string starting at `start` (or the end of `raw`)."""
	i = start + 1
	while i < len(raw):
		if raw[i] == "\\":
			i += 2
			continue
		if raw[i] == '"':
			return i + 1
		i += 1
	return len(raw)


def _balance_brackets(raw: str) -> str:
	"""Insert missing closers where a closer doesn't match the innermost open bracket, and
	append any still open at the end. Only closers are ever added; strings are skipped."""
	closer = {"{": "}", "[": "]"}
	stack: list[str] = []
	out: list[str] = []
	in_string = escaped = False
	for ch in raw:
		if in_string:
			if escaped:
				escaped = False
			elif ch == "\\":
				escaped = True
			elif ch == '"':
				in_string = False
		elif ch == '"':
			in_string = True
		elif ch in closer:
			stack.append(ch)
		elif ch in "}]":
			# Close inner brackets the model forgot until this closer matches one that's open.
			while stack and closer[stack[-1]] != ch and ch in (closer[b] for b in stack):
				out.append(closer[stack.pop()])
			if stack and closer[stack[-1]] == ch:
				stack.pop()
			else:
				continue  # stray closer with nothing to close: drop it
		out.append(ch)
	out.extend(closer[b] for b in reversed(stack))
	return "".join(out)


def _tools_prompt(tools: list[dict[str, Any]]) -> str:
	schemas = "\n".join(json.dumps(tool) for tool in tools)
	return (
		"# Tools\n\n"
		"You may call one or more functions to assist with the user query.\n\n"
		"You are provided with function signatures within <tools></tools> XML tags:\n"
		f"<tools>\n{schemas}\n</tools>\n\n"
		"For each function call, return a json object with function name and arguments "
		"within <tool_call></tool_call> XML tags:\n"
		'<tool_call>\n{"name": <function-name>, "arguments": <args-json-object>}\n</tool_call>\n\n'
		"Function results are given back to you within <tool_response></tool_response> tags."
	)


def _text(content: Any) -> str:
	"""Message content as text; multi-part content keeps only its text parts."""
	if content is None:
		return ""
	if isinstance(content, str):
		return content
	if isinstance(content, list):
		return "\n".join(
			part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text"
		)
	return str(content)


def _reply_key(request_id: str) -> str:
	return f"flow:webllm:reply:{request_id}"


def _owner_key(request_id: str) -> str:
	return f"flow:webllm:owner:{request_id}"
