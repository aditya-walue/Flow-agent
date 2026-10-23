# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Optional Opik tracing (https://www.comet.com/docs/opik): one trace per agent run, with a span
for each route decision, model call and tool call, so a wrong answer can be traced back to the
step that caused it.

Off unless enabled, and optional: if `opik` isn't installed or errors, Flow behaves as if tracing
were off, and tracing never raises into a chat. Site config (site_config.json):

    flow_opik_enabled          true to turn it on (default false)
    flow_opik_project          Opik project name (default "flow")
    flow_opik_url              Opik host, for a self-hosted server (default: Opik's own config)
    flow_opik_api_key          Comet API key, if OPIK_API_KEY can't be set in the environment
    flow_opik_workspace        Comet workspace, if OPIK_WORKSPACE can't be set
    flow_opik_capture_content  false to send names, timings and token counts only, without
                               messages, tool arguments or tool results (default true)

Traces carry the user's messages and the records tools read, so they go to the Opik server you
configure. Turn content capture off if that server shouldn't see ERP data.
"""

from __future__ import annotations

import datetime
import logging
import traceback
from typing import Any

import frappe

DEFAULT_PROJECT = "flow"

_clients: dict[tuple, Any] = {}


def enabled() -> bool:
	# The test suite runs agents on a site that has tracing on; those runs must not reach Opik.
	# Tests of tracing itself set `frappe.flags.flow_opik_in_tests`.
	if frappe.flags.in_test and not frappe.flags.get("flow_opik_in_tests"):
		return False
	return bool(frappe.conf.get("flow_opik_enabled"))


def now() -> datetime.datetime:
	return datetime.datetime.now(datetime.timezone.utc)


def start(name: str, *, input: Any, thread_id: str | None = None, metadata: dict[str, Any] | None = None):
	"""Open a trace for one agent run; a no-op object when tracing is off or unavailable."""
	if not enabled():
		return NO_TRACE
	try:
		client = _client()
		if client is None:
			return NO_TRACE
		from opik import id_helpers

		return RunTrace(
			client,
			id_helpers.generate_id(),
			name=name,
			input=input,
			thread_id=thread_id,
			metadata={"user": frappe.session.user, "site": frappe.local.site, **(metadata or {})},
		)
	except Exception:
		_warn("could not start Opik trace")
		return NO_TRACE


class RunTrace:
	"""One run's Opik trace. Spans are sent as they finish, against an ID made up front; the trace
	itself is sent once, when the run ends (updating a trace just after creating it can lose data
	with Opik's batching). Every method swallows errors: tracing must not break chat."""

	def __init__(self, client: Any, trace_id: str, **fields: Any):
		self._client = client
		self._id = trace_id
		self._fields = fields
		self._started = now()

	def span(
		self,
		name: str,
		*,
		type: str = "general",
		input: Any = None,
		output: Any = None,
		started: datetime.datetime | None = None,
		model: str | None = None,
		provider: str | None = None,
		usage: dict[str, int] | None = None,
		metadata: dict[str, Any] | None = None,
		error: BaseException | str | None = None,
	) -> None:
		"""Record a finished step. `started` is when it began; it ends now."""
		try:
			self._client.span(
				trace_id=self._id,
				name=name,
				type=type,
				input=_content(input),
				output=_content(output),
				start_time=started,
				end_time=now(),
				model=model,
				provider=provider,
				usage=usage or None,
				metadata=metadata,
				error_info=_error_info(error) if error else None,
				project_name=_project(),
			)
		except Exception:
			_warn("could not record Opik span")

	def finish(self, output: Any = None, *, metadata: dict[str, Any] | None = None) -> None:
		self._send(output=_content(output), extra=metadata)

	def fail(self, error: BaseException | str) -> None:
		self._send(error_info=_error_info(error))

	def _send(self, extra: dict[str, Any] | None = None, **fields: Any) -> None:
		try:
			values = {**self._fields, **fields}
			values["input"] = _content({"messages": values["input"]})
			values["metadata"] = {**values["metadata"], **(extra or {})}
			self._client.trace(
				id=self._id,
				start_time=self._started,
				end_time=now(),
				tags=["flow"],
				project_name=_project(),
				**values,
			)
		except Exception:
			_warn("could not send Opik trace")


class _NoTrace:
	def span(self, *args: Any, **kwargs: Any) -> None:
		pass

	def finish(self, *args: Any, **kwargs: Any) -> None:
		pass

	def fail(self, *args: Any, **kwargs: Any) -> None:
		pass


NO_TRACE = _NoTrace()


def _project() -> str:
	return frappe.conf.get("flow_opik_project") or DEFAULT_PROJECT


def _client() -> Any:
	"""The Opik client for this site's settings, built once; None when `opik` isn't installed."""
	key = (
		frappe.conf.get("flow_opik_url"),
		frappe.conf.get("flow_opik_api_key"),
		frappe.conf.get("flow_opik_workspace"),
	)
	if key not in _clients:
		try:
			import opik
		except ImportError:
			_clients[key] = None
		else:
			host, api_key, workspace = key
			_clients[key] = opik.Opik(project_name=_project(), host=host, api_key=api_key, workspace=workspace)
	return _clients[key]


def _content(value: Any) -> dict[str, Any] | None:
	"""Opik wants span input/output as a dict; with content capture off, nothing is sent."""
	if value is None:
		return None
	if not frappe.conf.get("flow_opik_capture_content", True):
		return {"captured": False}
	return value if isinstance(value, dict) else {"value": value}


def _error_info(error: BaseException | str) -> dict[str, str]:
	if isinstance(error, BaseException):
		return {
			"exception_type": type(error).__name__,
			"message": str(error),
			"traceback": "".join(traceback.format_exception(error)),
		}
	return {"exception_type": "Error", "message": str(error), "traceback": ""}


def _warn(message: str) -> None:
	logging.getLogger("flow.tracing").warning(message, exc_info=True)
