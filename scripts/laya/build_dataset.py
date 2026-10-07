# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Build the labelled dataset for fine-tuning Laya as Flow's intent classifier.

Run inside a site (it reads the site's DocTypes, field labels, the error messages in the installed
apps' code and Flow's own chat messages):

    bench --site <site> execute flow_laya_dataset.build --kwargs '{"out_dir": "<dir>"}'
    (or exec this file from `bench --site <site> console` and call build(out_dir))

Output, one JSON object per line ({"text", "intent", "asks_count", "source"}):
    train.jsonl / val.jsonl / test.jsonl  synthetic messages built from this site's real metadata;
                                          DocTypes, error messages and some phrasings are disjoint
                                          across the three splits, so val/test measure
                                          generalisation, not memorisation
    real_test.jsonl                       Flow's real user messages, labelled by hand in a JSON file
                                          kept outside the repo (--real-labels); never trained on
"""

from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path

import frappe

INTENTS = ("HOWTO", "DEFINITION", "WORKFLOW", "RECORD_LOOKUP", "TROUBLESHOOTING")
SEED = 7
PER_INTENT = {"train": 300, "val": 60, "test": 80}

# Phrasings per intent. The last HELD_OUT of each list appear only in the test split, to measure
# how the model handles wording it never saw.
HELD_OUT = 2
TEMPLATES: dict[str, list[str]] = {
	"HOWTO": [
		"how do i create a {dt}",
		"how to add a new {dt}?",
		"how can i make a {dt}",
		"steps to create {a} {dt}",
		"give me the steps to add a {dt}",
		"how to create new {dt} give me the steps",
		"what is the procedure to enter a new {dt}",
		"guide me through creating a {dt}",
		"how should i set up a {dt}",
		"walk me through adding a {dt} in the system",
		"how do i record a new {dt}?",
		"can you tell me how to create a {dt}",
		"explain how to add a {dt}",
		"where do i go to create a {dt} and what do i fill",
	],
	"DEFINITION": [
		"what is a {dt}?",
		"what does {dt} mean",
		"what is the {field} field in {dt}",
		"what does {field} mean on a {dt}",
		"explain what a {dt} is",
		"what is {dt} used for",
		"what is the difference between {dt} and {dt2}",
		"define {dt}",
		"what does the {field} on {dt} do",
		"meaning of {field} in {dt}",
		"why would i use a {dt}",
		"is a {dt} the same as a {dt2}?",
		"what kind of record is a {dt}",
		"tell me what {field} represents in a {dt}",
	],
	"WORKFLOW": [
		"how does the {dt} approval process work",
		"what happens after a {dt} is submitted",
		"what is the process from {dt} to {dt2}",
		"walk me through the {process} process",
		"explain the {process} flow end to end",
		"what are the stages of {process}",
		"what comes after {dt} in the {process} cycle",
		"how do {dt} and {dt2} connect in the process",
		"describe the full {process} cycle",
		"who approves a {dt} and what happens next",
		"what is the sequence of documents in {process}",
		"what steps follow once a {dt} is approved",
		"how does a {dt} move through its workflow states",
		"in what order do {dt} and {dt2} happen",
	],
	"RECORD_LOOKUP": [
		"how many {dt_pl} are there?",
		"how many {dt_pl} are present in system",
		"count the {dt_pl}",
		"number of {dt_pl} we have",
		"show me the {dt_pl} created this week",
		"list all {dt_pl}",
		"show the details of {dt} {id}",
		"what is the status of {dt} {id}",
		"which {dt_pl} have {field} {value}",
		"find {dt_pl} where {field} is {value}",
		"get me the latest {dt_pl}",
		"what is the total {field} of {dt_pl}",
		"how many {dt_pl} were made last month",
		"need information of those {dt_pl}",
	],
	"TROUBLESHOOTING": [
		"{error}",
		"getting this error: {error}",
		"Message {error}",
		"why do i get '{error}' when saving a {dt}",
		"{dt} won't save, it says {error}",
		"error while submitting {dt}: {error}",
		"i see {error} what does it mean",
		"how do i fix '{error}'",
		"it fails with {error}",
		"the {dt} form shows {error}",
		"can't create {dt}: {error}",
		"traceback ends with {error}",
		"something is wrong, the system says {error}",
		"help, {error} keeps popping up",
	],
}
# Count phrasings: the `asks_count` noul is true for exactly these RECORD_LOOKUP templates.
COUNT_TEMPLATES = {0, 1, 2, 3, 12}

PROCESSES = [
	"order to cash", "procure to pay", "quote to cash", "hire to retire", "record to report",
	"month end closing", "stock reconciliation", "purchase approval", "leave approval",
	"expense claim", "payroll", "sales return", "material request to purchase", "manufacturing",
	"project billing", "subscription renewal",
]
VALUES = ["Paid", "Open", "Draft", "Closed", "High", "Active", "Pending", "Overdue", "Completed", "test"]



def build(out_dir: str, real_labels: str | None = None) -> dict:
	"""Write the splits to `out_dir`. `real_labels` is a JSON file of hand-labelled real messages
	([{"text", "intent", "asks_count"}], kept outside the repo); they become real_test.jsonl."""
	rng = random.Random(SEED)
	out = Path(out_dir)
	out.mkdir(parents=True, exist_ok=True)

	doctypes = _doctypes()
	errors = _error_messages()
	rng.shuffle(doctypes)
	rng.shuffle(errors)
	splits = {
		"train": (_slice(doctypes, 0.0, 0.7), _slice(errors, 0.0, 0.7)),
		"val": (_slice(doctypes, 0.7, 0.85), _slice(errors, 0.7, 0.85)),
		"test": (_slice(doctypes, 0.85, 1.0), _slice(errors, 0.85, 1.0)),
	}
	summary: dict = {"doctypes": len(doctypes), "error_messages": len(errors)}
	for split, (dts, errs) in splits.items():
		rows = []
		for intent in INTENTS:
			templates = list(enumerate(TEMPLATES[intent]))
			usable = templates if split == "test" else templates[:-HELD_OUT]
			for _ in range(PER_INTENT[split]):
				index, template = rng.choice(usable)
				text = _noisy(rng, _fill(rng, template, dts, errs), rng)
				rows.append(
					{
						"text": text,
						"intent": intent,
						"asks_count": intent == "RECORD_LOOKUP" and index in COUNT_TEMPLATES,
						"source": f"synthetic:{intent}:{index}",
					}
				)
		rng.shuffle(rows)
		_write(out / f"{split}.jsonl", rows)
		summary[split] = len(rows)

	real = _real_messages(real_labels)
	_write(out / "real_test.jsonl", real)
	summary["real_test"] = len(real)
	(out / "summary.json").write_text(json.dumps(summary, indent=2))
	return summary


def _doctypes() -> list[dict]:
	"""Readable, non-child, non-single DocTypes with their user-facing field labels."""
	rows = []
	for name in frappe.get_all("DocType", filters={"istable": 0, "issingle": 0}, pluck="name"):
		meta = frappe.get_meta(name)
		labels = [
			f.label for f in meta.fields
			if f.label and f.fieldtype not in ("Section Break", "Column Break", "Tab Break", "HTML", "Button", "Table")
			and len(f.label) < 40
		]
		if labels:
			rows.append({"name": name, "fields": labels})
	return rows


def _error_messages() -> list[str]:
	"""User-facing error texts raised in the installed apps' Python code."""
	pattern = re.compile(r"frappe\.throw\(\s*_\(\s*(['\"])(.{12,160}?)\1")
	found = set()
	for app in frappe.get_installed_apps():
		root = frappe.get_app_path(app)
		for dirpath, dirnames, filenames in os.walk(root):
			dirnames[:] = [d for d in dirnames if d not in ("node_modules", "tests", "test", "patches", "public")]
			for filename in filenames:
				if not filename.endswith(".py") or filename.startswith("test_"):
					continue
				try:
					text = open(os.path.join(dirpath, filename), encoding="utf-8", errors="ignore").read()
				except OSError:
					continue
				for match in pattern.finditer(text):
					message = match.group(2).strip()
					if len(message.split()) >= 3 and "<" not in message:
						found.add(message)
	return sorted(found)


def _fill(rng: random.Random, template: str, doctypes: list[dict], errors: list[str]) -> str:
	dt, dt2 = rng.sample(doctypes, 2)
	name = dt["name"]
	values = {
		"dt": name.lower() if rng.random() < 0.7 else name,
		"dt2": dt2["name"].lower(),
		"dt_pl": _plural(name.lower()),
		"a": "an" if name[0].lower() in "aeiou" else "a",
		"field": rng.choice(dt["fields"]).lower(),
		"value": rng.choice(VALUES),
		"id": f"{''.join(w[0] for w in name.split()).upper()}-{rng.randint(2024, 2026)}-{rng.randint(1, 999):05d}",
		"process": rng.choice(PROCESSES),
		"error": "",
	}
	if "{error}" in template:
		message = rng.choice(errors)
		# Fill the message's own placeholders ({0}, {1}) the way the app would at runtime.
		message = re.sub(r"\{\d*\}", lambda _m: rng.choice([name, rng.choice(dt["fields"]), str(rng.randint(1, 99))]), message)
		values["error"] = message
	return template.format(**values)


def _noisy(rng: random.Random, text: str, _rng: random.Random) -> str:
	"""Typing variation seen in real messages: case, dropped punctuation, typos, filler."""
	if rng.random() < 0.15:
		text = text.capitalize()
	if rng.random() < 0.25:
		text = text.rstrip("?.!")
	if rng.random() < 0.1:
		words = text.split()
		i = rng.randrange(len(words))
		if len(words[i]) > 3:
			w = list(words[i])
			j = rng.randrange(len(w) - 1)
			w[j], w[j + 1] = w[j + 1], w[j]
			words[i] = "".join(w)
		text = " ".join(words)
	if rng.random() < 0.08:
		text = rng.choice(["pls ", "please ", "hey ", "quick question, "]) + text
	return text


def _real_messages(labels_path: str | None) -> list[dict]:
	"""Hand-labelled real messages that still exist in this site's Flow chats or Route Log."""
	if not labels_path or not Path(labels_path).exists():
		return []
	labels = {row["text"]: row for row in json.loads(Path(labels_path).read_text())}
	texts = {
		(m or "").strip().replace("\n", " ⏎ ")
		for m in frappe.get_all("Flow Session Message", filters={"role": "user"}, pluck="content")
		+ frappe.get_all("Flow Route Log", pluck="input")
	}
	return [
		{"text": text.replace(" ⏎ ", "\n"), "intent": labels[text]["intent"], "asks_count": labels[text]["asks_count"], "source": "real"}
		for text in sorted(texts)
		if text in labels
	]


def _plural(word: str) -> str:
	if word.endswith("y") and not word.endswith(("ay", "ey", "oy")):
		return word[:-1] + "ies"
	if word.endswith(("s", "x", "ch", "sh")):
		return word + "es"
	return word + "s"


def _slice(items: list, start: float, end: float) -> list:
	return items[int(len(items) * start) : int(len(items) * end)]


def _write(path: Path, rows: list[dict]) -> None:
	with open(path, "w") as f:
		for row in rows:
			f.write(json.dumps(row, ensure_ascii=False) + "\n")
