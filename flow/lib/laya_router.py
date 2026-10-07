# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Optional Laya decision layer: classify a user message before the chat model sees it.

Laya (https://github.com/NandhaKishorM/laya) is a small non-autoregressive classifier that
answers typed questions (`choice`, `score`, `noul`) about a text in one forward pass, with a
calibrated confidence. Flow asks it which kind of request a message is. When it is confident and
the request maps to a read-only Flow tool, the agent runs that tool directly; otherwise the chat
model (e.g. Qwen in the browser) handles the message exactly as before.

Laya only chooses. It never calls create/update/delete or any business action: the tools it can
route to are read-only, and writes still go through the model, the tools' prechecks, the user's
permissions and the approval card.

Off unless enabled, and optional: if the `laya` package isn't installed, fails to load, or errors,
Flow behaves as if it were disabled. Site config (site_config.json):

    flow_laya_enabled    true to turn it on (default false)
    flow_laya_threshold  calibrated confidence needed to route directly (default 0.8)
    flow_laya_model      Laya checkpoint name, e.g. "english" (default: Laya's own routing)
    flow_laya_model_path local checkpoint fine-tuned on Flow (scripts/laya/finetune.py); when set,
                         it is asked FINETUNED_QUESTIONS, the questions it was trained on

Every decision is logged as a Flow Route Log row (see `metrics` for the summary).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import frappe

DEFAULT_THRESHOLD = 0.8
INPUT_LOG_LIMIT = 500

# The intents shared with the other assistants on this bench, plus the two Flow also handles.
INTENTS: dict[str, str] = {
	"HOWTO": "asks how to create, add or set up a record by hand, or for the steps to do it",
	"RECORD_LOOKUP": "asks to count, find, list or show existing records or their details",
	"TROUBLESHOOTING": "reports an error message, a failure, or something that is not working",
	"CREATE": "asks the assistant to create or add a new record now",
	"DEFINITION": "asks what a term, document type or field means or is used for",
	"WORKFLOW": "asks about a business process with several steps, approvals or documents",
	"SMALL_TALK": "a greeting, thanks or casual chat with no request",
}

QUESTIONS: dict[str, dict[str, Any]] = {
	"intent": {
		"type": "choice",
		"instructions": "What kind of request is this message to an ERP assistant?",
		"criteria": INTENTS,
	},
	"asks_count": {
		"type": "noul",
		"instructions": "Does the message ask how many records there are (a count or number)?",
	},
	"complexity": {
		"type": "score",
		"instructions": "How complex is the request?",
		"criteria": ["one simple request", "a request with a couple of parts", "a complex multi-step request"],
	},
}
# Questions for a Laya checkpoint fine-tuned on Flow (scripts/laya/finetune.py trains on exactly
# these, so they must not change without retraining). Five intents only: create requests and
# greetings are handled by exact routes before Laya is asked.
FLOW_INTENTS: dict[str, str] = {
	"HOWTO": "asks how to create, add or set up a record by hand, or for the steps to do it",
	"DEFINITION": "asks what a term, document type or field means or is used for",
	"WORKFLOW": "asks about a business process with several steps, approvals or documents",
	"RECORD_LOOKUP": "asks to count, find, list or show existing records or their details",
	"TROUBLESHOOTING": "reports an error message, a failure, or something that is not working",
}
FINETUNED_QUESTIONS: dict[str, dict[str, Any]] = {
	"intent": {
		"type": "choice",
		"instructions": "What kind of request is this message to an ERP assistant?",
		"criteria": FLOW_INTENTS,
	},
	"asks_count": {
		"type": "noul",
		"instructions": "Does the message ask how many records there are (a count or number)?",
	},
}

# `complexity` (0 = simple .. 2 = complex) is logged for analysis but not used to gate: on the
# shipped checkpoint it scores nearly every message about 1.0, so it carries no signal yet.


@dataclass
class RouteDecision:
	"""Laya's verdict on one message, and what Flow did with it."""

	intent: str | None = None
	confidence: float = 0.0
	distribution: dict[str, float] = field(default_factory=dict)
	asks_count: float | None = None
	complexity: float | None = None
	model: str | None = None
	outcome: str = "Fallback"  # Routed | Fallback | Error
	reason: str = ""
	tool: str | None = None
	arguments: dict[str, Any] | None = None
	latency_ms: int = 0
	error: str | None = None


def enabled() -> bool:
	return bool(frappe.conf.get("flow_laya_enabled"))


def route(text: str, available_tools: set[str]) -> tuple[str, dict[str, Any]] | None:
	"""(tool name, arguments) to run for `text` without the chat model, or None to let the model
	handle it. Never raises: any failure is logged and treated as a fallback."""
	if not enabled() or not isinstance(text, str) or not text.strip():
		return None
	decision = RouteDecision()
	started = time.monotonic()
	try:
		_classify(text, decision)
		_decide(text, decision, available_tools)
	except Exception as e:
		decision.outcome, decision.error = "Error", f"{type(e).__name__}: {e}"[:1000]
		decision.reason = "Laya unavailable or failed; using the chat model"
	decision.latency_ms = int((time.monotonic() - started) * 1000)
	_log(text, decision)
	if decision.outcome == "Routed" and decision.tool:
		return decision.tool, decision.arguments or {}
	return None


def _classify(text: str, decision: RouteDecision) -> None:
	finetuned = frappe.conf.get("flow_laya_model_path")
	if finetuned:
		result = _finetuned_agent(finetuned).system_one(text, FINETUNED_QUESTIONS)
		result.setdefault("routing", {"model": finetuned})
	else:
		model = frappe.conf.get("flow_laya_model") or None
		result = _router().predict(text, QUESTIONS, model=model)
	answers = result.get("answers") or {}
	intent = answers.get("intent") or {}
	decision.intent = intent.get("choice")
	# answer_confidence is Laya's calibrated confidence, comparable across question types.
	decision.confidence = float(intent.get("answer_confidence") or intent.get("confidence") or 0)
	decision.distribution = {k: float(v) for k, v in (intent.get("probabilities") or {}).items()}
	decision.asks_count = _number(answers.get("asks_count"), "noul")
	decision.complexity = _number(answers.get("complexity"), "score")
	decision.model = str((result.get("routing") or {}).get("model") or "")[:140] or None


def _decide(text: str, decision: RouteDecision, available: set[str]) -> None:
	"""Pick a read-only Flow tool for a confident, simple request; otherwise fall back."""
	from flow.tools.builtins import _find_doctype_in_text, _parse_create_request

	threshold = float(frappe.conf.get("flow_laya_threshold") or DEFAULT_THRESHOLD)
	if decision.confidence < threshold:
		decision.reason = f"confidence {decision.confidence:.2f} below {threshold:.2f}"
		return

	intent = decision.intent
	tool: str | None = None
	arguments: dict[str, Any] | None = None
	if intent == "TROUBLESHOOTING":
		tool, arguments = "error_diagnosis", {"error": text}
	elif intent == "SMALL_TALK":
		tool, arguments = "small_talk", {"kind": "greeting"}
	elif intent == "HOWTO":
		doctype = _find_doctype_in_text(text)
		tool, arguments = ("creation_steps", {"doctype": doctype}) if doctype else (None, None)
	elif intent == "RECORD_LOOKUP" and (decision.asks_count or 0) >= threshold:
		doctype = _find_doctype_in_text(text)
		tool, arguments = ("count", {"doctype": doctype}) if doctype else (None, None)
	elif intent == "CREATE":
		# Laya never routes to create itself: only the read-only "what values do you need"
		# reply for a request that names a DocType but gives no values.
		parsed = _parse_create_request(text)
		if parsed and not parsed[1]:
			tool, arguments = "required_values", {"doctype": parsed[0]}

	if not tool:
		decision.reason = f"{intent}: no direct Flow path for this request"
		return
	if tool not in available:
		decision.reason = f"{intent}: tool {tool} isn't enabled for this agent"
		return
	decision.outcome, decision.tool, decision.arguments = "Routed", tool, arguments
	decision.reason = f"{intent} at {decision.confidence:.2f}"


def _number(answer: dict[str, Any] | None, key: str) -> float | None:
	if not answer or answer.get(key) is None:
		return None
	return float(answer[key])


_router_instance = None
_router_lock = threading.Lock()


def _router() -> Any:
	"""Laya's Router, loaded once per worker (it downloads a checkpoint on first use)."""
	global _router_instance
	if _router_instance is None:
		with _router_lock:
			if _router_instance is None:
				from laya import Router  # optional dependency: ImportError means "unavailable"

				_router_instance = Router()
	return _router_instance


_finetuned_agents: dict[str, Any] = {}


def _finetuned_agent(path: str) -> Any:
	"""The Flow fine-tuned checkpoint at `path`, loaded once per worker."""
	if path not in _finetuned_agents:
		with _router_lock:
			if path not in _finetuned_agents:
				import laya  # optional dependency

				_finetuned_agents[path] = laya.load(path)
	return _finetuned_agents[path]


def _log(text: str, decision: RouteDecision) -> None:
	"""Record the decision as a Flow Route Log row and in the flow.laya log file."""
	logger = frappe.logger("flow.laya", allow_site=True)
	# Frappe loggers keep only warnings (errors in production) by default; every decision is
	# worth a line here, so this one logger records info.
	logger.setLevel(logging.INFO)
	logger.info(
		{
			"outcome": decision.outcome,
			"intent": decision.intent,
			"confidence": decision.confidence,
			"tool": decision.tool,
			"latency_ms": decision.latency_ms,
			"error": decision.error,
		}
	)
	try:
		frappe.get_doc(
			{
				"doctype": "Flow Route Log",
				"run": frappe.flags.flow_run,
				"input": text[:INPUT_LOG_LIMIT],
				"intent": decision.intent,
				"confidence": decision.confidence,
				"distribution": frappe.as_json(decision.distribution),
				"asks_count": decision.asks_count,
				"complexity": decision.complexity,
				"outcome": decision.outcome,
				"tool": decision.tool,
				"reason": decision.reason[:140],
				"model": decision.model,
				"latency_ms": decision.latency_ms,
				"error": decision.error,
			}
		).insert(ignore_permissions=True)
	except Exception:
		logger.exception("could not write Flow Route Log")


def metrics(days: int = 7) -> dict[str, Any]:
	"""Laya decisions over the last `days`: how many were routed, fell back or errored, the
	fallback rate, average confidence and latency, and the routed count per intent."""
	since = frappe.utils.add_days(frappe.utils.now_datetime(), -int(days))
	rows = frappe.get_all(
		"Flow Route Log",
		filters={"creation": [">=", since]},
		fields=["outcome", "intent", "confidence", "latency_ms"],
		limit_page_length=0,
	)
	total = len(rows)
	count = lambda outcome: sum(1 for r in rows if r.outcome == outcome)  # noqa: E731
	routed_by_intent: dict[str, int] = {}
	for r in rows:
		if r.outcome == "Routed":
			routed_by_intent[r.intent] = routed_by_intent.get(r.intent, 0) + 1
	classified = [r for r in rows if r.outcome != "Error"]
	return {
		"enabled": enabled(),
		"days": int(days),
		"total": total,
		"routed": count("Routed"),
		"fallback": count("Fallback"),
		"errors": count("Error"),
		"fallback_rate": round(count("Fallback") / total, 4) if total else None,
		"error_rate": round(count("Error") / total, 4) if total else None,
		"avg_confidence": round(sum(r.confidence or 0 for r in classified) / len(classified), 4) if classified else None,
		"avg_latency_ms": round(sum(r.latency_ms or 0 for r in rows) / total) if total else None,
		"routed_by_intent": routed_by_intent,
	}
