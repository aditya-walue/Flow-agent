# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

from unittest.mock import MagicMock

import frappe
from frappe.tests import IntegrationTestCase

from flow.lib.agent import Agent
from flow.tools.answers import (
	_route_document_flow,
	_route_explain_doctype,
	_route_show_records,
	document_flow,
	explain_doctype,
	show_records,
)


class TestAnswerRoutes(IntegrationTestCase):
	def tearDown(self):
		frappe.db.rollback()

	def test_record_by_id_and_lists_route(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "answers probe"}).insert()
		self.assertEqual(_route_show_records(f"show the details of todo {todo.name}"), {"doctype": "ToDo", "names": [todo.name]})
		self.assertEqual(_route_show_records("list all todos"), {"doctype": "ToDo"})
		self.assertEqual(_route_show_records("show open todos"), {"doctype": "ToDo", "filters": {"status": "Open"}})
		self.assertIsNone(_route_show_records("show broken todos"))
		self.assertIsNone(_route_show_records("list all widgets"))

	def test_show_records_formats_from_live_data(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "answers probe"}).insert()
		one = show_records(doctype="ToDo", names=[todo.name])["answer"]
		self.assertIn(f"[{todo.name}](/desk/todo/{todo.name})", one)
		listed = show_records(doctype="ToDo", filters={"status": "Open"})["answer"]
		self.assertIn("ToDo record", listed)

	def test_definition_and_flow_routes(self):
		self.assertEqual(_route_explain_doctype("what is a todo?"), {"doctype": "ToDo"})
		self.assertIsNone(_route_explain_doctype("what is the meaning of life"))
		self.assertEqual(_route_document_flow("what happens after a note is submitted"), {"doctype": "Note"})
		self.assertIsNone(_route_document_flow("what happens after lunch"))
		self.assertIn("**ToDo** is a record type", explain_doctype(doctype="ToDo")["answer"])
		self.assertIn("**ToDo**", document_flow(doctype="ToDo")["answer"])

	def test_routed_answers_skip_the_model(self):
		model = MagicMock()
		result = Agent(model=model, tools=[show_records, explain_doctype, document_flow]).run("what is a todo?")
		model.chat.assert_not_called()
		self.assertIn("record type", result.output)

	def test_no_permission_raises(self):
		frappe.set_user("Guest")
		try:
			with self.assertRaises(PermissionError):
				show_records(doctype="User")
		finally:
			frappe.set_user("Administrator")

	def test_any_phrasing_and_case_insensitive_status(self):
		frappe.get_doc({"doctype": "ToDo", "description": "status case probe"}).insert()
		self.assertEqual(_route_show_records("any open todos?"), {"doctype": "ToDo", "filters": {"status": "Open"}})
		listed = show_records(doctype="ToDo", filters={"status": "open"})["answer"]
		self.assertNotIn("No ToDo records", listed)

	def test_record_id_with_any_verb_routes_but_writes_do_not(self):
		todo = frappe.get_doc({"doctype": "ToDo", "description": "pull up probe"}).insert()
		self.assertEqual(_route_show_records(f"can you pull up todo {todo.name} for me"), {"doctype": "ToDo", "names": [todo.name]})
		self.assertIsNone(_route_show_records(f"close todo {todo.name}"))

	def test_invalid_status_is_reported_with_options(self):
		with self.assertRaisesRegex(ValueError, "Options:"):
			show_records(doctype="ToDo", filters={"status": "Pending Forever"})

	def test_date_words_become_dates(self):
		from flow.tools.answers import _tidy_filters

		tidy = _tidy_filters("ToDo", {"date": ["<", "today()"]})
		self.assertEqual(tidy["date"], ["<", frappe.utils.today()])

