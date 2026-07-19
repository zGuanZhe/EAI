from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.core.secrets import safe_secret_status
from app.services.anthropic_provider import AnthropicProvider
from app.services.provider import ProviderAdapter


class _FakeStream:
    def __init__(self, chunks: list[str]) -> None:
        self.text_stream = iter(chunks)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeMessages:
    def __init__(self) -> None:
        self.create_calls: list[dict] = []
        self.stream_calls: list[dict] = []
        self.response = SimpleNamespace(content=[SimpleNamespace(type="text", text="Claude answer")])

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.response

    def stream(self, **kwargs):
        self.stream_calls.append(kwargs)
        return _FakeStream(["Claude ", "stream"])


class ModelProviderTest(unittest.TestCase):
    def setUp(self):
        self.messages = _FakeMessages()
        self.client = SimpleNamespace(messages=self.messages)
        self.client_patch = patch(
            "app.services.anthropic_provider.anthropic.Anthropic",
            return_value=self.client,
        )
        self.client_patch.start()

    def tearDown(self):
        self.client_patch.stop()

    def provider(self) -> AnthropicProvider:
        return AnthropicProvider({
            "api_key": "test-key",
            "base_url": "https://api.anthropic.com",
            "model": "claude-opus-4-7",
            "api_format": "anthropic",
            "provider": "anthropic",
        })

    def test_anthropic_text_and_stream_use_sdk_prompt_caching(self):
        provider = self.provider()
        self.assertEqual(provider.call_text("question", "system"), "Claude answer")
        self.assertEqual("".join(provider.stream_text("prompt", "system", lambda: False)), "Claude stream")
        for call in [self.messages.create_calls[0], self.messages.stream_calls[0]]:
            self.assertEqual(call["cache_control"], {"type": "ephemeral"})
            self.assertEqual(call["model"], "claude-opus-4-7")
            self.assertNotIn("api_key", call)

    def test_anthropic_tool_use_maps_to_registered_capability(self):
        self.messages.response = SimpleNamespace(content=[SimpleNamespace(
            type="tool_use", name="knowledge__search", input={"query": "VLA"},
        )])
        result = self.provider().tool_decision("choose", [{
            "name": "knowledge.search",
            "description": "Search local research",
            "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
        }])
        self.assertEqual(result["action"], "call_tools")
        self.assertEqual(result["calls"][0]["capability"], "knowledge.search")
        self.assertEqual(result["calls"][0]["arguments"], {"query": "VLA"})
        self.assertEqual(self.messages.create_calls[0]["cache_control"], {"type": "ephemeral"})

    def test_selected_compatible_provider_keeps_its_audit_identity(self):
        with patch.dict(os.environ, {
            "EAI_MODEL_PROVIDER": "deepseek",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "https://api.deepseek.com/v1",
            "OPENAI_MODEL": "deepseek-chat",
            "OPENAI_API_FORMAT": "chat",
        }, clear=False):
            config = ProviderAdapter._config(None)
        self.assertEqual(config["provider"], "deepseek")
        self.assertEqual(config["api_format"], "chat")

    def test_explicit_environment_provider_overrides_old_secret_file(self):
        old_data = {"openai": {"api_key": "old-key", "model": "old-model"}}
        with patch.dict(os.environ, {
            "EAI_MODEL_PROVIDER": "deepseek",
            "OPENAI_API_KEY": "current-key",
            "OPENAI_BASE_URL": "https://api.deepseek.com/v1",
            "OPENAI_MODEL": "deepseek-reasoner",
            "OPENAI_API_FORMAT": "chat",
        }, clear=False):
            config = ProviderAdapter._config(old_data)
        self.assertEqual(config["api_key"], "current-key")
        self.assertEqual(config["provider"], "deepseek")
        self.assertEqual(config["model"], "deepseek-reasoner")

    def test_secret_status_reports_selected_compatible_provider(self):
        old_data = {"openai": {"api_key": "old-key", "model": "old-model"}}
        with patch.dict(os.environ, {
            "EAI_MODEL_PROVIDER": "deepseek",
            "OPENAI_API_KEY": "current-key",
            "OPENAI_MODEL": "deepseek-chat",
        }, clear=False):
            status = safe_secret_status(old_data, None, True)
        providers = {item["provider"]: item for item in status["providers"]}
        self.assertEqual(providers["deepseek"]["model"], "deepseek-chat")
        self.assertEqual(status["source"], "environment")

    def test_explicit_provider_never_falls_back_to_another_saved_provider(self):
        old_data = {"openai": {"api_key": "old-key", "model": "old-model"}}
        with patch.dict(os.environ, {
            "EAI_MODEL_PROVIDER": "anthropic",
            "ANTHROPIC_API_KEY": "",
            "OPENAI_API_KEY": "old-environment-key",
        }, clear=False):
            config = ProviderAdapter._config(old_data)
        self.assertIsNone(config)


if __name__ == "__main__":
    unittest.main()
