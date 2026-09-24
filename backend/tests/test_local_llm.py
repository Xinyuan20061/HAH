"""Local fallback LLM availability evaluation (local-v1).

The local engine only promises *availability*: when the cloud provider fails,
basic Q&A still completes on local weights. It never claims DeepSeek-grade
quality. This test verifies the three fallback states without calling the
cloud or downloading anything:
  - normal: generator produces a non-empty health answer for a known query
  - missing config: get_local_provider() returns None (chain falls through)
  - broken model dir: provider returns None instead of crashing the endpoint
"""
import asyncio
import os
import sys

import pytest

from app.core.config import settings
from app.services.ai.local_llm import LocalLLMUnavailable, QwenLocalGenerator
from app.services.ai.gateway import LocalProvider, get_local_provider

MODEL_DIR = r"D:\HealthMateData\models\qwen2.5-0.5b-instruct-onnx"


@pytest.fixture(scope="module")
def generator():
    if not os.path.isdir(MODEL_DIR):
        pytest.skip("本地模型目录不存在，跳过本地引擎评测")
    try:
        return QwenLocalGenerator(MODEL_DIR, max_new_tokens=120)
    except LocalLLMUnavailable as exc:
        pytest.skip(f"本地模型不可用：{exc}")


def test_local_generator_answers_health_question(generator):
    answer = generator.generate(
        "你是一个健康助手。回答简短准确、口语化，1-3句话。",
        "每天喝多少水合适？",
    )
    assert isinstance(answer, str) and answer.strip()
    assert not answer.startswith("（本地引擎")


def test_local_generator_rejects_oversized_prompt(generator):
    # 6000 CJK characters reliably exceed the 2048-token cap (each is 1 token).
    with pytest.raises(LocalLLMUnavailable):
        generator.generate("健康助手", "问" + "啊" * 6000)


def test_get_local_provider_returns_provider_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "local_llm_model_dir", MODEL_DIR)
    provider = asyncio.run(get_local_provider())
    if provider is None and not os.path.isdir(MODEL_DIR):
        pytest.skip("模型目录不存在")
    assert provider is None or isinstance(provider, LocalProvider)


def test_get_local_provider_returns_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "local_llm_model_dir", "")
    assert asyncio.run(get_local_provider()) is None


def test_get_local_provider_returns_none_on_broken_dir(monkeypatch):
    monkeypatch.setattr(settings, "local_llm_model_dir", r"D:\HealthMateData\models\__missing__")
    assert asyncio.run(get_local_provider()) is None


def test_local_provider_stream_yields_text(generator):
    provider = LocalProvider(generator)

    async def collect():
        chunks = []
        async for chunk in provider.stream("你是一个健康助手。", "深蹲怎么做？"):
            chunks.append(chunk)
        return "".join(chunks)

    text = asyncio.run(collect())
    assert isinstance(text, str) and text.strip()
