from dataclasses import dataclass
from fastapi import HTTPException
import httpx
from app.core.config import settings
from app.core.crypto import decrypt_secret
from app.core.url_security import validate_ai_base_url, resolve_ai_host
from app.services.ai.local_llm import LocalLLMUnavailable, get_local_generator


@dataclass
class AIResult:
    text: str
    provider: str


class UnavailableProvider:
    provider_name = "unavailable"

    async def chat(self, system: str, message: str) -> AIResult:
        raise HTTPException(
            503, "DeepSeek 尚未配置，请在 AI 设置中配置 Key；普通健康记录仍可使用。"
        )

    async def stream(self, system: str, message: str):
        result = await self.chat(system, message)
        yield result.text


class LocalProvider:
    """Second rung of the fallback chain (DeepSeek -> local -> rules).

    Only promises availability: basic Q&A continues on local weights when the
    cloud is unreachable. It never claims DeepSeek-grade quality.
    """

    provider_name = "local"

    def __init__(self, generator):
        self._generator = generator

    async def chat(self, system: str, message: str) -> AIResult:
        import asyncio

        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(self._generator.generate, system, message[:1500]),
                timeout=settings.local_llm_timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise HTTPException(503, "本地引擎响应超时。") from None
        except LocalLLMUnavailable:
            raise HTTPException(503, "本地引擎不可用。") from None
        except Exception:
            raise HTTPException(503, "本地引擎生成失败。") from None
        if not text.strip():
            raise HTTPException(503, "本地引擎生成失败。")
        return AIResult(text, "local")

    async def stream(self, system: str, message: str):
        result = await self.chat(system, message)
        for index in range(0, len(result.text), 40):
            yield result.text[index : index + 40]


async def get_local_provider():
    """Build the local fallback provider when weights are configured.

    Returns None when the model directory is missing or cannot load, so callers
    can silently continue to the next fallback rung.
    """
    if not settings.local_llm_model_dir.strip():
        return None
    try:
        generator = await get_local_generator(
            settings.local_llm_model_dir, settings.local_llm_max_new_tokens
        )
    except (LocalLLMUnavailable, Exception):
        return None
    return LocalProvider(generator)


class DeepSeekProvider:
    def __init__(
        self, api_key: str, base_url: str, model: str, provider_name: str = "deepseek"
    ):
        self.api_key, self.base_url, self.model, self.provider_name = (
            api_key,
            base_url.rstrip("/"),
            model,
            provider_name,
        )

    async def stream(self, system: str, message: str):
        # Buffer a reliable upstream response. All failures happen before producing partial output.
        result = await self.chat(system, message)
        for index in range(0, len(result.text), 40):
            yield result.text[index : index + 40]

    async def chat(self, system: str, message: str) -> AIResult:
        try:
            base = validate_ai_base_url(self.base_url)
            original = httpx.URL(base + "/chat/completions")
            url, headers, extensions = (
                original,
                {"Authorization": f"Bearer {self.api_key}"},
                {},
            )
            if settings.is_production:
                url = original.copy_with(host=resolve_ai_host(base))
                headers["Host"] = original.netloc.decode("ascii")
                extensions["sni_hostname"] = original.host
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": message},
                ],
                "temperature": 0.4,
                "stream": False,
            }
            async with httpx.AsyncClient(
                timeout=40, follow_redirects=False, trust_env=False
            ) as client:
                response = await client.post(
                    url, headers=headers, extensions=extensions, json=payload
                )
            if not response.is_success:
                status = response.status_code
                if status in {401, 403}:
                    raise HTTPException(
                        503, "DeepSeek Key 无效或权限不足，请检查 AI 设置。"
                    )
                if status == 402:
                    raise HTTPException(
                        503, "DeepSeek 账户余额不足或欠费，请前往 DeepSeek 控制台充值。"
                    )
                if status == 429:
                    raise HTTPException(503, "DeepSeek 请求限流，请稍后重试。")
                if status == 400:
                    raise HTTPException(
                        503, "DeepSeek 拒绝了请求（400）：请检查模型名是否有效。"
                    )
                raise HTTPException(503, "DeepSeek 服务暂时不可用，请稍后重试。")
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty provider response")
            return AIResult(content, self.provider_name)
        except httpx.TimeoutException:
            raise HTTPException(503, "DeepSeek 响应超时，请稍后重试。") from None
        except httpx.ConnectError as exc:
            raise HTTPException(
                503, f"DeepSeek 网络连接失败（{type(exc.__cause__).__name__ if exc.__cause__ else 'connect'}），请检查服务端出网或 Base URL。"
            ) from None
        except httpx.HTTPStatusError as exc:
            raise HTTPException(503, f"DeepSeek 返回异常状态 {exc.response.status_code}，请检查模型名或权限。") from None
        except (httpx.HTTPError, KeyError, IndexError, ValueError, TypeError) as exc:
            detail = type(exc).__name__
            raise HTTPException(
                503, f"DeepSeek 连接或响应异常（{detail}），请检查 Base URL、DNS 出网或模型名。"
            ) from None


def get_provider(user=None):
    config = getattr(user, "ai_config", None) if user else None
    if config and config.enabled:
        try:
            key = decrypt_secret(config.api_key_encrypted)
        except ValueError:
            raise HTTPException(
                503, "用户 AI Key 无法解密，请重新配置或恢复服务端加密密钥。"
            ) from None
        if key:
            return DeepSeekProvider(key, config.base_url, config.model, "deepseek-user")
        return UnavailableProvider()
    if settings.deepseek_api_key:
        return DeepSeekProvider(
            settings.deepseek_api_key,
            settings.deepseek_base_url,
            settings.deepseek_model,
            "deepseek-system",
        )
    return UnavailableProvider()
