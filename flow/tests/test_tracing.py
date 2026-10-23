# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from flow.lib import tracing
from flow.lib.agent import Agent, Done
from flow.lib.model import ChatResponse, ToolCall
from flow.lib.tool import tool
from flow.tests.test_ai_agent import FakeModel


class FakeClient:
	"""Records what Opik would be sent: spans as they finish, then the trace once at the end."""

	def __init__(self):
		self.traces = []
		self.spans = []

	def span(self, **kwargs):
		self.spans.append(kwargs)

	def trace(self, **kwargs):
		self.traces.append(kwargs)


@tool
def echo(text: str) -> str:
	"""Return the text."""
	return text


def _call_then_answer():
	return FakeModel(
		[
			ChatResponse(content=None, tool_calls=[ToolCall(id="c1", name="echo", arguments={"text": "hi"})]),
			ChatResponse(content="done"),
		]
	)


class TestTracing(IntegrationTestCase):
	def setUp(self):
		self.client = FakeClient()
		self.addCleanup(patch.object(tracing, "_client", return_value=self.client).start().stop)
		frappe.flags.flow_opik_in_tests = True
		self.addCleanup(setattr, frappe.flags, "flow_opik_in_tests", False)
		self.conf = patch.dict(frappe.local.conf, {"flow_opik_enabled": 1})
		self.conf.start()
		self.addCleanup(self.conf.stop)

	def test_off_in_other_tests(self):
		frappe.flags.flow_opik_in_tests = False
		Agent(model=_call_then_answer(), tools=[echo]).run("hello")
		self.assertEqual(self.client.traces, [])

	def test_off_by_default(self):
		with patch.dict(frappe.local.conf, {"flow_opik_enabled": 0}):
			Agent(model=_call_then_answer(), tools=[echo]).run("hello")
		self.assertEqual(self.client.traces, [])

	def test_run_records_trace_with_model_and_tool_spans(self):
		agent = Agent(model=_call_then_answer(), tools=[echo], name="tester")
		agent.trace_context = {"session": "S-1", "run": "R-1"}
		agent.run("hello")

		(trace,) = self.client.traces
		self.assertEqual(trace["name"], "tester")
		self.assertEqual(trace["thread_id"], "S-1")
		self.assertEqual(trace["metadata"]["run"], "R-1")
		self.assertEqual(trace["output"], {"value": "done"})
		self.assertEqual([s["name"] for s in self.client.spans], ["model", "echo", "model"])
		self.assertEqual([s["type"] for s in self.client.spans], ["llm", "tool", "llm"])
		self.assertEqual(self.client.spans[1]["input"], {"text": "hi"})
		# Spans belong to the trace sent at the end.
		self.assertEqual({s["trace_id"] for s in self.client.spans}, {trace["id"]})

	def test_trace_input_is_the_question_not_the_final_transcript(self):
		Agent(model=_call_then_answer(), tools=[echo]).run("hello")
		(trace,) = self.client.traces
		self.assertEqual([m["content"] for m in trace["input"]["messages"] if m["role"] == "user"], ["hello"])
		self.assertEqual(trace["input"]["messages"][-1]["role"], "user")

	def test_model_span_lists_the_tools_offered(self):
		Agent(model=_call_then_answer(), tools=[echo]).run("hello")
		self.assertEqual(self.client.spans[0]["metadata"], {"tools_offered": ["echo"]})

	def test_stream_records_trace(self):
		events = list(Agent(model=_call_then_answer(), tools=[echo]).run("hello", stream=True))
		self.assertIsInstance(events[-1], Done)
		(trace,) = self.client.traces
		self.assertEqual([s["name"] for s in self.client.spans], ["model", "echo", "model"])
		self.assertEqual(trace["output"], {"value": "done"})

	def test_failed_run_sends_trace_with_error(self):
		with self.assertRaises(AssertionError):
			Agent(model=FakeModel([]), tools=[echo]).run("hello")
		(trace,) = self.client.traces
		self.assertEqual(self.client.spans[0]["error_info"]["exception_type"], "AssertionError")
		self.assertEqual(trace["error_info"]["exception_type"], "AssertionError")

	def test_content_capture_can_be_turned_off(self):
		with patch.dict(frappe.local.conf, {"flow_opik_capture_content": 0}):
			Agent(model=_call_then_answer(), tools=[echo]).run("secret question")
		(trace,) = self.client.traces
		self.assertEqual(trace["input"], {"captured": False})
		self.assertEqual(self.client.spans[1]["input"], {"captured": False})
		self.assertNotIn("secret", repr(self.client.spans) + repr(trace))

	def test_opik_errors_never_reach_the_chat(self):
		with (
			patch.object(self.client, "trace", side_effect=RuntimeError("opik down")),
			patch.object(self.client, "span", side_effect=RuntimeError("opik down")),
		):
			result = Agent(model=_call_then_answer(), tools=[echo]).run("hello")
		self.assertEqual(result.output, "done")
