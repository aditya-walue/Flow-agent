# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Evaluate a Laya checkpoint as Flow's intent classifier.

Asks exactly the production questions (flow.lib.laya_router.FINETUNED_QUESTIONS) and reports, per
split: accuracy, confusion matrix, per-intent precision/recall, asks-count accuracy, and
calibration of the gating confidence (`answer_confidence`): expected calibration error (ECE), a
reliability table, and accuracy vs. coverage at each candidate threshold.

    ./env/bin/python apps/flow/scripts/laya/evaluate.py --model laya-flow/model \\
        --data laya-flow/data --splits val test real_test --out laya-flow/reports/finetuned.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import laya

from flow.lib.laya_router import FINETUNED_QUESTIONS

INTENTS = list(FINETUNED_QUESTIONS["intent"]["criteria"])
THRESHOLDS = [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
BINS = 10


def evaluate(agent, rows: list[dict]) -> dict:
	preds, latencies = [], []
	for row in rows:
		start = time.perf_counter()
		answers = agent.system_one(row["text"], FINETUNED_QUESTIONS)["answers"]
		latencies.append((time.perf_counter() - start) * 1000)
		intent = answers["intent"]
		preds.append(
			{
				"pred": intent["choice"],
				"conf": float(intent.get("answer_confidence", intent.get("confidence", 0))),
				"asks_count": float(answers["asks_count"]["noul"]),
				"gold": row["intent"],
				"gold_count": bool(row["asks_count"]),
				"text": row["text"],
			}
		)
	return report(preds, latencies)


def report(preds: list[dict], latencies: list[float]) -> dict:
	n = len(preds)
	correct = [p["pred"] == p["gold"] for p in preds]
	confusion = {g: {p: 0 for p in INTENTS} for g in INTENTS}
	for p in preds:
		confusion[p["gold"]][p["pred"]] += 1
	per_intent = {}
	for intent in INTENTS:
		tp = confusion[intent][intent]
		predicted = sum(confusion[g][intent] for g in INTENTS)
		actual = sum(confusion[intent].values())
		per_intent[intent] = {
			"support": actual,
			"precision": round(tp / predicted, 3) if predicted else None,
			"recall": round(tp / actual, 3) if actual else None,
		}
	# Calibration of the confidence Flow gates on: does 0.9 confidence mean 90% correct?
	reliability, ece = [], 0.0
	for b in range(BINS):
		lo, hi = b / BINS, (b + 1) / BINS
		group = [(p["conf"], ok) for p, ok in zip(preds, correct) if lo <= p["conf"] < hi or (b == BINS - 1 and p["conf"] == 1.0)]
		if group:
			avg_conf = sum(c for c, _ in group) / len(group)
			acc = sum(ok for _, ok in group) / len(group)
			ece += len(group) / n * abs(acc - avg_conf)
			reliability.append({"bin": f"{lo:.1f}-{hi:.1f}", "n": len(group), "avg_confidence": round(avg_conf, 3), "accuracy": round(acc, 3)})
	thresholds = []
	for t in THRESHOLDS:
		kept = [ok for p, ok in zip(preds, correct) if p["conf"] >= t]
		thresholds.append(
			{
				"threshold": t,
				"coverage": round(len(kept) / n, 3),
				"accuracy": round(sum(kept) / len(kept), 3) if kept else None,
				"routed_wrong": len(kept) - sum(kept),
			}
		)
	count_ok = sum((p["asks_count"] >= 0.5) == p["gold_count"] for p in preds)
	latencies = sorted(latencies)
	return {
		"n": n,
		"accuracy": round(sum(correct) / n, 4),
		"asks_count_accuracy": round(count_ok / n, 4),
		"ece": round(ece, 4),
		"confusion": confusion,
		"per_intent": per_intent,
		"reliability": reliability,
		"thresholds": thresholds,
		"latency_ms": {"p50": round(latencies[n // 2]), "p95": round(latencies[int(n * 0.95) - 1])},
		"errors": [
			{"text": p["text"][:120], "gold": p["gold"], "pred": p["pred"], "conf": round(p["conf"], 3)}
			for p, ok in zip(preds, correct)
			if not ok
		][:25],
	}


def main() -> None:
	parser = argparse.ArgumentParser()
	parser.add_argument("--model", required=True, help="checkpoint path or Laya model id/alias")
	parser.add_argument("--data", required=True)
	parser.add_argument("--splits", nargs="+", default=["val", "test", "real_test"])
	parser.add_argument("--out", required=True)
	args = parser.parse_args()

	agent = laya.load(args.model)
	agent.system_one("warm up", FINETUNED_QUESTIONS)
	results = {"model": args.model}
	for split in args.splits:
		rows = [json.loads(line) for line in open(Path(args.data) / f"{split}.jsonl")]
		results[split] = evaluate(agent, rows)
		r = results[split]
		print(f"{split:10s} n={r['n']:4d} accuracy={r['accuracy']:.3f} count_acc={r['asks_count_accuracy']:.3f} ece={r['ece']:.3f}")
	Path(args.out).parent.mkdir(parents=True, exist_ok=True)
	Path(args.out).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
	main()
