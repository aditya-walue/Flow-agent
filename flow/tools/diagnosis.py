# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""error_diagnosis: explain an error message from facts on this site, not from memory.

Three sources, each optional:
- the code that raises the message, found by searching the installed apps' source (exact text
  first, then its fixed parts, so templated messages like "Could not find {0}" still match);
- recent matching Error Log entries, for users who may read them;
- a table of common Frappe / ERPNext error types with their usual cause and fix.

The result is a finished, formatted diagnosis shown to the user as-is (a final-answer tool).
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from typing import Annotated, Any

import frappe

from flow.lib.tool import tool

SOURCE_EXTENSIONS = (".py", ".js", ".vue", ".ts")
# Generated bundles, dependencies and tests: matches there are noise, not where an error is raised.
SKIPPED_DIRS = frozenset(
	{"node_modules", "dist", ".git", "__pycache__", "tests", "test", "locale", "translations", "build", "fixtures"}
)
MAX_SOURCE_MATCHES = 2
MIN_FRAGMENT = 12
# Longer lines are minified bundles, not readable source.
MAX_LINE = 400
MAX_ERROR_LOGS = 3
# Lines that raise or show a message rank above a mere mention of the text.
RAISE_MARKERS = ("throw", "msgprint", "raise", "_(", "__(", "show_alert", "ValidationError")

# Pasted errors often carry the dialog's chrome; drop it before searching.
CHROME_PREFIXES = re.compile(
	r"^\s*(message|error|warning|alert|not permitted|validation error|traceback[^\n]*)\s*[:\n]\s*",
	re.IGNORECASE,
)
# The exception line of a Python traceback: "frappe.exceptions.LinkValidationError: Could not find …"
EXCEPTION_LINE = re.compile(r"^\s*(?:[\w.]+\.)?(\w+(?:Error|Exception)):\s*(.+)$", re.MULTILINE)

KNOWN_CAUSES: tuple[tuple[re.Pattern, str, str], ...] = tuple(
	(re.compile(pattern, re.IGNORECASE), cause, fix)
	for pattern, cause, fix in (
		(
			r"not permitted|no permission|insufficient permission|permissionerror|not allowed to",
			"Your role lacks permission for this document or action.",
			"Ask a System Manager to grant the permission in **Role Permission Manager** "
			"(or **User Permissions**), then reload.",
		),
		(
			r"is mandatory|mandatory fields? required|mandatoryerror|please (enter|select|set|specify)|cannot be (empty|blank)",
			"A required field is empty.",
			"Fill in the field named in the message, then save again.",
		),
		(
			r"could not find|does not exist|not found|linkvalidationerror",
			"A linked record named in the message doesn't exist (wrong name, deleted, or not yet created).",
			"Pick an existing record from the field's dropdown, or create the missing record first.",
		),
		(
			r"duplicate entry|already exists|duplicateentryerror|uniquevalidationerror",
			"A record with the same name or unique value already exists.",
			"Open the existing record instead, or change the name/value so it is unique.",
		),
		(
			r"cannot (edit|update|change).*(submitted|cancelled)|not allowed to change .* after submission|updateaftersubmiterror",
			"The document is submitted or cancelled, so it can no longer be edited.",
			"Cancel it and click **Amend** to make an editable copy, or create a new document.",
		),
		(
			r"cannot delete or cancel because .* is linked|linkexistserror|is linked with",
			"Other documents link to this one, so it can't be deleted or cancelled.",
			"Cancel or delete the linked documents listed in the message first.",
		),
		(
			r"fiscal year|accounting period|books (have been|are) closed|period closing",
			"The posting date falls in a closed or missing fiscal year / accounting period.",
			"Change the posting date, or ask an accountant to create or reopen the period.",
		),
		(
			r"naming series|series .* not set",
			"The document's naming series isn't set up.",
			"Set it in **Document Naming Settings** for this document type.",
		),
		(
			r"(document has been modified|timestampmismatch|has been modified after you have opened)",
			"Someone (or something) saved this document after you opened it.",
			"Reload the page, re-apply your changes and save again.",
		),
		(
			r"insufficient stock|negative stock|not enough stock|negativestockerror|needed in warehouse",
			"There isn't enough stock in the selected warehouse.",
			"Receive stock first (Purchase Receipt / Stock Entry), choose another warehouse, or reduce the quantity.",
		),
		(
			r"not a valid (phone|email|url)|invalid (phone|email|url)",
			"A phone number, email or URL isn't in a valid format.",
			"Correct the value's format: a phone number with its country code, or a full email address.",
		),
		(
			r"session expired|csrf|invalid request|login required|authenticationerror",
			"Your login session expired.",
			"Reload the page and log in again.",
		),
	)
)


# Unmistakably an error report: a traceback, an exception name, or a typical Frappe message.
ERROR_REPORT = re.compile(
	r"traceback \(most recent call last\)|\b\w+(error|exception)\b|^\s*(error|message)\s*[:\n]"
	r"|could not find|is mandatory|mandatory fields|not permitted|insufficient permission"
	r"|not correctly configured|please (check|enter|select|set)\b|does not exist|not allowed to"
	r"|cannot be (deleted|cancelled|empty)|duplicate entry|already exists|is not a valid"
	r"|session expired|negative stock|insufficient stock",
	re.IGNORECASE,
)
# Requests that mention an error but ask for something else ("how to ...", "create ...").
NOT_A_REPORT = re.compile(r"^\s*(how|create|add|make|show|list|count|update|delete|set)\b", re.IGNORECASE)


def _route_error(text: str) -> dict[str, Any] | None:
	if NOT_A_REPORT.match(text) or not ERROR_REPORT.search(text):
		return None
	return {"error": text}


@tool(final_answer=True, route=_route_error)
def error_diagnosis(
	error: Annotated[str, "The error message exactly as the user pasted it, including any traceback."],
) -> dict[str, Any]:
	"""Diagnose an error message the user got on this site: where it is raised, recent
	occurrences in the Error Log, the likely cause and how to fix it. Use whenever the user
	pastes an error or asks why something failed. The diagnosis is shown to the user directly.
	"""
	message, exception = _clean(error)
	if not message:
		raise ValueError("Paste the error message to diagnose.")

	sources = _find_in_source(message) if _can_read_source() else []
	logs = _recent_error_logs(message, exception)
	causes = [(cause, fix) for pattern, cause, fix in KNOWN_CAUSES if pattern.search(f"{exception} {message}")]

	if exception == "MandatoryError":
		missing = message.rsplit("]:", 1)[-1].strip()
		causes = [(f"Required field(s) left empty: {missing}.", "Fill in those fields, then save again.")]
	return {"answer": _format(message, exception, sources, logs, causes[:2])}


def _clean(error: str) -> tuple[str, str]:
	"""(the message itself, the exception class if the paste included a traceback)."""
	text = (error or "").strip()
	exception = ""
	matches = EXCEPTION_LINE.findall(text)
	if matches:
		exception, text = matches[-1]
	previous = None
	while previous != text:
		previous, text = text, CHROME_PREFIXES.sub("", text, count=1)
	lines = [line.strip() for line in text.splitlines() if line.strip()]
	return " ".join(lines)[:500], exception


def _can_read_source() -> bool:
	return frappe.session.user != "Guest" and frappe.get_cached_value(
		"User", frappe.session.user, "user_type"
	) == "System User"


def _find_in_source(message: str) -> list[dict[str, Any]]:
	"""Where the message is raised: the exact text first, then its longest fixed parts, then
	its leading words (a template's fixed start, "Could not find" of "Could not find {0}")."""
	for needle, translated_only in [(n, False) for n in _needles(message)] + [
		(n, True) for n in _leading_words(message)
	]:
		hits = _search(needle, translated_only)
		if hits:
			hits.sort(key=lambda h: not any(m in h["line_text"] for m in RAISE_MARKERS))
			# A partial match is a guess: show only the best one.
			return hits[: MAX_SOURCE_MATCHES if needle == message.strip(" .") else 1]
	return []


def _needles(message: str) -> list[str]:
	"""Search strings, most specific first. Values filled into a template ("Row #1", quoted
	names, numbers) split the message into the fixed parts the source code contains."""
	parts = re.split(r"\"[^\"]*\"|'[^']*'|#\d+|\d[\d,.:-]*|<[^>]+>|:\s|\[[^\]]*\]", message)
	# Multi-word phrases only: a lone identifier ("delivery_date") matches unrelated code.
	fragments = sorted(
		{p.strip(" .,;") for p in parts if len(p.strip(" .,;")) >= MIN_FRAGMENT and " " in p.strip()},
		key=len,
		reverse=True,
	)
	whole = message.strip(" .")
	return ([whole] if " " in whole else []) + [f for f in fragments if f != whole][:4]


def _leading_words(message: str) -> list[str]:
	"""The message's first 5..3 words, for templates whose filled-in values follow a fixed start."""
	words = re.findall(r"[A-Za-z][\w']*", message)
	out = []
	for n in (5, 4, 3):
		if len(words) >= n:
			phrase = " ".join(words[:n])
			if len(phrase) >= MIN_FRAGMENT and phrase not in out:
				out.append(phrase)
	return out


def _search(needle: str, translated_only: bool = False) -> list[dict[str, Any]]:
	"""Files whose source contains `needle` (case-insensitive), one hit per file. With
	`translated_only`, only user-facing strings count: lines that wrap text in _( or __(."""
	lowered = needle.lower()
	hits = []
	for path in _source_files():
		try:
			with open(path, encoding="utf-8", errors="ignore") as f:
				text = f.read()
		except OSError:
			continue
		if lowered not in text.lower():
			continue
		lines = text.splitlines()
		for i, line in enumerate(lines):
			if len(line) > MAX_LINE or lowered not in line.lower():
				continue
			if translated_only and not re.search(r"__?\(\s*[\"']" + re.escape(needle[:6]), line, re.IGNORECASE):
				continue
			hits.append(_hit(path, lines, i))
			break
		if len(hits) >= 10:
			break
	return hits


def _hit(path: str, lines: list[str], index: int) -> dict[str, Any]:
	relative = os.path.relpath(path, frappe.get_app_path("frappe", "..", ".."))
	app = relative.split(os.sep, 1)[0]
	return {
		"app": frappe.get_hooks("app_title", app_name=app)[0] if frappe.get_hooks("app_title", app_name=app) else app,
		"file": relative,
		"line": index + 1,
		"line_text": lines[index],
	}


@lru_cache(maxsize=1)
def _source_files() -> tuple[str, ...]:
	"""Source files of the installed apps (cached for the worker's lifetime)."""
	files = []
	for app in frappe.get_installed_apps():
		root = frappe.get_app_path(app)
		for dirpath, dirnames, filenames in os.walk(root):
			dirnames[:] = [d for d in dirnames if d not in SKIPPED_DIRS and not d.startswith(".")]
			files += [
				os.path.join(dirpath, name)
				for name in filenames
				if name.endswith(SOURCE_EXTENSIONS)
				and not name.endswith((".min.js", ".bundle.js"))
				and not name.startswith("test_")
			]
	return tuple(files)


def _recent_error_logs(message: str, exception: str) -> list[dict[str, Any]]:
	if not frappe.has_permission("Error Log", "read"):
		return []
	needle = next((n for n in _needles(message) if len(n) <= 140), message[:140])
	filters = [["error", "like", f"%{needle}%"]]
	logs = frappe.get_list(
		"Error Log",
		filters=filters,
		fields=["name", "method", "creation", "error", "reference_doctype", "reference_name"],
		order_by="creation desc",
		limit=MAX_ERROR_LOGS,
	)
	if not logs and exception:
		logs = frappe.get_list(
			"Error Log",
			filters=[["error", "like", f"%{exception}%"]],
			fields=["name", "method", "creation", "error", "reference_doctype", "reference_name"],
			order_by="creation desc",
			limit=MAX_ERROR_LOGS,
		)
	return logs


def _format(
	message: str,
	exception: str,
	sources: list[dict[str, Any]],
	logs: list[dict[str, Any]],
	causes: list[tuple[str, str]],
) -> str:
	out = ["**Error diagnosis**", f"> {message}"]
	if exception:
		out.append(f"Exception type: `{exception}`")

	if causes:
		out.append("**Likely cause**\n" + "\n".join(f"- {cause}" for cause, _fix in causes))
		out.append("**How to fix**\n" + "\n".join(f"- {fix}" for _cause, fix in causes))

	if sources:
		# Location only — users want the cause and the fix, not the code.
		places = "\n".join(f"- {hit['app']} — `{hit['file']}`, line {hit['line']}" for hit in sources)
		out.append("**Where it comes from**\n" + places)
	elif _can_read_source() and not causes:
		out.append("**Where it comes from**\nThis exact message isn't in the installed apps' code — it may come from a custom script, a server script, or a translation.")

	if logs:
		latest = logs[0]
		ref = (
			f" on {latest.reference_doctype} {latest.reference_name}"
			if latest.get("reference_doctype") and latest.get("reference_name")
			else ""
		)
		lines = [line.strip() for line in (latest.error or "").strip().splitlines() if line.strip()]
		summary = lines[-1][:200] if lines else ""
		out.append(
			f"**Recent occurrences** ({len(logs)} found in Error Log)\n"
			f"Latest: [{latest.name}](/desk/error-log/{latest.name}), {frappe.utils.format_datetime(latest.creation)}"
			f"{ref}{', in ' + latest.method if latest.get('method') else ''}"
			+ (f"\n> {summary}" if summary else "")
		)

	if not (causes or sources or logs):
		out.append(
			"I couldn't find this message in the code or the Error Log, and it doesn't match a known "
			"error type. Tell me what you were doing when it appeared (which page, which button)."
		)
	return "\n\n".join(out)
