# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""The default chat setup: a small model that runs in the user's browser (WebLLM) and a lean
assistant agent written for it. Both are created and kept up to date from code on install and
migrate, so the panel works with nothing to configure and offers no model or agent choice.
"""

from __future__ import annotations

import frappe

DEFAULT_MODEL_TITLE = "Qwen 2.5 3B (Browser)"
DEFAULT_MODEL_ID = "webllm/Qwen2.5-3B-Instruct-q4f16_1-MLC"

DEFAULT_AGENT_TITLE = "Flow Lite"
DEFAULT_AGENT_MAX_ITERATIONS = 8
DEFAULT_AGENT_TOOLS = (
	"find_doctypes",
	"describe",
	"count",
	"read",
	"create",
	"update",
	"creation_steps",
	"error_diagnosis",
	"required_values",
	"small_talk",
)
DEFAULT_AGENT_INSTRUCTIONS = """You help the user with their ERPNext / Frappe site. You can look things up with tools. Follow these rules exactly.

RULES
0. Greetings, thanks or small talk ("hi", "hii", "thanks", "ok"): reply in one short friendly line and offer help. Do NOT call any tool.
1. Never invent data. Every customer, item, record name, field or value you mention must come from a tool result or from the user.
2. Questions ("how many", "show", "list", "what is") -> look it up:
   - how many -> count(doctype). It also returns the record names.
   - details -> read(doctype, filters={"name": ["in", [names]]}, fields=[...]).
3. "How do I / how to / give me the steps" -> call creation_steps(doctype) and give the user its steps exactly as returned. Do NOT call create or update for a how-to question.
4. When the user asks to create/add a record and gives values, call create RIGHT AWAY with those values (field labels and typed dates are fine). Flow checks every value and the user approves before anything is saved. If they gave no values, call required_values(doctype). Never invent values.
5. Child tables: line items go inside their table field, e.g. Sales Invoice / Sales Order use "items": [{"item_code": ..., "qty": ..., "rate": ...}]. Use item codes, not item names.
6. If a tool returns an error, read it and fix that exact problem. If you cannot, tell the user plainly and stop.
7. After a successful create or update, reply with the record ID, its link from the tool result (/desk/<doctype>/<ID>), and the values you saved, one per line.
8. The user pastes an error message or asks why something failed -> call error_diagnosis(error) with the full text they pasted. Do not guess the cause yourself.

STYLE: short, plain answers. One sentence before each tool call saying what you are checking."""


def sync_default_assistant() -> None:
	"""Ensure the browser model and the default assistant exist, are enabled and match the code.
	Runs after install and migrate."""
	from flow.tools.builtins import sync_builtin_tools

	sync_builtin_tools()
	model = _sync_default_model()
	_sync_default_agent(model)


def default_agent() -> str | None:
	"""The agent every panel chat uses, if it is set up and enabled."""
	if frappe.db.get_value("Flow Agent", DEFAULT_AGENT_TITLE, "enabled"):
		return DEFAULT_AGENT_TITLE
	return None


def _sync_default_model() -> str:
	name = frappe.db.get_value("Flow Model", {"model_id": DEFAULT_MODEL_ID}, "name")
	if not name:
		doc = frappe.get_doc(
			{"doctype": "Flow Model", "title": DEFAULT_MODEL_TITLE, "model_id": DEFAULT_MODEL_ID, "enabled": 1}
		).insert(ignore_permissions=True)
		return doc.name
	if not frappe.db.get_value("Flow Model", name, "enabled"):
		frappe.db.set_value("Flow Model", name, "enabled", 1)
	return name


def _sync_default_agent(model: str) -> None:
	values = {
		"model": model,
		"enabled": 1,
		"is_system_generated": 1,
		"max_iterations": DEFAULT_AGENT_MAX_ITERATIONS,
		"instructions": DEFAULT_AGENT_INSTRUCTIONS,
	}
	if frappe.db.exists("Flow Agent", DEFAULT_AGENT_TITLE):
		doc = frappe.get_doc("Flow Agent", DEFAULT_AGENT_TITLE)
		doc.update(values)
	else:
		doc = frappe.get_doc({"doctype": "Flow Agent", "title": DEFAULT_AGENT_TITLE, **values})
	doc.set("tools", [{"tool": slug} for slug in DEFAULT_AGENT_TOOLS if frappe.db.exists("Flow Tool", slug)])
	doc.save(ignore_permissions=True) if not doc.is_new() else doc.insert(ignore_permissions=True)
