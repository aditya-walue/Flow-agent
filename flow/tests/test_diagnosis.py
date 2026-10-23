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

	def test_finds_a_message_wrapped_in_the_users_own_words(self):
		answer = error_diagnosis(
			error="getting this error\nMessage\nIcon is not correctly configured please check the workspace sidebar to it"
			" in the desk"
		)["answer"]
		self.assertIn("frappe/frappe/desk/page/desktop/desktop.js", answer)

	def test_finds_a_templated_message_by_its_fixed_start(self):
		answer = error_diagnosis(error="Could not find Row #1: Item: Some Unknown Item")["answer"]
		self.assertIn("frappe/frappe/model/document.py", answer)
		self.assertNotIn("```", answer)  # location only, never code
		self.assertIn("doesn't exist", answer)

	def test_finds_the_script_saved_on_the_site_that_raises_it(self):
		message = "Quarterly figures are locked for editing"
		script = frappe.get_doc(
			{
				"doctype": "Client Script",
				"name": "diagnosis probe script",
				"dt": "ToDo",
				"view": "Form",
				"enabled": 0,
				"script": f'frappe.ui.form.on("ToDo", {{ refresh() {{ frappe.throw(__("{message}")); }} }});',
			}
		).insert()
		answer = error_diagnosis(error=f"Message\n{message}")["answer"]
		self.assertIn("Client Script", answer)
		self.assertIn(script.name, answer)
		self.assertNotIn("isn't in the installed apps", answer)

	def test_translated_message_is_traced_to_its_english_source(self):
		frappe.get_doc(
			{
				"doctype": "Translation",
				"language": "fr",
				"source_text": "Icon is not correctly configured please check the workspace sidebar to it",
				"translated_text": "Icône mal configurée, vérifiez la barre latérale de l'espace de travail",
			}
		).insert()
		answer = error_diagnosis(error="Icône mal configurée, vérifiez la barre latérale de l'espace de travail")["answer"]
		self.assertIn("frappe/frappe/desk/page/desktop/desktop.js", answer)

	def test_more_known_errors_get_a_cause(self):
		answer = error_diagnosis(error="OperationalError: Lock wait timeout exceeded; try restarting transaction")["answer"]
		self.assertIn("Retry in a minute", answer)

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
