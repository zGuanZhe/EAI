from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..core.secrets import read_secret_data


class ProviderError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def extract_provider_text(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list):
        texts = []
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or {}
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                texts.append(message["content"])
            elif isinstance(choice.get("text"), str):
                texts.append(choice["text"])
        if texts:
            return "\n".join(texts).strip()
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    texts = []
    for item in response.get("output", []) or []:
        for content in item.get("content", []) or []:
            if isinstance(content, dict) and isinstance(content.get("text"), str):
                texts.append(content["text"])
    return "\n".join(texts).strip()


class ProviderAdapter:
    def __init__(self, secret_candidates: list[Path]) -> None:
        self.secret_candidates = secret_candidates

    @staticmethod
    def _config(data: dict[str, Any] | None, requested_model: str | None = None) -> dict[str, str] | None:
        env_openrouter = os.environ.get("OPENROUTER_API_KEY")
        if env_openrouter:
            return {
                "api_key": env_openrouter,
                "model": requested_model or os.environ.get("OPENROUTER_MODEL", "openrouter/auto"),
                "base_url": os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                "api_format": "chat", "provider": "openrouter",
            }
        if not data:
            env_key = os.environ.get("OPENAI_API_KEY")
            if not env_key:
                return None
            return {
                "api_key": env_key,
                "model": requested_model or os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
                "base_url": os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
                "api_format": os.environ.get("OPENAI_API_FORMAT", "responses"), "provider": "openai",
            }
        for provider, default_model, default_url, default_format in (
            ("openrouter", "openrouter/auto", "https://openrouter.ai/api/v1", "chat"),
            ("openai", "gpt-4.1-mini", "https://api.openai.com/v1", "responses"),
        ):
            upper = provider.upper()
            raw = data.get(provider) or data.get(provider.title()) or data.get(f"{upper}_API_KEY")
            if isinstance(raw, str):
                return {
                    "api_key": raw,
                    "model": requested_model or str(data.get(f"{provider}_model") or default_model),
                    "base_url": str(data.get(f"{provider}_base_url") or default_url),
                    "api_format": str(data.get(f"{provider}_api_format") or default_format),
                    "provider": provider,
                }
            if isinstance(raw, dict):
                key = raw.get("api_key") or raw.get("key") or raw.get(f"{upper}_API_KEY")
                if key:
                    return {
                        "api_key": str(key),
                        "model": requested_model or str(raw.get("model") or data.get(f"{provider}_model") or default_model),
                        "base_url": str(raw.get("base_url") or default_url),
                        "api_format": str(raw.get("api_format") or data.get(f"{provider}_api_format") or default_format),
                        "provider": provider,
                        "mock_response": str(raw.get("mock_response") or ""),
                    }
        return None

    def call_text(self, user_content: str, system_content: str, model: str | None = None) -> tuple[str, str]:
        env_mock = os.environ.get("EAI_VNEXT_MOCK_OPENAI_RESPONSE")
        if env_mock:
            return env_mock, model or "mock-model"
        secret_data, _ = read_secret_data(self.secret_candidates)
        config = self._config(secret_data, model)
        if not config:
            raise ProviderError(400, "未配置 OpenAI-compatible 密钥，无法发送 API。")
        if config.get("mock_response"):
            return config["mock_response"], config["model"]
        base_url = config.get("base_url") or "https://api.openai.com/v1"
        if config.get("api_format") == "chat":
            endpoint = f"{base_url.rstrip('/')}/chat/completions"
            payload = {
                "model": config["model"],
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ],
            }
        else:
            endpoint = f"{base_url.rstrip('/')}/responses"
            payload = {
                "model": config["model"],
                "input": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ],
            }
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            raise ProviderError(502, f"OpenAI-compatible API 调用失败: {detail[:500]}") from exc
        except Exception as exc:
            raise ProviderError(502, f"OpenAI-compatible API 调用失败: {exc}") from exc
        text = extract_provider_text(body)
        if not text:
            raise ProviderError(502, "OpenAI-compatible API 返回为空。")
        return text, config["model"]

    def run_task_pack(self, markdown: str, model: str | None = None) -> tuple[str, str]:
        return self.call_text(
            markdown,
            "你是严谨的中文学术研究助手。只基于用户提供的 Task Pack 回答，不编造论文细节。",
            model,
        )
