# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Read-only tools that answer common questions completely from site data, with routes that run
them without the chat model:

- show_records    a record by ID ("show the details of <ID>", "status of <ID>"), or a list
                  ("list all sales invoices", "show open todos", "which employees do we have")
- explain_doctype "what is a <doctype>?" — module, purpose, lifecycle, main fields, links
- document_flow   "what happens after a <doctype> is submitted?" — lifecycle, workflow, and the
                  documents ERPNext connects before and after it
- assignment_steps "how do I assign <doctype> to <doctype>?" — where one record type is attached
                  to another (a field, a table, or through a linked record), from the metadata

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


# ── assignment_steps ───────────────────────────────────────────────────────────────────────

# "how do I assign roles to employees", "how to link a contact with a customer",
# "steps to give a role to a user": acting on existing records rather than creating one.
ASSIGN_QUESTION = re.compile(
	r"^\s*(?:how\s+(?:do|can|should)\s+(?:i|we|you)|how\s+to|steps\s+(?:to|for)|what\s+are\s+the\s+steps\s+(?:to|for))\s+"
	r"(?:assign|link|attach|add|give|grant|allocate|map|connect|set)\s+(?P<item>[a-z][a-z \-]*?)\s+"
	r"(?:to|for|with|on|against)\s+(?P<target>[a-z][a-z \-]*?)\s*[?.!]*\s*$",
	re.IGNORECASE,
)
LINK_TYPES = ("Link", "Table", "Table MultiSelect")


def _route_assignment_steps(text: str) -> dict[str, Any] | None:
	match = ASSIGN_QUESTION.match(text or "")
	return match and assignment_arguments(match.group("item"), match.group("target"))


# Laya: an assign/link/attach verb, then "<item> to|with|for|on <target>" anywhere in the message
# ("can you tell me the way to attach a holiday list for employees").
LOOSE_ASSIGNMENT = re.compile(
	r"\b(?:assign|assigning|link|linking|attach|attaching|give|giving|grant|granting|allocate|allocating|map|mapping|connect|connecting|add|adding)\s+"
	r"(?P<item>[a-z][a-z \-]*?)\s+(?:to|for|with|on|against)\s+(?P<target>[a-z][a-z \-]*)",
	re.IGNORECASE,
)


def assignment_arguments(item_phrase: str, target_phrase: str) -> dict[str, Any] | None:
	"""{"item", "target"} when both phrases name DocTypes the metadata can connect, else None."""
	find = _builtins()._find_doctype_in_text
	item = find(item_phrase) or _assignment_doctype_for(item_phrase)
	target = find(target_phrase)
	if not item or not target or item == target or not _assignment_paths(item, target):
		return None
	return {"item": item, "target": target}


def loose_assignment(text: str) -> dict[str, Any] | None:
	match = LOOSE_ASSIGNMENT.search(text or "")
	return match and assignment_arguments(match.group("item"), match.group("target"))


@tool(final_answer=True, route=_route_assignment_steps)
def assignment_steps(
	item: Annotated[str, 'DocType being assigned or linked, e.g. "Role" in "assign roles to employees".'],
	target: Annotated[str, 'DocType it is assigned to, e.g. "Employee".'],
) -> dict[str, Any]:
	"""Steps to assign, link or attach one kind of record to another ("how do I assign roles to
	employees?"), found from the site's metadata: a field or table on the target, a field on the
	item pointing back, or a record the target links to. Shown to the user directly. Changes nothing."""
	for doctype in (item, target):
		if not frappe.has_permission(doctype, "read"):
			raise PermissionError(f"No permission to read {doctype}")
	paths = _assignment_paths(item, target)
	if not paths:
		return {
			"answer": f"This site has no field or table that links **{item}** to **{target}**, so they "
			"aren't assigned to each other directly. Ask your administrator if a custom field is needed.",
			"item": item,
			"target": target,
		}
	return {"answer": _describe_path(item, target, paths[0]), "item": item, "target": target}


ASSIGNMENT_SUFFIXES = ("Assignment", "Allocation")


def _assignment_doctype_for(phrase: str) -> str | None:
	"""An "<X> Assignment" / "<X> Allocation" DocType for a phrase that isn't a DocType itself
	("shift" -> Shift Assignment)."""
	words = " ".join(w.capitalize() for w in re.findall(r"[a-z]+", (phrase or "").lower()) if w not in ("a", "an", "the"))
	names = _builtins()._doctype_names()
	for suffix in ASSIGNMENT_SUFFIXES:
		for name in (f"{words} {suffix}", f"{words.rstrip('s')} {suffix}"):
			if name.lower() in names:
				return names[name.lower()]
	return None


def _assignment_paths(item: str, target: str) -> list[list[tuple[str, Any]]]:
	"""Ways to attach `item` to `target`, best first. Each path is a list of hops, every hop a
	(doctype the user opens, field they fill): a dedicated assignment record ("Salary Structure
	Assignment"), a field on the target, a field on the item pointing back at the target (a
	Dynamic Link table included, like Contact's Links), or a record the target links to."""
	if item.endswith(ASSIGNMENT_SUFFIXES) and _fields_to(item, target):
		return [[(item, f)] for f in _fields_to(item, target) if f.fieldtype == "Link"][:1] or []
	assignment = _assignment_record(item, target)
	if assignment:
		return [assignment]
	candidates = [(2, [(target, f)]) for f in _fields_to(target, item)]
	candidates += [(2, [(item, f)]) for f in _fields_to(item, target, dynamic=True) if f.fieldtype != "Link" or f.options == target]
	for link in frappe.get_meta(target).fields:
		if link.fieldtype != "Link" or not link.options or link.options in (item, target) or link.hidden:
			continue
		via = frappe.get_meta(link.options)
		if via.istable or via.issingle:
			continue
		candidates += [(0, [(target, link), (link.options, f)]) for f in _fields_to(link.options, item)]
	ranked = sorted(enumerate(candidates), key=lambda c: (-(c[1][0] + _fit(c[1][1][-1][1], item, target)), c[0]))
	return [path for _, (_, path) in ranked]


def _fit(field: Any, item: str, target: str) -> int:
	"""How plainly a field is "the place" for the assignment: named after the record type it
	holds ("roles" for Role, "department" for Department) rather than one special use of it
	("exception_budget_approver_role"), and a table, which can hold several."""
	held = field.options if field.fieldtype == "Link" else None
	names = {_snake(item), _snake(item) + "s", _snake(target), _snake(target) + "s"}
	score = 3 if field.fieldname in names or (held and field.fieldname == _snake(held)) else 0
	return score + (2 if field.fieldtype != "Link" else 0)


def _snake(doctype: str) -> str:
	return doctype.lower().replace(" ", "_")


def _assignment_record(item: str, target: str) -> list[tuple[str, Any]] | None:
	"""A "<item> Assignment" / "<item> Allocation" DocType that links to both: the way the apps
	mean the assignment to be made (it often carries dates and amounts the target can't)."""
	names = _builtins()._doctype_names()
	for suffix in ASSIGNMENT_SUFFIXES:
		doctype = names.get(f"{item} {suffix}".lower())
		if doctype and _fields_to(doctype, item) and _fields_to(doctype, target):
			return [(doctype, _fields_to(doctype, target)[0])]
	return None


def _fields_to(doctype: str, other: str, dynamic: bool = False) -> list[Any]:
	"""Fields of `doctype` that hold `other`: a visible Link to it, or a table whose rows link to
	it (with `dynamic`, also rows with a Dynamic Link, which can point at any DocType). Tables count
	even when hidden: some (User's roles) are edited through a custom widget."""
	found = []
	for f in frappe.get_meta(doctype).fields:
		if f.fieldtype not in LINK_TYPES or not f.options:
			continue
		if f.fieldtype == "Link":
			if f.options == other and not f.hidden:
				found.append(f)
			continue
		rows = frappe.get_meta(f.options).fields
		if any(c.fieldtype == "Link" and c.options == other for c in rows) or (
			dynamic and any(c.fieldtype == "Dynamic Link" for c in rows)
		):
			found.append(f)
	return found


def _describe_path(item: str, target: str, path: list[tuple[str, Any]]) -> str:
	steps = []
	if len(path) == 2:
		(_, link), (via, field) = path
		steps.append(
			f"{item} isn't set on the {target} itself: it's set on the **{via}** the {target} links to."
		)
		steps.append(
			f"1. Open the {target} ([list](/desk/{_route(target)})) and check **{link.label}**: that's its {via}. "
			f"If it's empty, pick or create the {via} there and save."
		)
		steps.append(f"2. Open that {via} and {_where(via, field)}{_fill(field, item)}.")
		steps.append(f"3. Save the {via}.")
	elif path[0][0] != target and path[0][0].endswith(ASSIGNMENT_SUFFIXES):
		((doctype, field),) = path
		if doctype != item:
			steps.append(f"Create a **{doctype}** record: it assigns the {item} to the {target}.")
		else:
			steps.append(f"Create a **{doctype}** record for the {target}.")
		steps.append(f"1. Open [New {doctype}](/desk/{_route(doctype)}/new).")
		chosen = f" and the {item}" if doctype != item else ""
		steps.append(f"2. Pick the {target} in **{field.label}**{chosen}, and fill in the other required fields.")
		steps.append(f"3. Save{' and Submit' if frappe.get_meta(doctype).is_submittable else ''} it.")
	else:
		((doctype, field),) = path
		other = target if doctype == item else item
		steps.append(f"1. Open the {doctype} ([list](/desk/{_route(doctype)})).")
		action = _where(doctype, field) + _fill(field, other)
		steps.append(f"2. {action[0].upper()}{action[1:]}.")
		steps.append(f"3. Save the {doctype}.")
	return "\n\n".join(steps)


def _fill(field: Any, other: str) -> str:
	if field.fieldtype == "Link":
		return f"set **{field.label}** to the {other}"
	if any(c.fieldtype == "Dynamic Link" for c in frappe.get_meta(field.options).fields):
		return f"add a row in the **{field.label}** table with {other} and the {other}'s name"
	return f"add the {other} in the **{field.label}** table"


def _where(doctype: str, field: Any) -> str:
	"""'in the <section> section, ' for the section holding `field`, when it has a label."""
	section = None
	for f in frappe.get_meta(doctype).fields:
		if f.fieldtype in ("Section Break", "Tab Break") and f.label:
			section = f.label
		if f.fieldname == field.fieldname:
			break
	return f"in the **{section}** section, " if section else ""


def _route(doctype: str) -> str:
	return doctype.lower().replace(" ", "-")

