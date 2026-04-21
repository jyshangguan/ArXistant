"""Tests for src/llm_client.py — create_client, chat_completion."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.config import Settings
from src.llm_client import OpenAI, chat_completion, create_client


# ── create_client ────────────────────────────────────────────────────────


class TestCreateClient:
    @patch("src.llm_client.OpenAI")
    def test_returns_openai_instance(self, mock_openai_cls, sample_settings):
        mock_openai_cls.return_value = MagicMock(spec=OpenAI)
        client = create_client(sample_settings)
        assert isinstance(client, MagicMock)

    @patch("src.llm_client.OpenAI")
    def test_passes_correct_args(self, mock_openai_cls, sample_settings):
        create_client(sample_settings)
        mock_openai_cls.assert_called_once_with(
            api_key="test-key-123",
            base_url="https://open.bigmodel.cn/api/paas/v4",
        )


# ── chat_completion ──────────────────────────────────────────────────────


class TestChatCompletion:
    @patch("src.llm_client.OpenAI")
    def test_returns_content(self, mock_openai_cls):
        mock_client = MagicMock(spec=OpenAI)
        mock_openai_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Hello, world!"
        mock_client.chat.completions.create.return_value = mock_response

        settings = Settings(
            llm_api_key="key",
            llm_base_url="https://example.com/v4",
        )
        client = create_client(settings)
        result = chat_completion(
            client=client,
            model="test-model",
            system_prompt="You are helpful.",
            user_prompt="Hi",
        )
        assert result == "Hello, world!"

    @patch("src.llm_client.OpenAI")
    def test_handles_none_content(self, mock_openai_cls):
        mock_client = MagicMock(spec=OpenAI)
        mock_openai_cls.return_value = mock_client

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = None
        mock_client.chat.completions.create.return_value = mock_response

        settings = Settings(
            llm_api_key="key",
            llm_base_url="https://example.com/v4",
        )
        client = create_client(settings)
        result = chat_completion(
            client=client,
            model="test-model",
            system_prompt="System",
            user_prompt="User",
        )
        assert result == ""
