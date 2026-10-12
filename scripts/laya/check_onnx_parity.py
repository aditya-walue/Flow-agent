# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Check Flow's PyTorch-free ONNX runner (flow/lib/laya_onnx.py) against Laya's own agent.

1. Token level: for every message and question, the sequence and option-marker positions Flow
   builds must be identical to laya.common.build_sequence.
2. Output level: the ONNX runner and the PyTorch checkpoint must make the same decisions, with
   probabilities within a small tolerance (fp32 ONNX vs eager PyTorch differ only in rounding).

    ./env/bin/python apps/flow/scripts/laya/check_onnx_parity.py \\
        --torch-model apps/flow/laya_data/model --onnx-model apps/flow/laya_data/onnx --data apps/flow/laya_data/data
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PROB_TOLERANCE = 0.02


def main() -> None:
	parser = argparse.ArgumentParser()
	parser.add_argument("--torch-model", required=True)
	parser.add_argument("--onnx-model", required=True)
	parser.add_argument("--data", required=True)
	parser.add_argument("--splits", nargs="+", default=["val", "test", "real_test"])
	args = parser.parse_args()

	import laya
	from laya.common import build_sequence
	from transformers import AutoTokenizer

	from flow.lib import laya_onnx
	from flow.lib.laya_router import FINETUNED_QUESTIONS

	onnx_agent = laya_onnx.OnnxLaya(args.onnx_model)
	torch_agent = laya.load(args.torch_model)
	hf_tok = AutoTokenizer.from_pretrained(Path(args.torch_model) / "tokenizer")
	internal = {qid: laya_onnx._internal(q) for qid, q in FINETUNED_QUESTIONS.items()}

	totals = {"messages": 0, "token_mismatch": 0, "decision_mismatch": 0, "max_prob_diff": 0.0}
	for split in args.splits:
		rows = [json.loads(line) for line in open(Path(args.data) / f"{split}.jsonl")]
		for row in rows:
			text = row["text"]
			totals["messages"] += 1
			state_ids = onnx_agent.tok(text.replace(onnx_agent.tok.mask_token, " "), add_special_tokens=False)["input_ids"]
			for qid, q in internal.items():
				ours = laya_onnx._build_sequence(onnx_agent.tok, q, state_ids, onnx_agent.max_len, onnx_agent.head_max_len)
				theirs = build_sequence(hf_tok, text, q, onnx_agent.max_len, onnx_agent.head_max_len)
				if ours != tuple(theirs[:2]) and (list(ours[0]), list(ours[1])) != (list(theirs[0]), list(theirs[1])):
					totals["token_mismatch"] += 1
			a = onnx_agent.system_one(text, FINETUNED_QUESTIONS)["answers"]
			b = torch_agent.system_one(text, FINETUNED_QUESTIONS)["answers"]
			same = a["intent"]["choice"] == b["intent"]["choice"] and (a["asks_count"]["noul"] >= 0.5) == (
				b["asks_count"]["noul"] >= 0.5
			)
			if not same:
				totals["decision_mismatch"] += 1
			diffs = [abs(a["intent"]["probabilities"][k] - b["intent"]["probabilities"][k]) for k in a["intent"]["probabilities"]]
			diffs.append(abs(a["asks_count"]["noul"] - b["asks_count"]["noul"]))
			totals["max_prob_diff"] = max(totals["max_prob_diff"], max(diffs))
		print(f"{split}: {len(rows)} messages checked")

	ok = totals["token_mismatch"] == 0 and totals["decision_mismatch"] == 0 and totals["max_prob_diff"] <= PROB_TOLERANCE
	print(json.dumps(totals, indent=1))
	print("PARITY OK" if ok else "PARITY FAILED")
	raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
	main()
