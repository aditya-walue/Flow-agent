# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Read-only tools that answer common questions completely from site data, with routes that run
them without the chat model:

- show_records    a record by ID ("show the details of <ID>", "status of <ID>"), or a list
                  ("list all sales invoices", "show open todos", "which employees do we have")
- explain_doctype "what is a <doctype>?" — module, purpose, lifecycle, main fields, links
- document_flow   "what happens after a <doctype> is submitted?" — lifecycle, workflow, and the
                  documents ERPNext connects before and after it

Every answer is built from the live metadata and records the user may read; a route only fires
when it recognises a real DocType or record, otherwise the message goes on to Laya / the model.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

import frappe

from flow.lib.tool import tool

# builtins imports this module to register its tools, so its helpers are imported inside the
# functions below rather than at the top (which would be circular).
DocTypeArg = Annotated[str, 'Exact DocType name, e.g. "Sales Invoice", "Customer", "Item".']


def _builtins():
	from flow.tools import builtins

	return builtins

LIST_LIMIT = 20
MAIN_FIELDS_LIMIT = 6

# A token that looks like a record ID: letters and digits joined by dashes/dots/slashes, with a
# digit somewhere ("ACC-SINV-2026-00008", "HR-EMP-00003", "PO/24/001").
RECORD_ID = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[-./][A-Za-z0-9]+)*[-./]?\d[A-Za-z0-9-./]*\b")
SHOW_RECORD = re.compile(
	r"\b(show|details?|detail|info(?:rmation)?|status|open|view|display|get|tell\s+me\s+about|what\s+is)\b",
	re.IGNORECASE,
)
# Asking to change a record rather than see it: those go to the model (and the approval flow).
WRITE_INTENT = re.compile(r"\b(create|add|make|update|change|set|edit|delete|remove|cancel|submit|amend|rename|close|mark)\b", re.IGNORECASE)
LIST_REQUEST = re.compile(
	r"^\s*(?:please\s+)?(?:list|show(?:\s+me)?|get(?:\s+me)?|display|which|what|any|are\s+there\s+any|do\s+we\s+have\s+any)\s+"
	r"(?:(?:all|the|my|our|every)\s+)*(?:(?P<order>latest|recent|newest|last|oldest)\s+)?"
	r"(?P<rest>[a-z][a-z \-]*?)"
	r"(?:\s+(?:do\s+we\s+have|are\s+there|exist|in\s+(?:the\s+)?system|we\s+have|present))?\s*[?.!]*\s*$",
	re.IGNORECASE,
)
DEFINITION = re.compile(
	r"^\s*(?:what\s+(?:is|are|does)\s+(?:a|an|the)?\s*|explain\s+(?:what\s+(?:a|an)\s+)?|define\s+|meaning\s+of\s+)"
	r"(?P<what>[a-z][a-z \-]*?)(?:\s+(?:mean|means|used\s+for|for|do|in\s+erpnext))?\s*[?.!]*\s*$",
	re.IGNORECASE,
)
FLOW_QUESTION = re.compile(
	r"^\s*(?:what\s+(?:happens|comes|is\s+next|follows)\s+(?:after|once|when)\s+(?:a|an|the)?\s*"
	r"|what\s+comes\s+after\s+(?:a|an|the)?\s*|next\s+steps?\s+(?:after|for)\s+(?:a|an|the)?\s*)"
	r"(?P<what>[a-z][a-z \-]*?)(?:\s+(?:is|gets|has\s+been)\s+(?:submitted|created|approved|saved|made))?\s*[?.!]*\s*$",
	re.IGNORECASE,
)


# ── show_records ──────────────────────────────────────────────────────────────────────────


def _route_show_records(text: str) -> dict[str, Any] | None:
	# A real record ID in a message that isn't asking to change anything is a request to see
	# it, however it is phrased ("pull up", "can you get", "about").
	by_id = _record_from_text(text) if not WRITE_INTENT.search(text or "") else None
	if by_id:
		return {"doctype": by_id[0], "names": [by_id[1]]}
	match = LIST_REQUEST.match(text or "")
	if not match:
		return None
	return _list_arguments(match.group("rest"), match.group("order"))


def _record_from_text(text: str) -> tuple[str, str] | None:
	"""(DocType, ID) for a record ID in the text that exists and the user may read. The DocType
	comes from the text when named, else from the naming series the ID's prefix belongs to."""
	named = _builtins()._find_doctype_in_text(text)
	tokens = RECORD_ID.findall(text or "")
	if named:
		# With the DocType named, any word containing a digit may be its ID (random IDs like
		# "1bj14mc8ae" don't follow a naming series).
		tokens += [w for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9-./]{3,}", text or "") if re.search(r"\d", w)]
	for token in dict.fromkeys(tokens):
		token = token.strip(".-/")
		for doctype in ([named] if named else []) + _doctypes_for_series(token):
			if frappe.db.exists(doctype, {"name": token}) and frappe.has_permission(doctype, "read", token):
				return doctype, token
	return None


def _doctypes_for_series(record_id: str) -> list[str]:
	"""DocTypes whose naming series options start like this ID ("ACC-SINV-2026-00008" -> the
	DocTypes with an "ACC-SINV-" series), from the DocType and any Property Setter override."""
	prefix = re.match(r"^(.*?[-./])\d", record_id)
	if not prefix or len(prefix.group(1)) < 3:
		return []
	stem = re.split(r"[-./]\d", record_id)[0].split(".")[0]
	like = ["like", f"%{stem}%"]
	doctypes = frappe.get_all("DocField", filters={"fieldname": "naming_series", "options": like}, pluck="parent")
	doctypes += frappe.get_all(
		"Property Setter",
		filters={"field_name": "naming_series", "property": "options", "value": like},
		pluck="doc_type",
	)
	return list(dict.fromkeys(doctypes))


def _list_arguments(rest: str, order: str | None) -> dict[str, Any] | None:
	"""Arguments for a list request: the DocType, plus a status filter when an adjective in the
	request is one of its status values ("open todos", "paid invoices")."""
	words = rest.strip().split()
	doctype = _builtins()._doctype_from_phrase(" ".join(words), fuzzy=True)
	status = None
	if not doctype and len(words) > 1:
		doctype = _builtins()._doctype_from_phrase(" ".join(words[1:]), fuzzy=True)
		status = _status_value(doctype, words[0]) if doctype else None
		if doctype and not status:
			return None  # an unknown qualifier ("broken invoices"): let the model interpret it
	if not doctype:
		return None
	arguments: dict[str, Any] = {"doctype": doctype}
	if status:
		arguments["filters"] = {"status": status}
	if order in ("oldest",):
		arguments["order_by"] = "creation asc"
	return arguments


def _status_value(doctype: str, word: str) -> str | None:
	field = frappe.get_meta(doctype).get_field("status")
	if not field or field.fieldtype != "Select" or not field.options:
		return None
	options = {o.strip().lower(): o.strip() for o in field.options.split("\n") if o.strip()}
	return options.get(word.lower())


@tool(final_answer=True, route=_route_show_records)
def show_records(
	doctype: DocTypeArg,
	names: Annotated[list[str] | None, "Record IDs; omit for a recent list."] = None,
	filters: Annotated[dict | None, 'e.g. {"status": "Open"}.'] = None,
	order_by: Annotated[str | None, 'e.g. "creation desc".'] = None,
) -> dict[str, Any]:
	"""Show records (by ID, or a recent list) with key fields. Shown to the user directly."""
	if not frappe.has_permission(doctype, "read"):
		raise PermissionError(f"No permission to read {doctype}")
	route = doctype.lower().replace(" ", "-")
	columns = _builtins()._key_columns(doctype)
	filters = _tidy_filters(doctype, filters)
	conditions = dict(filters or {})
	if names:
		conditions["name"] = ["in", names]
	rows = frappe.get_list(
		doctype, filters=conditions, fields=columns, order_by=order_by or "creation desc", limit=LIST_LIMIT
	)
	total = frappe.db.count(doctype, conditions) if not names else len(rows)
	meta = frappe.get_meta(doctype)
	labels = {c: (meta.get_field(c).label if meta.get_field(c) else c.replace("_", " ").title()) for c in columns}

	if not rows:
		what = f"{doctype} {', '.join(names)}" if names else f"{doctype} records" + (" matching that" if filters else "")
		return {"answer": f"No {what} found (or you don't have access to them)."}

	if len(rows) == 1 and names:
		row = rows[0]
		lines = [f"**[{row.name}](/desk/{route}/{row.name})** — {doctype}"]
		lines += [f"- **{labels[c]}**: {_fmt(row.get(c))}" for c in columns if c != "name" and row.get(c) not in (None, "")]
		return {"answer": "\n".join(lines), "doctype": doctype, "names": [row.name]}

	shown = [c for c in columns if c != "name"][:4]
	header = "| ID | " + " | ".join(labels[c] for c in shown) + " |"
	sep = "|---" * (len(shown) + 1) + "|"
	body = [
		f"| [{r.name}](/desk/{route}/{r.name}) | " + " | ".join(_fmt(r.get(c)) for c in shown) + " |" for r in rows
	]
	qualifier = " ".join(f"{k} = {v}" for k, v in (filters or {}).items())
	title = f"**{total} {doctype} record{'s' if total != 1 else ''}**" + (f" ({qualifier})" if qualifier else "")
	more = f"\n\nShowing the latest {len(rows)}." if total > len(rows) else ""
	return {
		"answer": f"{title}:\n\n{header}\n{sep}\n" + "\n".join(body) + more,
		"doctype": doctype,
		"names": [r.name for r in rows],
	}


def _tidy_filters(doctype: str, filters: dict | None) -> dict | None:
	"""Filters as read() tidies them (labels, operator words), plus Select values matched to
	their option regardless of case ("overdue" -> "Overdue")."""
	if not filters:
		return filters
	args = {"doctype": doctype, "filters": dict(filters)}
	_builtins()._precheck_query(args)
	meta = frappe.get_meta(doctype)
	tidy = {}
	for key, value in (args["filters"] or {}).items():
		field = meta.get_field(key)
		value = _date_words(value)
		if field and field.fieldtype == "Select" and isinstance(value, str):
			options = {o.strip().lower(): o.strip() for o in (field.options or "").split("\n") if o.strip()}
			if value.strip().lower() not in options:
				# Report it rather than return "no records": the model can retry with a real value.
				raise ValueError(
					f"{value!r} is not a {field.label} of {doctype}. Options: {', '.join(options.values())}."
				)
			value = options[value.strip().lower()]
		tidy[key] = value
	return tidy


DATE_WORDS = {"today()": "today", "today": "today", "now()": "today", "now": "today", "nowdate()": "today"}


def _date_words(value: Any) -> Any:
	"""Date words a model writes into filters ("today()", ["<", "today"]) as the actual date."""
	if isinstance(value, str) and value.strip().lower() in DATE_WORDS:
		return frappe.utils.today()
	if isinstance(value, list) and len(value) == 2:
		return [value[0], _date_words(value[1])]
	return value


def _fmt(value: Any) -> str:
	if value is None:
		return ""
	if isinstance(value, float):
		return f"{value:,.2f}"
	return str(value).replace("|", "/").replace("\n", " ")[:60]


# ── explain_doctype ───────────────────────────────────────────────────────────────────────


def _route_explain_doctype(text: str) -> dict[str, Any] | None:
	match = DEFINITION.match(text or "")
	doctype = match and _builtins()._doctype_from_phrase(match.group("what"), fuzzy=True)
	return {"doctype": doctype} if doctype else None


@tool(final_answer=True, route=_route_explain_doctype)
def explain_doctype(doctype: DocTypeArg) -> dict[str, Any]:
	"""Explain what a DocType (record type) is: its module, purpose, lifecycle, main fields and
	the records it links to, from the site's metadata. Shown to the user directly."""
	if not frappe.has_permission(doctype, "read"):
		raise PermissionError(f"No permission to read {doctype}")
	meta = frappe.get_meta(doctype)
	route = doctype.lower().replace(" ", "-")
	parts = [f"**{doctype}** is a record type in the **{meta.module}** module."]
	if meta.description:
		parts.append(frappe.utils.strip_html(meta.description).strip())
	if meta.is_submittable:
		parts.append("It is **submittable**: saved as a Draft, then Submitted to make it final; a submitted record can only be cancelled (and amended), not edited.")
	if meta.is_tree:
		parts.append("Its records form a **tree** (groups containing records).")
	workflow = frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1}, "workflow_name")
	if workflow:
		parts.append(f"It follows the **{workflow}** approval workflow.")

	main = _builtins()._user_filled(meta) + _builtins()._conditionally_required(meta)
	main_labels = [f.label for f in main if f.label][:MAIN_FIELDS_LIMIT]
	tables = [t.label for t in meta.get_table_fields() if t.reqd]
	if main_labels or tables:
		parts.append("Main fields: " + ", ".join(main_labels + [f"{t} (table)" for t in tables]) + ".")
	links = list(dict.fromkeys(f.options for f in meta.fields if f.fieldtype == "Link" and f.reqd and f.options))
	if links:
		parts.append("It links to: " + ", ".join(links) + ".")
	count = frappe.db.count(doctype) if frappe.has_permission(doctype, "read") else None
	if count is not None:
		parts.append(f"There {'is' if count == 1 else 'are'} {count} on this site — [open the list](/desk/{route}).")
	return {"answer": "\n\n".join(parts), "doctype": doctype}


# ── document_flow ─────────────────────────────────────────────────────────────────────────


def _route_document_flow(text: str) -> dict[str, Any] | None:
	match = FLOW_QUESTION.match(text or "")
	doctype = match and _builtins()._doctype_from_phrase(match.group("what"), fuzzy=True)
	return {"doctype": doctype} if doctype else None


@tool(final_answer=True, route=_route_document_flow)
def document_flow(doctype: DocTypeArg) -> dict[str, Any]:
	"""Explain where a DocType sits in its business process: its lifecycle, any approval
	workflow, the documents it is usually made from, and the documents made from it next, as
	the site's apps connect them. Shown to the user directly."""
	if not frappe.has_permission(doctype, "read"):
		raise PermissionError(f"No permission to read {doctype}")
	meta = frappe.get_meta(doctype)
	parts = [f"**{doctype}** ({meta.module})"]
	if meta.is_submittable:
		parts.append("1. It is created as a **Draft** and can be edited.\n2. **Submit** makes it final: it can no longer be edited, only **Cancelled** (and then **Amended** into a new draft).")
	workflow = frappe.db.get_value("Workflow", {"document_type": doctype, "is_active": 1}, "name")
	if workflow:
		states = frappe.get_all("Workflow Document State", filters={"parent": workflow}, pluck="state", order_by="idx")
		parts.append(f"Approval workflow **{workflow}**: " + " → ".join(states) + ".")

	groups = _connections(meta)
	before = groups.get("before", [])
	after = groups.get("after", [])
	if before:
		parts.append("**Usually made from:** " + ", ".join(before) + ".")
	if after:
		parts.append("**What comes next** (documents created from it): " + ", ".join(after) + ".")
	if not (meta.is_submittable or workflow or before or after):
		parts.append("It is a standalone record: no lifecycle or connected documents are defined for it.")
	parts.append("Open a record and use the **Create** button to make the next document from it.")
	return {"answer": "\n\n".join(parts), "doctype": doctype}


def _connections(meta: Any) -> dict[str, list[str]]:
	"""Documents connected to this DocType, from its dashboard: "Reference" groups are where it
	came from, the rest (Related, Returns, ...) are made from it."""
	try:
		data = meta.get_dashboard_data()
	except Exception:
		return {}
	before, after = [], []
	for group in data.get("transactions") or []:
		items = [i for i in group.get("items") or [] if frappe.db.exists("DocType", i)]
		(before if (group.get("label") or "").lower() == "reference" else after).extend(items)
	return {"before": list(dict.fromkeys(before)), "after": list(dict.fromkeys(after))}
