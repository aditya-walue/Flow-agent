# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

import frappe
from frappe.tests import IntegrationTestCase

from flow.tools.diagnosis import _clean, _needles, error_diagnosis


class TestErrorDiagnosis(IntegrationTestCase):
	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def test_finds_where_an_exact_message_is_raised(self):
		answer = error_diagnosis(
			error="Message\nIcon is not correctly configured please check the workspace sidebar to it"
		)["answer"]
		self.assertIn("frappe/frappe/desk/page/desktop/desktop.js", answer)
		self.assertIn("**Where it comes from**", answer)

	def test_finds_a_templated_message_by_its_fixed_start(self):
		answer = error_diagnosis(error="Could not find Row #1: Item: Some Unknown Item")["answer"]
		self.assertIn("frappe/frappe/model/document.py", answer)
		self.assertNotIn("```", answer)  # location only, never code
		self.assertIn("doesn't exist", answer)

	def test_traceback_names_the_missing_fields(self):
		answer = error_diagnosis(
			error='Traceback (most recent call last):\n  File "x.py", line 1, in y\n'
			"frappe.exceptions.MandatoryError: [ToDo, abc123]: description"
		)["answer"]
		self.assertIn("`MandatoryError`", answer)
		self.assertIn("Required field(s) left empty: description.", answer)

	def test_recent_error_log_entries_are_listed(self):
		log = frappe.log_error(title="flow diagnosis probe", message="Flowprobe widget exploded unexpectedly")
		answer = error_diagnosis(error="Flowprobe widget exploded unexpectedly")["answer"]
		self.assertIn("**Recent occurrences**", answer)
		self.assertIn(log.name, answer)

	def test_unknown_message_asks_for_context(self):
		answer = error_diagnosis(error="Zzq flux capacitor overheated near the gizmo")["answer"]
		self.assertIn("couldn't find this message", answer)

	def test_lone_identifiers_are_not_searched_on_their_own(self):
		self.assertNotIn("delivery_date", _needles("[Sales Order, SAL-1]: delivery_date"))

	def test_dialog_chrome_is_stripped(self):
		self.assertEqual(_clean("Message\n\nPlease enter Delivery Date")[0], "Please enter Delivery Date")

	def test_website_users_get_no_source_code(self):
		frappe.set_user("Guest")
		answer = error_diagnosis(error="Icon is not correctly configured please check the workspace sidebar to it")[
			"answer"
		]
		self.assertNotIn("desktop.js", answer)
