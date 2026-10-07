# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

import json
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests import IntegrationTestCase

from flow.lib import laya_router

TOOLS = {"count", "creation_steps", "error_diagnosis", "small_talk", "required_values", "create", "update"}


def _laya(intent, confidence, asks_count=0.0):
	"""A fake Laya Router returning one fixed prediction."""
	router = MagicMock()
	router.predict.return_value = {
		"answers": {
			"intent": {
				"type": "choice",
				"choice": intent,
				"probabilities": {intent: confidence, "OTHER": round(1 - confidence, 4)},
				"confidence": confidence,
				"answer_confidence": confidence,
			},
			"asks_count": {"type": "noul", "noul": asks_count, "answer_confidence": max(asks_count, 1 - asks_count)},
			"complexity": {"type": "score", "score": 1.0, "answer_confidence": 0.5},
		},
		"routing": {"model": "english"},
	}
	return router


class TestLayaRouter(IntegrationTestCase):
	def setUp(self):
		self._conf = {k: frappe.conf.get(k) for k in ("flow_laya_enabled", "flow_laya_threshold")}
		frappe.conf.flow_laya_enabled = True
		frappe.conf.flow_laya_threshold = 0.8

	def tearDown(self):
		for k, v in self._conf.items():
			frappe.conf[k] = v
		frappe.db.rollback()

	def _route(self, router, text, tools=TOOLS):
		with patch.object(laya_router, "_router", return_value=router):
			return laya_router.route(text, tools)

	def _last_log(self):
		return frappe.get_last_doc("Flow Route Log")

	def test_disabled_does_nothing(self):
		frappe.conf.flow_laya_enabled = False
		router = _laya("TROUBLESHOOTING", 0.99)
		self.assertIsNone(self._route(router, "Something exploded"))
		router.predict.assert_not_called()

	def test_confident_troubleshooting_routes_to_diagnosis_and_logs(self):
		result = self._route(_laya("TROUBLESHOOTING", 0.93), "Something exploded on save")
		self.assertEqual(result, ("error_diagnosis", {"error": "Something exploded on save"}))
		log = self._last_log()
		self.assertEqual((log.outcome, log.intent, log.tool), ("Routed", "TROUBLESHOOTING", "error_diagnosis"))
		self.assertAlmostEqual(log.confidence, 0.93)
		self.assertEqual(json.loads(log.distribution)["TROUBLESHOOTING"], 0.93)

	def test_low_confidence_falls_back_to_the_chat_model(self):
		self.assertIsNone(self._route(_laya("HOWTO", 0.4), "how do I add a todo"))
		log = self._last_log()
		self.assertEqual(log.outcome, "Fallback")
		self.assertIn("below", log.reason)

	def test_howto_and_count_need_a_doctype(self):
		self.assertEqual(self._route(_laya("HOWTO", 0.9), "guide me through adding a todo"), ("creation_steps", {"doctype": "ToDo"}))
		self.assertIsNone(self._route(_laya("HOWTO", 0.9), "guide me through life"))
		self.assertEqual(
			self._route(_laya("RECORD_LOOKUP", 0.9, asks_count=0.95), "number of todos we have"),
			("count", {"doctype": "ToDo"}),
		)
		# A lookup that isn't a count goes to the chat model, which picks read's fields.
		self.assertIsNone(self._route(_laya("RECORD_LOOKUP", 0.9, asks_count=0.1), "show open todos"))

	def test_never_routes_to_a_write(self):
		# A confident CREATE with values must reach the model, prechecks and approval card.
		self.assertIsNone(self._route(_laya("CREATE", 0.99), "create a todo with description call vendor"))
		# Without values it only asks for them (read-only).
		self.assertEqual(self._route(_laya("CREATE", 0.99), "create a todo"), ("required_values", {"doctype": "ToDo"}))

	def test_unavailable_tool_falls_back(self):
		self.assertIsNone(self._route(_laya("TROUBLESHOOTING", 0.95), "it broke", tools={"count"}))
		self.assertIn("isn't enabled", self._last_log().reason)

	def test_laya_failure_is_logged_and_ignored(self):
		router = MagicMock()
		router.predict.side_effect = ImportError("No module named 'laya'")
		self.assertIsNone(self._route(router, "how many todos"))
		log = self._last_log()
		self.assertEqual(log.outcome, "Error")
		self.assertIn("ImportError", log.error)

	def test_metrics_summarise_outcomes(self):
		frappe.db.delete("Flow Route Log")
		self._route(_laya("TROUBLESHOOTING", 0.95), "it broke")
		self._route(_laya("HOWTO", 0.3), "how do I add a todo")
		self._route(_laya("HOWTO", 0.2), "how do I add a note")
		m = laya_router.metrics(days=1)
		self.assertEqual((m["total"], m["routed"], m["fallback"], m["errors"]), (3, 1, 2, 0))
		self.assertAlmostEqual(m["fallback_rate"], 0.6667, places=3)
		self.assertEqual(m["routed_by_intent"], {"TROUBLESHOOTING": 1})

	def test_agent_uses_laya_when_no_exact_route_matches(self):
		from flow.lib.agent import Agent
		from flow.tools.builtins import error_diagnosis

		model = MagicMock()
		with patch.object(laya_router, "_router", return_value=_laya("TROUBLESHOOTING", 0.95)):
			result = Agent(model=model, tools=[error_diagnosis]).run("my sales order keeps exploding when I save it")
		model.chat.assert_not_called()
		self.assertIn("**Error diagnosis**", result.output)

	def test_finetuned_checkpoint_gets_the_questions_it_was_trained_on(self):
		agent = MagicMock()
		agent.system_one.return_value = _laya("TROUBLESHOOTING", 0.95).predict.return_value
		frappe.conf.flow_laya_model_path = "/tmp/flow-laya-model"
		try:
			with patch.object(laya_router, "_finetuned_agent", return_value=agent):
				result = laya_router.route("something exploded", TOOLS)
		finally:
			frappe.conf.flow_laya_model_path = None
		self.assertEqual(result[0], "error_diagnosis")
		self.assertEqual(agent.system_one.call_args[0][1], laya_router.FINETUNED_QUESTIONS)

