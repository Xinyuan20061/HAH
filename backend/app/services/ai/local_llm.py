"""Local fallback LLM: Qwen2.5-0.5B-Instruct (int8 ONNX) on CPU.

Serves as the second rung of the provider chain (DeepSeek -> local -> rules).
It only promises availability, not DeepSeek-grade quality: when the cloud is
down the app can still answer basic questions from local weights plus the RAG
knowledge snippet. Complex intents still degrade explicitly to rules advice.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

try:
    import numpy as np
except ImportError:  # pragma: no cover - container can run without local fallback weights
    np = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

CHAT_TEMPLATE = (
    "<|im_start|>system\n{system}<|im_end|>\n"
    "<|im_start|>user\n{message}<|im_end|>\n<|im_start|>assistant\n"
)
IM_END_ID = 151645  # <|im_end|>
PAD_ID = 151643
VOCAB_SIZE = 151936


class LocalLLMUnavailable(RuntimeError):
    """Raised when the local model directory or runtime cannot be used."""


class QwenLocalGenerator:
    def __init__(self, model_dir: str, max_new_tokens: int = 200):
        self.model_dir = model_dir
        self.max_new_tokens = max(16, min(int(max_new_tokens), 512))
        # int8 of onnx-community's 0.5B export is quality-degraded on CPU
        # (verified: it produces gibberish), so prefer q4f16, then fp32.
        candidates = ["model_q4f16.onnx", "model.onnx", "model_int8.onnx"]
        tokenizer_path = os.path.join(model_dir, "tokenizer.json")
        onnx_path = None
        for name in candidates:
            candidate = os.path.join(model_dir, "onnx", name)
            if os.path.isfile(candidate):
                onnx_path = candidate
                break
        if onnx_path is None or not os.path.isfile(tokenizer_path):
            raise LocalLLMUnavailable(
                f"本地模型不完整：{model_dir}（缺少 ONNX 权重或 tokenizer.json）"
            )
        if np is None:
            raise LocalLLMUnavailable("本地引擎依赖 numpy，当前环境未安装。")
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._session = ort.InferenceSession(
            onnx_path, providers=["CPUExecutionProvider"]
        )
        self._tokenizer = Tokenizer.from_file(tokenizer_path)
        self._num_layers = sum(
            1
            for name in self._session.get_inputs()
            if name.name.startswith("past_key_values.") and name.name.endswith(".key")
        )
        if self._num_layers == 0:
            raise LocalLLMUnavailable("ONNX 模型缺少 KV cache 输入，格式不兼容。")

    def _encode(self, text: str) -> np.ndarray:
        ids = self._tokenizer.encode(text).ids
        return np.asarray(ids, dtype=np.int64)

    def _run(self, input_ids, past_kv, attention_len):
        """One forward pass. Returns (logits, new past_kv)."""
        feed = {
            "input_ids": input_ids,
            "attention_mask": np.ones((1, attention_len), dtype=np.int64),
            "position_ids": np.arange(
                attention_len - input_ids.shape[1], attention_len, dtype=np.int64
            ).reshape(1, -1),
        }
        for layer in range(self._num_layers):
            feed[f"past_key_values.{layer}.key"] = past_kv[layer][0]
            feed[f"past_key_values.{layer}.value"] = past_kv[layer][1]
        outputs = self._session.run(None, feed)
        logits = outputs[0]
        new_past = []
        for layer in range(self._num_layers):
            key = outputs[1 + layer * 2]
            value = outputs[2 + layer * 2]
            new_past.append((key, value))
        return logits, new_past

    @staticmethod
    def _sample(logits_row) -> int:
        """Greedy sampling; deterministic and stable for the fallback engine."""
        row = np.asarray(logits_row, dtype=np.float32)
        if not np.all(np.isfinite(row)):
            raise LocalLLMUnavailable("本地引擎输出非有限数值，已停止生成。")
        return int(np.argmax(row))

    def generate(self, system: str, message: str) -> str:
        """Run the chat template and decode a complete assistant answer."""
        prompt = CHAT_TEMPLATE.format(system=system, message=message)
        input_ids = self._encode(prompt)
        if input_ids.size == 0:
            raise LocalLLMUnavailable("提示词编码为空。")
        if input_ids.size > 2048:
            raise LocalLLMUnavailable("提示词过长（超过 2048 token），本地引擎拒绝。")

        empty_past = [
            (
                np.zeros((1, 2, 0, 64), dtype=np.float32),
                np.zeros((1, 2, 0, 64), dtype=np.float32),
            )
            for _ in range(self._num_layers)
        ]
        # Prefill: process the whole prompt, keep only the last logits row.
        logits, past = self._run(
            input_ids.reshape(1, -1), empty_past, int(input_ids.size)
        )
        generated = [int(self._sample(logits[0, -1, :]))]
        total_len = int(input_ids.size) + 1
        for _ in range(self.max_new_tokens - 1):
            if generated[-1] == IM_END_ID:
                break
            next_input = np.asarray([[generated[-1]]], dtype=np.int64)
            logits, past = self._run(next_input, past, total_len)
            token = int(self._sample(logits[0, -1, :]))
            generated.append(token)
            total_len += 1
            if token == IM_END_ID:
                break

        # Strip special tokens, decode as one sequence (per-token decode of
        # multi-byte BPE pieces would corrupt UTF-8).
        stop_ids = {IM_END_ID, PAD_ID}
        kept = [t for t in generated if t not in stop_ids]
        text = self._tokenizer.decode(kept).strip()
        return text if text else "（本地引擎未能生成有效回答，请稍后重试。）"


_local_generators: dict = {}
_local_generator_lock = asyncio.Lock()


async def get_local_generator(model_dir: str, max_new_tokens: int = 200):
    """Lazily build a process-wide generator per model directory."""
    if model_dir in _local_generators:
        return _local_generators[model_dir]
    async with _local_generator_lock:
        if model_dir not in _local_generators:
            _local_generators[model_dir] = await asyncio.to_thread(
                QwenLocalGenerator, model_dir, max_new_tokens
            )
        return _local_generators[model_dir]
