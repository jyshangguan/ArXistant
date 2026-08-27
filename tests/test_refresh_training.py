"""Regression tests for training the ML model before a daily/recent refresh.

A refresh must train the model first when it is missing (fresh install) or a
retrain is due, and skip training entirely when there are no saved papers to
learn from.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import arxiv_db_server as server


class RefreshTrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patchers = [
            mock.patch.object(server, "RETRAIN_STATE_PATH",
                              os.path.join(self.tmp.name, "retrain_state.json")),
            mock.patch.object(server, "MODEL_FILE_PATH",
                              os.path.join(self.tmp.name, "model.pkl")),
            mock.patch.object(server, "VECTORIZER_FILE_PATH",
                              os.path.join(self.tmp.name, "vectorizer.pkl")),
        ]
        for patcher in self.patchers:
            patcher.start()
            self.addCleanup(patcher.stop)
        # Fresh in-memory retrain state for every test.
        server.RETRAIN_STATE.clear()
        server.RETRAIN_STATE.update(server._default_retrain_state())

    def test_skips_training_without_saved_papers(self):
        with mock.patch.object(server, "_saved_paper_count", return_value=0), \
                mock.patch.object(server.arxistant_tasks,
                                  "train_and_generate_features") as train:
            note = server.ensure_model_for_refresh()
        train.assert_not_called()
        self.assertIn("skipping", note)

    def test_trains_when_model_missing(self):
        with mock.patch.object(server, "_saved_paper_count", return_value=3), \
                mock.patch.object(server.arxistant_tasks,
                                  "train_and_generate_features",
                                  return_value=(True, None)) as train:
            note = server.ensure_model_for_refresh()
        train.assert_called_once()
        self.assertIn("trained", note)
        self.assertIsNotNone(server.RETRAIN_STATE["last_trained_at"])

    def test_no_training_when_model_is_fresh(self):
        Path(self.tmp.name, "model.pkl").write_text("x")
        Path(self.tmp.name, "vectorizer.pkl").write_text("x")
        with mock.patch.object(server, "_saved_paper_count", return_value=3), \
                mock.patch.object(server.arxistant_tasks,
                                  "train_and_generate_features") as train:
            note = server.ensure_model_for_refresh()
        train.assert_not_called()
        self.assertIn("up to date", note)

    def test_retrains_when_threshold_reached(self):
        Path(self.tmp.name, "model.pkl").write_text("x")
        Path(self.tmp.name, "vectorizer.pkl").write_text("x")
        server.RETRAIN_STATE["changes_since_training"] = \
            server.RETRAIN_STATE["retrain_after_changes"]
        with mock.patch.object(server, "_saved_paper_count", return_value=3), \
                mock.patch.object(server.arxistant_tasks,
                                  "train_and_generate_features",
                                  return_value=(True, None)) as train:
            server.ensure_model_for_refresh()
        train.assert_called_once()
        self.assertEqual(server.RETRAIN_STATE["changes_since_training"], 0)

    def test_failed_training_does_not_block_refresh(self):
        with mock.patch.object(server, "_saved_paper_count", return_value=3), \
                mock.patch.object(server.arxistant_tasks,
                                  "train_and_generate_features",
                                  return_value=(False, "boom")) as train:
            note = server.ensure_model_for_refresh()
        train.assert_called_once()
        self.assertIn("failed", note)
        self.assertFalse(server.RETRAIN_STATE["training"])


if __name__ == "__main__":
    unittest.main()
