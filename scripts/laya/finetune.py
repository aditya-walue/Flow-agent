# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Fine-tune Laya as Flow's intent classifier, on Apple Silicon (MPS) or CPU.

Uses Laya's own training script (laya_finetune_mps.py, vendored unchanged): its RLCD + soft
cross-entropy loop and its per-type temperature calibration on a held-out slice. Only the data
source is replaced, with the dataset from build_dataset.py, asked exactly the questions Flow asks
at runtime (flow.lib.laya_router.FINETUNED_QUESTIONS).

    ./env/bin/python apps/flow/scripts/laya/finetune.py \\
        --data laya-flow/data --output-dir laya-flow/model --epochs 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import torch
from transformers import AutoTokenizer

sys.path.insert(0, str(Path(__file__).parent))
import laya_finetune_mps as upstream  # noqa: E402

from flow.lib.laya_router import FINETUNED_QUESTIONS  # noqa: E402

# Soft targets: the labelled answer gets most of the mass, the rest is spread thin, so the model
# learns a calibrated preference rather than certainty.
CHOICE_TARGET = 0.92
NOUL_TARGET = 0.95


def gold_for(row: dict) -> dict:
	keys = list(FINETUNED_QUESTIONS["intent"]["criteria"])
	rest = (1 - CHOICE_TARGET) / (len(keys) - 1)
	yes = NOUL_TARGET if row["asks_count"] else 1 - NOUL_TARGET
	return {
		"intent": {"probabilities": {k: CHOICE_TARGET if k == row["intent"] else rest for k in keys}},
		"asks_count": {"probabilities": {"true": yes, "false": 1 - yes}},
	}


def prepare_items(model_dir: str, items_path: str, data_dir: str) -> None:
	model_path = Path(model_dir).resolve()
	cfg = json.loads((model_path / "rl_agent_config.json").read_text())
	cfg = {**cfg, "max_len": 1024, "head_max_len": 256}
	tokenizer = AutoTokenizer.from_pretrained(model_path / "tokenizer")
	rows = [json.loads(line) for line in open(Path(data_dir) / "train.jsonl")]
	items, skipped = [], 0
	for row in rows:
		gold = gold_for(row)
		for qid, question in FINETUNED_QUESTIONS.items():
			item = upstream.build_training_item(tokenizer, cfg, row["text"], question, gold[qid])
			if item is None:
				skipped += 1
			else:
				items.append(item)
	Path(items_path).parent.mkdir(parents=True, exist_ok=True)
	torch.save(items, items_path)
	print(f"{len(items)} training items from {len(rows)} messages; skipped={skipped}")


def main() -> None:
	parser = argparse.ArgumentParser(description="Fine-tune Laya on Flow's intent dataset")
	parser.add_argument("--data", required=True, help="directory with train.jsonl")
	parser.add_argument("--output-dir", required=True)
	parser.add_argument("--model-dir", default=None, help="base checkpoint dir (downloaded if missing)")
	parser.add_argument("--epochs", type=int, default=3)
	parser.add_argument("--micro-batch", type=int, default=4)
	parser.add_argument("--grad-accum", type=int, default=8)
	parser.add_argument("--calib-max", type=int, default=300)
	parser.add_argument("--device", choices=["auto", "mps", "cpu"], default="auto")
	args = parser.parse_args()
	args.no_checkpointing = False

	model_dir = upstream.prepare_model(args.model_dir or str(Path(args.output_dir).parent / "laya_base"))
	items = str(Path(args.output_dir).parent / "train_items.pt")
	prepare_items(model_dir, items, args.data)
	torch.set_float32_matmul_precision("high")
	upstream.train(args, model_dir, items, upstream.choose_device(args.device))


if __name__ == "__main__":
	main()
