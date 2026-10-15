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



class TestAssignmentSteps(IntegrationTestCase):
	"""assignment_steps reads where one DocType is attached to another from the metadata."""

	def _answer(self, text):
		from flow.tools.answers import _route_assignment_steps, assignment_steps

		args = _route_assignment_steps(text)
		return args, (assignment_steps.func(**args)["answer"] if args else None)

	def test_roles_go_on_the_users_table(self):
		args, answer = self._answer("how do I add a role to a user")
		self.assertEqual(args, {"item": "Role", "target": "User"})
		self.assertIn("**Roles Assigned** table", answer)

	def test_a_field_on_the_item_pointing_back(self):
		args, answer = self._answer("how to assign a todo to a role?")
		self.assertEqual(args, {"item": "ToDo", "target": "Role"})
		self.assertIn("Open the ToDo", answer)

	def test_dynamic_link_table(self):
		args, answer = self._answer("how can I link a contact to a user")
		self.assertIsNotNone(args)
		self.assertIn("Open the Contact", answer)

	def test_unrelated_or_unknown_doctypes_fall_through(self):
		self.assertEqual(self._answer("how do I link my car to the moon")[0], None)
		self.assertEqual(self._answer("how do I assign a note to a note")[0], None)


class TestStatusSteps(IntegrationTestCase):
	"""status_steps explains how a record reaches a status, from its metadata."""

	def _answer(self, text):
		from flow.tools.answers import _route_status_steps, status_steps

		args = _route_status_steps(text)
		return args, (status_steps.func(**args)["answer"] if args else None)

	def test_status_set_by_hand(self):
		args, answer = self._answer("how do I mark a todo as closed?")
		self.assertEqual(args, {"doctype": "ToDo", "status": "Closed"})
		self.assertIn("Closed", answer)

	def test_status_without_as(self):
		self.assertEqual(self._answer("how do I mark this todo closed")[0], {"doctype": "ToDo", "status": "Closed"})
		self.assertEqual(self._answer("how to set a todo to closed")[0], {"doctype": "ToDo", "status": "Closed"})

	def test_unknown_status_or_doctype_falls_through(self):
		self.assertIsNone(self._answer("how do I mark a todo as purple")[0])
		self.assertIsNone(self._answer("how do I mark my car as paid")[0])

	def test_paid_comes_from_the_connected_payment_document(self):
		if not frappe.db.exists("DocType", "Sales Invoice"):
			self.skipTest("ERPNext not installed")
		args, answer = self._answer("how can I make a sales invoice as paid?")
		self.assertEqual(args, {"doctype": "Sales Invoice", "status": "Paid"})
		self.assertIn("Payment Entry", answer)
		self.assertIn("submitted", answer)


class TestListPhrasing(IntegrationTestCase):
	"""List and count questions name records by a plain noun and a status word."""

	def test_more_list_phrasings(self):
		from flow.tools.answers import _route_show_records

		for text in ("is there any open todos?", "are there open todos", "do we have open todos", "do i have any open todos"):
			self.assertEqual(_route_show_records(text), {"doctype": "ToDo", "filters": {"status": "Open"}}, text)

	def test_count_with_status_word(self):
		from flow.tools.builtins import _route_count

		self.assertEqual(_route_count("how many open todos are there?"), {"doctype": "ToDo", "filters": {"status": "Open"}})

	def test_unknown_word_falls_through(self):
		from flow.tools.answers import _route_show_records

		self.assertIsNone(_route_show_records("show me broken todos"))

	def test_invented_filter_key_uses_the_select_field_with_that_value(self):
		from flow.tools.builtins import _precheck_query

		args = {"doctype": "ToDo", "filters": {"todo_state": "Open"}}
		self.assertIsNone(_precheck_query(args))
		self.assertEqual(args["filters"], {"status": "Open"})
		bad = {"doctype": "ToDo", "filters": {"todo_state": "purple"}}
		self.assertIn("one of: Open", _precheck_query(bad))

	def test_plain_noun_picks_the_record_type(self):
		if not frappe.db.exists("DocType", "Sales Invoice"):
			self.skipTest("ERPNext not installed")
		from flow.tools.answers import list_target

		self.assertEqual(list_target("unpaid customer invoices"), {"doctype": "Sales Invoice", "filters": {"status": "Unpaid"}})
		self.assertEqual(list_target("unpaid supplier invoices"), {"doctype": "Purchase Invoice", "filters": {"status": "Unpaid"}})


class TestAskedFields(IntegrationTestCase):
	"""A question about one record shows the fields its words name, from the record's labels."""

	def test_question_words_pick_fields_by_label(self):
		from flow.tools.answers import asked_fields

		self.assertIn("description", asked_fields("ToDo", "what is the description of todo abc123"))
		self.assertEqual(asked_fields("ToDo", "show todo abc123", exclude="abc123"), [])

	def test_record_answer_includes_asked_field(self):
		from flow.tools.answers import _route_show_records, show_records

		todo = frappe.get_doc({"doctype": "ToDo", "description": "asked field probe", "reference_type": "User"}).insert()
		args = _route_show_records(f"todo {todo.name}: what is its reference type?")
		self.assertIn("reference_type", args.get("about", []))
		self.assertIn("Reference Type", show_records.func(**args)["answer"])


class TestCountQuestions(IntegrationTestCase):
	"""Count questions: totals, breakdowns by a field, and an app or module scope."""

	def test_breakdown_by_a_field(self):
		from flow.tools.answers import count_arguments

		self.assertEqual(count_arguments("number of open todos by priority"), {"doctype": "ToDo", "filters": {"status": "Open"}, "group_by": "priority"})
		self.assertEqual(count_arguments("how many todos are there?"), {"doctype": "ToDo"})
		# A breakdown word that names no field is left to the chat model.
		self.assertIsNone(count_arguments("how many todos per flavour"))

	def test_grouped_count_answer(self):
		from flow.tools.builtins import count

		frappe.get_doc({"doctype": "ToDo", "description": "group probe", "priority": "High"}).insert()
		result = count.func(doctype="ToDo", group_by="priority")
		self.assertIn("High", result["groups"])
		self.assertIn("| Priority | Count |", result["answer"])

	def test_scope_by_app_or_module(self):
		from flow.tools.answers import _scope

		self.assertIn("core", _scope("frappe framework"))
		self.assertEqual(_scope("core"), {"core"})
		self.assertIsNone(_scope("the system"))
