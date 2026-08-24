"""Task runners for ArXistant's ML and page-refresh background work.

The HTTP server originally ran ML training, feature-page generation, and the
daily/recent page refresh as subprocesses, reusing the same interpreter and
isolating the NumPy/scikit-learn imports. That is still the default on desktop.

On Android (via Chaquopy) subprocess spawning of Python is unavailable, so
setting ``ARXISTANT_IN_PROCESS=1`` makes these tasks run in-process instead,
importing the modules and calling their functions directly. The HTTP server
must be launched with that environment variable set in the Android app.
"""

import os
import subprocess
import sys

from arxistant_paths import PROJECT_ROOT

IN_PROCESS = os.environ.get("ARXISTANT_IN_PROCESS") == "1"


def child_python_env():
    """Return a portable environment for Python child processes."""
    env = os.environ.copy()
    # These variables can be inherited through macOS Launch Services and make
    # a child interpreter use the wrong virtual environment or architecture.
    for name in ("__PYVENV_LAUNCHER__", "PYTHONHOME"):
        env.pop(name, None)
    return env


_APPLE_SILICON = None


def is_apple_silicon():
    """Detect Apple Silicon hardware (true even when running under Rosetta)."""
    global _APPLE_SILICON
    if _APPLE_SILICON is None:
        if sys.platform != "darwin":
            _APPLE_SILICON = False
        else:
            try:
                result = subprocess.run(
                    ["/usr/sbin/sysctl", "-n", "hw.optional.arm64"],
                    capture_output=True, text=True, timeout=5,
                )
                _APPLE_SILICON = (result.returncode == 0 and result.stdout.strip() == "1")
            except Exception:
                _APPLE_SILICON = False
    return _APPLE_SILICON


def python_command(script, *args):
    """Run project scripts with the same interpreter as this server."""
    command = [sys.executable, os.path.join(PROJECT_ROOT, "src", script), *args]
    # On Apple Silicon, force the native arm64 architecture so child processes
    # match the arm64 NumPy/scikit-learn wheels. A server launched under Rosetta
    # would otherwise spawn x86_64 children that cannot import the arm64 C
    # extensions (this affects training, feature-page generation, and the daily
    # refresh alike).
    if is_apple_silicon():
        command = ["/usr/bin/arch", "-arm64", *command]
    return command


def _run_script(script, *args, timeout):
    """Run a project script as a subprocess. Returns a CompletedProcess."""
    return subprocess.run(
        python_command(script, *args),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=child_python_env(),
    )


def _error_from(result, fallback):
    return result.stderr.strip() or result.stdout.strip() or fallback


def train_and_generate_features():
    """Run 'train' then 'features'. Returns (trained_ok, error)."""
    if IN_PROCESS:
        import arxiv_ml_ranker
        try:
            trained = arxiv_ml_ranker.train_model()
        except Exception as exc:
            return False, str(exc)
        if not trained:
            return False, "Training failed (no positive training data?)"
        try:
            features_ok = arxiv_ml_ranker.generate_features_html()
        except Exception as exc:
            return True, str(exc)
        if not features_ok:
            return True, "Training succeeded, but feature-page generation failed"
        return True, None

    try:
        result = _run_script("arxiv_ml_ranker.py", "train", timeout=900)
    except subprocess.TimeoutExpired:
        return False, "Model training timed out"
    except Exception as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, _error_from(result, "Training failed")

    try:
        features = _run_script("arxiv_ml_ranker.py", "features", timeout=120)
    except subprocess.TimeoutExpired:
        return True, "Training succeeded, but feature-page generation timed out"
    except Exception as exc:
        return True, "Training succeeded, but feature-page generation failed: " + str(exc)
    if features.returncode != 0:
        return True, _error_from(features, "Training succeeded, but feature-page generation failed")
    return True, None


def regenerate_features():
    """Run 'features'. Returns (ok, error)."""
    if IN_PROCESS:
        import arxiv_ml_ranker
        try:
            ok = arxiv_ml_ranker.generate_features_html()
        except Exception as exc:
            return False, str(exc)
        if not ok:
            return False, "Feature generation failed (train the model first)"
        return True, None

    try:
        result = _run_script("arxiv_ml_ranker.py", "features", timeout=120)
    except subprocess.TimeoutExpired:
        return False, "Feature generation timed out"
    except Exception as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, _error_from(result, "Feature generation failed")
    return True, None


def refresh_daily(output_path):
    return _refresh(output_path, recent=False)


def refresh_recent(output_path):
    return _refresh(output_path, recent=True)


def _refresh(output_path, recent):
    """Fetch, rank, and write a page. Returns (ok, error).

    A JSON snapshot of the ranked list is written next to the HTML page
    (same name with a ``.json`` suffix); the Chat page uses it to offer
    papers from the daily/recent lists.
    """
    json_path = os.path.splitext(output_path)[0] + ".json"
    if IN_PROCESS:
        import arxiv_daily_ranker_html as ranker
        try:
            ranker.generate_ranked_html(
                recent=recent, output_path=output_path, json_output=json_path)
            return True, None
        except Exception as exc:
            return False, str(exc)

    args = ["--output", output_path, "--json-output", json_path]
    if recent:
        args.insert(0, "--recent")
    try:
        result = _run_script("arxiv_daily_ranker_html.py", *args, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "Page generation timed out"
    except Exception as exc:
        return False, str(exc)
    if result.returncode != 0:
        return False, _error_from(result, "Page generation failed")
    return True, None
