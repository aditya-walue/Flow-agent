# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

import frappe
from frappe.tests import IntegrationTestCase

from flow.assistant.default import (
	DEFAULT_AGENT_INSTRUCTIONS,
	DEFAULT_AGENT_TITLE,
	DEFAULT_AGENT_TOOLS,
	DEFAULT_MODEL_ID,
	default_agent,
	sync_default_assistant,
)


class TestDefaultAssistant(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_creates_browser_model_and_agent(self):
		# Raw deletes (sessions link to the agent, so delete_doc would refuse): children too.
		frappe.db.delete("Flow Agent Tool", {"parent": DEFAULT_AGENT_TITLE, "parenttype": "Flow Agent"})
		frappe.db.delete("Flow Agent", {"name": DEFAULT_AGENT_TITLE})
		frappe.db.delete("Flow Model", {"model_id": DEFAULT_MODEL_ID})

		sync_default_assistant()

		agent = frappe.get_doc("Flow Agent", DEFAULT_AGENT_TITLE)
		self.assertTrue(agent.enabled and agent.is_system_generated)
		self.assertEqual(frappe.db.get_value("Flow Model", agent.model, "model_id"), DEFAULT_MODEL_ID)
		self.assertEqual([r.tool for r in agent.tools], list(DEFAULT_AGENT_TOOLS))
		self.assertEqual(default_agent(), DEFAULT_AGENT_TITLE)

	def test_restores_edits_and_re_enables(self):
		sync_default_assistant()
		frappe.db.set_value("Flow Agent", DEFAULT_AGENT_TITLE, {"enabled": 0, "instructions": "edited"})
		model = frappe.db.get_value("Flow Model", {"model_id": DEFAULT_MODEL_ID}, "name")
		frappe.db.set_value("Flow Model", model, "enabled", 0)

		sync_default_assistant()

		agent = frappe.get_doc("Flow Agent", DEFAULT_AGENT_TITLE)
		self.assertTrue(agent.enabled)
		self.assertEqual(agent.instructions, DEFAULT_AGENT_INSTRUCTIONS)
		self.assertTrue(frappe.db.get_value("Flow Model", model, "enabled"))

	def test_is_idempotent(self):
		sync_default_assistant()
		sync_default_assistant()
		self.assertEqual(frappe.db.count("Flow Model", {"model_id": DEFAULT_MODEL_ID}), 1)

	def test_boot_names_the_default_agent(self):
		from flow.boot import boot_session

		sync_default_assistant()
		bootinfo = frappe._dict()
		boot_session(bootinfo)
		self.assertEqual(bootinfo.flow_default_agent, DEFAULT_AGENT_TITLE)
