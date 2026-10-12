# Copyright (c) 2026, Frappe Technologies and contributors
# License: MIT. See LICENSE

"""Flow's PyTorch-free Laya runner, without a real checkpoint: sequence layout, batching and
decoding. Parity with Laya itself on the real model is checked by scripts/laya/check_onnx_parity.py."""

from unittest import TestCase

import numpy as np

from flow.lib import laya_onnx


class FakeTokenizer:
	"""One id per whitespace word; special tokens get fixed ids."""

	mask_token = "[MASK]"
	cls_token_id, sep_token_id, mask_token_id, pad_token_id = 1, 2, 3, 0

	def __init__(self):
		self.vocab = {}

	def __call__(self, text, add_special_tokens=True, truncation=False, max_length=None):
		ids = [self.vocab.setdefault(w, len(self.vocab) + 10) for w in text.split()]
		return {"input_ids": ids[:max_length] if truncation and max_length else ids}


QUESTIONS = {
	"intent": {"type": "choice", "instructions": "kind?", "criteria": {"A": "first", "B": "second", "C": ""}},
	"asks_count": {"type": "noul", "instructions": "count?"},
}


class TestLayaOnnx(TestCase):
	def test_options_render_like_laya(self):
		self.assertEqual(laya_onnx._render_options(laya_onnx._internal(QUESTIONS["intent"])), ["A: first", "B: second", "C"])
		self.assertEqual(
			laya_onnx._render_options(laya_onnx._internal(QUESTIONS["asks_count"])),
			["false: no, the statement does not hold", "true: yes, the statement holds"],
		)

	def test_sequence_layout(self):
		tok = FakeTokenizer()
		q = laya_onnx._internal(QUESTIONS["intent"])
		ids, markers = laya_onnx._build_sequence(tok, q, [100, 101, 102], max_len=64, head_max_len=32)
		self.assertEqual(ids[0], tok.cls_token_id)
		self.assertEqual([ids[m] for m in markers], [tok.mask_token_id] * 3)
		self.assertEqual(ids[-4:], [100, 101, 102, tok.sep_token_id])

	def test_long_state_is_cut_to_max_len(self):
		tok = FakeTokenizer()
		q = laya_onnx._internal(QUESTIONS["asks_count"])
		ids, markers = laya_onnx._build_sequence(tok, q, list(range(500, 900)), max_len=40, head_max_len=24)
		self.assertEqual(len(ids), 40)
		self.assertEqual(ids[-1], tok.sep_token_id)
		self.assertEqual(len(markers), 2)

	def test_collate_pads_rows(self):
		feed = laya_onnx._collate(
			[{"ids": [1, 5, 2], "markers": [1], "qtype": 0}, {"ids": [1, 2], "markers": [0, 1], "qtype": 2}], pad_id=0
		)
		self.assertEqual(feed["input_ids"].tolist(), [[1, 5, 2], [1, 2, 0]])
		self.assertEqual(feed["attention_mask"].tolist(), [[1, 1, 1], [1, 1, 0]])
		self.assertEqual(feed["marker_mask"].tolist(), [[True, False], [True, True]])
		self.assertEqual(feed["qtype"].tolist(), [0, 2])

	def test_decode_applies_temperature_and_shapes_answers(self):
		runner = laya_onnx.OnnxLaya.__new__(laya_onnx.OnnxLaya)
		runner.model_id = "test"
		runner.tok = FakeTokenizer()
		runner.max_len, runner.head_max_len = 128, 64
		runner.temperature = [2.0, 1.0, 1.0]
		runner.temperature_by_options = {}
		runner._lock = __import__("threading").Lock()

		class Session:
			def run(self, names, feed):
				return np.array([[2.0, 0.0, 0.0], [0.0, 3.0, 0.0]], dtype=np.float32), np.array([[0.5], [0.5]])

		runner.session = Session()
		answers = runner.system_one("how many items", QUESTIONS)["answers"]

		intent = answers["intent"]
		self.assertEqual(intent["choice"], "A")
		expected = np.exp(1.0) / (np.exp(1.0) + 2)  # logits / temperature 2
		self.assertAlmostEqual(intent["answer_confidence"], round(expected, 4))
		self.assertAlmostEqual(sum(intent["probabilities"].values()), 1.0, places=3)

		count = answers["asks_count"]
		self.assertEqual(count["type"], "noul")
		self.assertAlmostEqual(count["noul"], round(np.exp(3.0) / (1 + np.exp(3.0)), 4))

	def test_temperature_buckets_and_clamp(self):
		self.assertEqual(laya_onnx._temp_bucket(0, 5), "choice:3-5")
		self.assertEqual(laya_onnx._temp_bucket(2, 2), "noul:2")
		self.assertEqual(laya_onnx._clamp(99), laya_onnx.TEMP_MAX)
		self.assertEqual(laya_onnx._clamp(0.1), laya_onnx.TEMP_MIN)
