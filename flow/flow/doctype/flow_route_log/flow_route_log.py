# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

import frappe
from frappe.model.document import Document


class FlowRouteLog(Document):
	"""One Laya routing decision (see flow.lib.laya_router). Written by the router; read-only."""

	@staticmethod
	def clear_old_logs(days=30):
		from frappe.query_builder import Interval
		from frappe.query_builder.functions import Now

		table = frappe.qb.DocType("Flow Route Log")
		frappe.db.delete(table, filters=(table.creation < (Now() - Interval(days=days))))
