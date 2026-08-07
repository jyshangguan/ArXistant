import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import arxiv_daily_ranker_html
import arxiv_db_server


class _Response(io.BytesIO):
    class _Headers:
        @staticmethod
        def get_content_charset():
            return "utf-8"

    headers = _Headers()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class PortabilityTests(unittest.TestCase):
    def test_data_directory_can_live_outside_installation(self):
        with tempfile.TemporaryDirectory() as data_dir:
            env = os.environ.copy()
            env["ARXISTANT_DATA_DIR"] = data_dir
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from arxistant_paths import DATA_DIR; print(DATA_DIR)",
                ],
                cwd=PROJECT_ROOT / "src",
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.stdout.strip(), data_dir)

    def test_python_command_reuses_current_interpreter(self):
        command = arxiv_db_server.python_command("worker.py", "--flag", "value")

        self.assertEqual(command[0], sys.executable)
        self.assertEqual(Path(command[1]), PROJECT_ROOT / "src" / "worker.py")
        self.assertEqual(command[2:], ["--flag", "value"])

    def test_child_environment_preserves_platform_path_and_removes_launcher_state(self):
        source = {
            "PATH": "platform-specific-path",
            "APP_SETTING": "kept",
            "PYTHONHOME": "wrong-runtime",
            "__PYVENV_LAUNCHER__": "wrong-launcher",
        }
        with mock.patch.dict(os.environ, source, clear=True):
            result = arxiv_db_server.child_python_env()

        self.assertEqual(result["PATH"], source["PATH"])
        self.assertEqual(result["APP_SETTING"], "kept")
        self.assertNotIn("PYTHONHOME", result)
        self.assertNotIn("__PYVENV_LAUNCHER__", result)

    @mock.patch.object(arxiv_daily_ranker_html.urllib.request, "urlopen")
    def test_fetch_url_uses_python_https_client(self, urlopen):
        urlopen.return_value = _Response(b"portable response")

        result = arxiv_daily_ranker_html._fetch_url(
            "https://example.test/papers", timeout=7, attempts=1
        )

        self.assertEqual(result, "portable response")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/papers")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 7)
        self.assertIn("ArXistant", request.get_header("User-agent"))


if __name__ == "__main__":
    unittest.main()
