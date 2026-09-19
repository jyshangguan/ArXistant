"""Tests for the voice-reading (Listen) feature.

Covers the TTS config store, the spoken-fallback text builder (LaTeX
cleanup), the LLM summary parsing, the summary cache, and batch slicing of
the ranked daily/recent lists. The settings page relay and the injected
page script are exercised manually in the browser.
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import arxiv_db_server as srv


def _paper(pid, title, authors, abstract):
    return {"arxiv_id": pid, "title": title, "authors": authors,
            "abstract": abstract, "source": "daily", "score": 10}


SAMPLE_PAPERS = [
    _paper("2609.16122", "A Dark-matter Origin of Little Red Dots",
           "Hua-Peng Gu, Volker Springel",
           "We study $0.3\\,M_J$ halos with \\texttt{AREPO} and infer "
           "the {mass} of seeds."),
    _paper("2609.16123", "Giant Planet Atmosphere Models",
           "Yi-Xian Chen",
           "Updated tables for $T_{\\mathrm{eff}}$ from 100 to 1400 K."),
]


class VoiceReadingTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = self._tmp.name
        # Point every data-file constant at the temp dir so tests never
        # touch the real local/ data directory.
        self._patches = []
        for name in ("TTS_CONFIG_PATH", "TTS_SUMMARY_CACHE_PATH",
                     "DAILY_JSON", "RECENT_JSON", "CHAT_CONFIG_PATH"):
            attr = getattr(srv, name)
            patcher = mock.patch.object(
                srv, name, os.path.join(tmp, os.path.basename(attr)))
            self._patches.append(patcher)
            patcher.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def _write_ranked(self, papers, recent=False):
        path = srv.RECENT_JSON if recent else srv.DAILY_JSON
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"score": p["score"], "id": p["arxiv_id"],
                        "title": p["title"], "authors": p["authors"],
                        "abstract": p["abstract"]} for p in papers], f)

    # ── config ──

    def test_config_roundtrip_and_clamping(self):
        srv.save_tts_config({"voice": "male", "papers_per_read": 12,
                             "rate": 1.25})
        cfg = srv.load_tts_config()
        self.assertEqual(cfg["voice"], "male")
        self.assertEqual(cfg["papers_per_read"], 12)
        self.assertEqual(cfg["rate"], 1.25)

        # Out-of-range values are clamped, not rejected.
        srv.save_tts_config({"papers_per_read": 999, "rate": 9.9})
        cfg = srv.load_tts_config()
        self.assertEqual(cfg["papers_per_read"], 50)
        self.assertEqual(cfg["rate"], 2.0)

        # Nonsense values fall back to defaults.
        srv.save_tts_config({"papers_per_read": "many", "rate": "fast"})
        cfg = srv.load_tts_config()
        self.assertEqual(cfg["papers_per_read"], 5)
        self.assertEqual(cfg["rate"], 1.0)

    def test_config_voice_roles(self):
        # The voice is stored as a role; roles pass through untouched.
        for role in ("", "male", "female"):
            cfg = srv.save_tts_config({"voice": role})
            self.assertEqual(cfg["voice"], role)

    def test_voice_role_migrates_legacy_names(self):
        # Concrete voice names saved before the role redesign are mapped by
        # well-known hints instead of being dropped.
        cases = {
            "Samantha": "female",
            "Google UK English Female": "female",
            "Moira": "female",
            "Microsoft Zira - English (United States)": "female",
            "Alex": "male",
            "Google UK English Male": "male",
            "Daniel": "male",
            "Microsoft David - English (United States)": "male",
            "Some Unknown Voice": "",
            "": "",
        }
        for name, role in cases.items():
            self.assertEqual(srv._voice_role(name), role, name)

    def test_config_missing_file_uses_defaults(self):
        cfg = srv.load_tts_config()
        self.assertEqual(cfg, srv.DEFAULT_TTS_CONFIG)

    # ── spoken fallback text ──

    def test_plain_speech_text_strips_latex(self):
        text = srv._plain_speech_text(
            "$0.3\\,M_J$ halos run with \\texttt{AREPO} at $T_{\\mathrm{eff}}$=100 K")
        for bad in ("$", "\\", "{", "}", "texttt"):
            self.assertNotIn(bad, text)
        self.assertIn("AREPO", text)  # \texttt{AREPO} content is kept
        self.assertNotIn("  ", text)

    def test_fallback_paper_text_composition(self):
        text = srv._fallback_paper_text(SAMPLE_PAPERS[0])
        self.assertTrue(text.startswith("A Dark-matter Origin"))
        self.assertIn("Hua-Peng Gu", text)
        self.assertIn("halos with", text)
        # LaTeX noise is cleaned: no dollar, backslash, or braces survive.
        for bad in ("$", "\\", "{", "}"):
            self.assertNotIn(bad, text)

    def test_fallback_single_author_no_colleagues(self):
        text = srv._fallback_paper_text(SAMPLE_PAPERS[1])
        self.assertIn("By Yi-Xian Chen.", text)
        self.assertNotIn("colleagues", text)

    # ── LLM summary parsing ──

    def test_parse_tts_summaries_json_array(self):
        texts, ok = srv._parse_tts_summaries('["one.", "two."]', 2)
        self.assertTrue(ok)
        self.assertEqual(texts, ["one.", "two."])

    def test_parse_tts_summaries_prose_wrapped(self):
        content = 'Here you go:\n["first", "second"]\nAnything else?'
        texts, ok = srv._parse_tts_summaries(content, 2)
        self.assertTrue(ok)
        self.assertEqual(texts, ["first", "second"])

    def test_parse_tts_summaries_numbered_plain_text(self):
        content = "Paper 1: alpha.\n\nPaper 2: beta."
        texts, ok = srv._parse_tts_summaries(content, 2)
        self.assertTrue(ok)
        self.assertEqual(texts, ["alpha.", "beta."])

    def test_parse_tts_summaries_unusable(self):
        texts, ok = srv._parse_tts_summaries("", 2)
        self.assertFalse(ok)
        self.assertIsNone(texts)

    # ── summarize_papers_for_tts ──

    def test_summarize_no_llm_uses_fallback(self):
        with mock.patch.object(srv, "load_chat_config",
                               return_value={"base_url": "", "model": ""}), \
             mock.patch.object(srv, "get_chat_api_key", return_value=""):
            result = srv.summarize_papers_for_tts(SAMPLE_PAPERS)
        self.assertFalse(result["llm_used"])
        self.assertEqual(len(result["texts"]), 2)
        self.assertTrue(result["texts"][0].startswith("A Dark-matter Origin"))

    def test_summarize_llm_success(self):
        reply = {"choices": [{"message": {"content":
            '["Spoken digest one.", "Spoken digest two."]'}}]}
        with mock.patch.object(srv, "load_chat_config",
                               return_value={"base_url": "https://x", "model": "m"}), \
             mock.patch.object(srv, "get_chat_api_key", return_value="k"), \
             mock.patch.object(srv, "_chat_completion", return_value=reply) as call:
            result = srv.summarize_papers_for_tts(SAMPLE_PAPERS)
        self.assertTrue(result["llm_used"])
        self.assertEqual(result["model"], "m")
        self.assertEqual(result["texts"], ["Spoken digest one.", "Spoken digest two."])
        # Title, first author, and cleaned abstract all reach the prompt.
        prompt = call.call_args[0][2][1]["content"]
        self.assertIn("A Dark-matter Origin", prompt)
        self.assertIn("First author: Hua-Peng Gu", prompt)
        self.assertIn("halos with", prompt)
        self.assertNotIn("\\texttt", prompt)

    def test_summarize_llm_failure_falls_back(self):
        with mock.patch.object(srv, "load_chat_config",
                               return_value={"base_url": "https://x", "model": "m"}), \
             mock.patch.object(srv, "get_chat_api_key", return_value="k"), \
             mock.patch.object(srv, "_chat_completion",
                               side_effect=Exception("boom")):
            result = srv.summarize_papers_for_tts(SAMPLE_PAPERS)
        self.assertFalse(result["llm_used"])
        self.assertEqual(result["texts"], [srv._fallback_paper_text(p)
                                           for p in SAMPLE_PAPERS])
        self.assertIn("boom", str(result["error"]))

    def test_summarize_short_array_padded_with_fallback(self):
        reply = {"choices": [{"message": {"content": '["only one."]'}}]}
        with mock.patch.object(srv, "load_chat_config",
                               return_value={"base_url": "https://x", "model": "m"}), \
             mock.patch.object(srv, "get_chat_api_key", return_value="k"), \
             mock.patch.object(srv, "_chat_completion", return_value=reply):
            result = srv.summarize_papers_for_tts(SAMPLE_PAPERS)
        self.assertTrue(result["llm_used"])
        self.assertEqual(result["texts"][0], "only one.")
        self.assertEqual(result["texts"][1], srv._fallback_paper_text(SAMPLE_PAPERS[1]))

    # ── build_tts_batch: slicing, cache, errors ──

    def test_build_tts_batch_slicing(self):
        papers = SAMPLE_PAPERS + [
            _paper("3", "Third", "A", "abs"), _paper("4", "Fourth", "B", "abs"),
            _paper("5", "Fifth", "C", "abs")]
        self._write_ranked(papers)
        with mock.patch.object(srv, "load_chat_config",
                               return_value={"base_url": "", "model": ""}), \
             mock.patch.object(srv, "get_chat_api_key", return_value=""):
            batch = srv.build_tts_batch("daily", 1, 2)
        self.assertEqual(batch["total"], 5)
        self.assertEqual(batch["count"], 2)
        self.assertEqual(batch["start"], 1)
        self.assertEqual(batch["remaining"], 2)
        self.assertEqual([p["id"] for p in batch["papers"]],
                         ["2609.16123", "3"])
        self.assertEqual([p["first_author"] for p in batch["papers"]],
                         ["Yi-Xian Chen", "A"])
        # author_count drives "and colleagues" in the spoken lead-in.
        self.assertEqual([p["author_count"] for p in batch["papers"]], [1, 1])

    def test_build_tts_batch_cleans_title_and_counts_authors(self):
        papers = [_paper("2609.00001", "A $M_{\\odot}$ Study of Halos",
                         "Ann Lee, Bo Chen", "abstract")]
        self._write_ranked(papers)
        with mock.patch.object(srv, "load_chat_config",
                               return_value={"base_url": "", "model": ""}), \
             mock.patch.object(srv, "get_chat_api_key", return_value=""):
            batch = srv.build_tts_batch("daily", 0, 1)
        p = batch["papers"][0]
        # The title is cleaned so the spoken announcement has no LaTeX.
        for bad in ("$", "\\", "{", "}"):
            self.assertNotIn(bad, p["title"])
        self.assertIn("Halos", p["title"])
        self.assertEqual(p["author_count"], 2)
        self.assertEqual(p["first_author"], "Ann Lee")

    def test_build_tts_batch_unknown_list(self):
        with self.assertRaises(ValueError):
            srv.build_tts_batch("bogus", 0, 5)

    def test_build_tts_batch_missing_snapshot(self):
        with self.assertRaises(FileNotFoundError):
            srv.build_tts_batch("daily", 0, 5)

    def test_build_tts_batch_cache_hit(self):
        self._write_ranked(SAMPLE_PAPERS)
        # Pre-populate the cache for both papers under model "m".
        cache = {
            "daily:m:2609.16122": {"text": "cached one", "updated_at": 1},
            "daily:m:2609.16123": {"text": "cached two", "updated_at": 1},
        }
        srv._save_tts_cache(cache)
        with mock.patch.object(srv, "load_chat_config",
                               return_value={"base_url": "", "model": "m"}), \
             mock.patch.object(srv, "get_chat_api_key", return_value="k"), \
             mock.patch.object(srv, "summarize_papers_for_tts") as summarize:
            batch = srv.build_tts_batch("daily", 0, 2)
        # Everything came from the cache: no LLM call at all.
        summarize.assert_not_called()
        self.assertEqual([p["text"] for p in batch["papers"]],
                         ["cached one", "cached two"])
        self.assertTrue(all(p["cached"] for p in batch["papers"]))
        self.assertFalse(batch["llm_used"])

    def test_build_tts_batch_writes_cache(self):
        self._write_ranked(SAMPLE_PAPERS)
        fake_summary = {"texts": ["digest one", "digest two"],
                        "llm_used": True, "model": "m", "error": None}
        with mock.patch.object(srv, "load_chat_config",
                               return_value={"base_url": "https://x", "model": "m"}), \
             mock.patch.object(srv, "get_chat_api_key", return_value="k"), \
             mock.patch.object(srv, "summarize_papers_for_tts",
                               return_value=fake_summary):
            batch = srv.build_tts_batch("daily", 0, 2)
        self.assertEqual([p["text"] for p in batch["papers"]],
                         ["digest one", "digest two"])
        self.assertTrue(batch["llm_used"])
        stored = srv._load_tts_cache()
        self.assertEqual(stored["daily:m:2609.16122"]["text"], "digest one")
        self.assertEqual(stored["daily:m:2609.16123"]["text"], "digest two")

    def test_prune_tts_cache_drops_stale_papers(self):
        self._write_ranked(SAMPLE_PAPERS)
        cache = {
            "daily:m:2609.16122": {"text": "keep", "updated_at": 1},
            "daily:m:gone-paper": {"text": "drop", "updated_at": 2},
            "recent:m:2609.16123": {"text": "keep2", "updated_at": 3},
        }
        pruned = srv._prune_tts_cache(cache, [srv.DAILY_JSON, srv.RECENT_JSON])
        self.assertIn("daily:m:2609.16122", pruned)
        self.assertIn("recent:m:2609.16123", pruned)
        self.assertNotIn("daily:m:gone-paper", pruned)

    # ── injection ──

    def test_listen_script_has_marker_and_no_marker_collision(self):
        script = srv.LISTEN_BUTTON_SCRIPT
        self.assertTrue(script.startswith("<!-- listen-button-embedded -->"))
        # The injection check in do_GET relies on this exact marker.
        self.assertIn("listen-button-embedded", script)
        # The pill and panel are both present.
        self.assertIn("arx-listen-btn", script)
        self.assertIn("arx-listen-panel", script)


if __name__ == "__main__":
    unittest.main()
