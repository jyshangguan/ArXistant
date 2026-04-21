"""Tests for src/config.py — Topic, load_topics, load_settings."""

from __future__ import annotations

import os

import pytest
import yaml

from src.config import Settings, Topic, load_settings, load_topics


# ── Topic.from_dict ──────────────────────────────────────────────────────


class TestTopicFromDict:
    def test_minimal(self):
        t = Topic.from_dict({"name": "Test"})
        assert t.name == "Test"
        assert t.description == ""
        assert t.keywords == []
        assert t.categories == []

    def test_full(self):
        t = Topic.from_dict({
            "name": "Full",
            "description": "A full topic",
            "keywords": ["kw1", "kw2"],
            "categories": ["astro-ph.GA"],
        })
        assert t.name == "Full"
        assert t.description == "A full topic"
        assert t.keywords == ["kw1", "kw2"]
        assert t.categories == ["astro-ph.GA"]

    def test_missing_name_raises(self):
        with pytest.raises(KeyError):
            Topic.from_dict({"description": "no name"})

    def test_extra_keys_ignored(self):
        t = Topic.from_dict({"name": "X", "extra": 42})
        assert t.name == "X"
        assert not hasattr(t, "extra")


# ── load_topics ──────────────────────────────────────────────────────────


class TestLoadTopics:
    def test_basic(self, tmp_path, sample_topic):
        data = {"topics": [
            {"name": sample_topic.name,
             "description": sample_topic.description,
             "keywords": sample_topic.keywords,
             "categories": sample_topic.categories},
        ]}
        f = tmp_path / "topics.yaml"
        f.write_text(yaml.dump(data))

        topics = load_topics(f)
        assert len(topics) == 1
        assert topics[0].name == sample_topic.name

    def test_empty_topics_list(self, tmp_path):
        f = tmp_path / "topics.yaml"
        f.write_text(yaml.dump({"topics": []}))
        assert load_topics(f) == []

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_topics("/nonexistent/path/topics.yaml")


# ── load_settings ────────────────────────────────────────────────────────


class TestLoadSettings:
    def _write_settings(self, tmp_path, data=None):
        if data is None:
            data = {
                "llm": {"provider": "openai_compatible", "model": "test-model",
                        "base_url": "https://example.com/v4", "temperature": 0.2},
                "arxiv": {"max_results": 50, "days_back": 7},
                "filter": {"batch_size": 4, "relevance_threshold": 4},
                "report": {"output_dir": "test_reports"},
            }
        f = tmp_path / "settings.yaml"
        f.write_text(yaml.dump(data))
        return f

    def test_basic(self, tmp_path):
        f = self._write_settings(tmp_path)
        s = load_settings(f)
        assert s.llm_model == "test-model"
        assert s.llm_base_url == "https://example.com/v4"
        assert s.llm_temperature == 0.2
        assert s.max_results == 50
        assert s.days_back == 7
        assert s.batch_size == 4
        assert s.relevance_threshold == 4
        assert s.report_output_dir == "test_reports"

    def test_defaults_when_keys_missing(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GLM_API_KEY", raising=False)
        monkeypatch.delenv("LLM_MODEL", raising=False)
        monkeypatch.delenv("LLM_BASE_URL", raising=False)
        f = self._write_settings(tmp_path, {"llm": {}})
        s = load_settings(f)
        assert s.llm_model == "glm-4-flash"
        assert s.max_results == 100
        assert s.days_back == 3
        assert s.relevance_threshold == 4

    def test_env_override_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GLM_API_KEY", "env-key-999")
        f = self._write_settings(tmp_path)
        s = load_settings(f)
        assert s.llm_api_key == "env-key-999"

    def test_env_override_model(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_MODEL", "env-model")
        f = self._write_settings(tmp_path)
        s = load_settings(f)
        assert s.llm_model == "env-model"

    def test_env_override_base_url(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LLM_BASE_URL", "https://env.example.com/v4")
        f = self._write_settings(tmp_path)
        s = load_settings(f)
        assert s.llm_base_url == "https://env.example.com/v4"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_settings("/nonexistent/path/settings.yaml")
