# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Tools that look outside the site, for questions its own tools can't answer:

- frappe_docs  the official documentation at docs.frappe.io (Frappe Framework, ERPNext, HR,
               CRM, Helpdesk, Cloud…), through that site's own search: the best page's text
- web_search   the public web, when the documentation has nothing

Uses DuckDuckGo's HTML results page — no API key, but an unofficial endpoint that can rate-limit
or change without notice. Only the query the model writes is sent; no record data. Site config
`flow_web_search_enabled` = 0 turns it off.
"""

from __future__ import annotations

import re
from typing import Annotated, Any
from urllib.parse import parse_qs, urlparse

import frappe

from flow.lib.tool import tool

SEARCH_URL = "https://html.duckduckgo.com/html/"
DOCS_HOST = "docs.frappe.io"
DOCS_SEARCH_URL = f"https://{DOCS_HOST}/api/method/wiki.frappe_wiki.doctype.wiki_document.search.search"
DOCS_MORE_LIMIT = 4
# The model reads the page in a small context window; the rest is a link away.
DOCS_TEXT_LIMIT = 3500
RESULTS_LIMIT = 5
TIMEOUT_SECONDS = 10
QUERY_LIMIT = 200


@tool
def web_search(
	query: Annotated[str, 'Search words, e.g. "ERPNext give a role permission on a doctype". No record IDs or personal data.'],
) -> dict[str, Any]:
	"""Search the public web when no other tool can answer: how something works in general,
	what a concept means, steps documented online. Returns titles, links and snippets; answer
	from them in your own words and name the source links. Use the site's own tools first for
	anything about this site's data."""
	if _turned_off():
		raise ValueError("Web search is turned off on this site.")
	query = " ".join(str(query or "").split())[:QUERY_LIMIT]
	if not query:
		raise ValueError("Give the search words.")
	results = _duckduckgo(query)
	if not results:
		return {"query": query, "results": [], "answer_hint": "No web results found."}
	return {"query": query, "results": results, "source": "DuckDuckGo"}


def _turned_off() -> bool:
	return str(frappe.conf.get("flow_web_search_enabled")).strip().lower() in ("0", "false", "no")


def _duckduckgo(query: str) -> list[dict[str, str]]:
	import requests
	from bs4 import BeautifulSoup

	try:
		response = requests.post(
			SEARCH_URL,
			data={"q": query},
			headers={"User-Agent": "Mozilla/5.0 (compatible; FlowAssistant/1.0)"},
			timeout=TIMEOUT_SECONDS,
		)
		response.raise_for_status()
	except requests.RequestException as e:
		raise ValueError(f"Web search is unavailable right now ({type(e).__name__}).") from e

	soup = BeautifulSoup(response.text, "html.parser")
	results = []
	for item in soup.select("div.result"):
		if "result--ad" in (item.get("class") or []):
			continue
		link = item.select_one("a.result__a")
		if not link or not link.get("href"):
			continue
		snippet = item.select_one(".result__snippet")
		results.append(
			{
				"title": link.get_text(" ", strip=True),
				"url": _real_url(link["href"]),
				"snippet": snippet.get_text(" ", strip=True) if snippet else "",
			}
		)
		if len(results) >= RESULTS_LIMIT:
			break
	return results


def _real_url(href: str) -> str:
	"""DuckDuckGo sometimes wraps links in a redirect (//duckduckgo.com/l/?uddg=<url>)."""
	parsed = urlparse(href if "://" in href else f"https:{href}")
	if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
		target = parse_qs(parsed.query).get("uddg")
		if target:
			return target[0]
	return href


@tool
def frappe_docs(
	query: Annotated[str, 'What to look up, e.g. "role permissions manager" or "make payment entry against sales invoice".'],
) -> dict[str, Any]:
	"""Look something up in the official Frappe / ERPNext documentation (docs.frappe.io): how a
	feature works, the steps to do something, what a setting means. Returns the best page's text
	and links to other matching pages; answer from that text in your own words and give the page
	link. Use the site's own tools first for anything about this site's data."""
	if _turned_off():
		raise ValueError("Looking things up online is turned off on this site.")
	query = " ".join(str(query or "").split())[:QUERY_LIMIT]
	if not query:
		raise ValueError("Give the words to look up.")
	pages = _docs_search(query)
	if not pages:
		return {"query": query, "page": None, "more": [], "answer_hint": "Nothing found in the Frappe documentation."}
	best = pages[0]
	return {
		"query": query,
		"page": {**best, "text": _page_text(best["url"]) or best["snippet"]},
		"more": [{"title": p["title"], "url": p["url"]} for p in pages[1:DOCS_MORE_LIMIT + 1]],
	}


def _docs_search(query: str) -> list[dict[str, str]]:
	"""Pages docs.frappe.io's own search finds (it is a Frappe Wiki site), best first: pages of
	the apps this site runs before others (the docs also cover Frappe Cloud, Books…), and current
	pages before old versioned copies (…/v12/…)."""
	import requests

	try:
		response = requests.get(
			DOCS_SEARCH_URL,
			params={"query": query},
			headers={"User-Agent": "Mozilla/5.0 (compatible; FlowAssistant/1.0)"},
			timeout=TIMEOUT_SECONDS,
		)
		response.raise_for_status()
		found = (response.json().get("message") or {}).get("results") or []
	except (requests.RequestException, ValueError) as e:
		raise ValueError(f"The Frappe documentation can't be reached right now ({type(e).__name__}).") from e

	installed = _installed_app_words()
	pages = []
	for i, item in enumerate(found):
		route = str(item.get("route") or "").strip("/")
		if not route:
			continue
		parts = route.lower().split("/")
		pages.append(
			{
				"title": _plain(item.get("title")),
				"url": f"https://{DOCS_HOST}/{route}",
				"snippet": _plain(item.get("content"))[:300],
				"rank": (parts[0] not in installed, any(re.fullmatch(r"v\d+", p) for p in parts), i),
			}
		)
	pages.sort(key=lambda p: p.pop("rank"))
	return pages


def _plain(html: Any) -> str:
	return re.sub(r"<[^>]+>", "", str(html or "")).strip()


def _installed_app_words() -> set[str]:
	"""Names and title words of the installed apps ("erpnext", "hrms", "hr", "crm", "framework"…),
	to match docs sections (docs.frappe.io/<section>/...)."""
	words = set()
	for app in frappe.get_installed_apps():
		words.add(app.lower())
		title = (frappe.get_hooks("app_title", app_name=app) or [app])[0]
		words.update(w.lower() for w in title.split())
	return words


def _page_text(url: str) -> str:
	"""A docs page as Markdown (docs.frappe.io serves <page>.md), without its front matter."""
	import requests

	try:
		response = requests.get(
			f"{url}.md", headers={"User-Agent": "Mozilla/5.0 (compatible; FlowAssistant/1.0)"}, timeout=TIMEOUT_SECONDS
		)
		response.raise_for_status()
	except requests.RequestException:
		return ""
	text = response.text
	if text.startswith("---"):
		end = text.find("\n---", 3)
		text = text[end + 4 :] if end != -1 else text
	text = text.strip()
	return text if len(text) <= DOCS_TEXT_LIMIT else text[:DOCS_TEXT_LIMIT].rsplit("\n", 1)[0] + "\n…"

