"""Shared application and writable-data paths."""

import os


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.abspath(os.path.expanduser(
    os.environ.get("ARXISTANT_DATA_DIR", os.path.join(PROJECT_ROOT, "local"))
))


def data_path(*parts):
    """Return a path inside ArXistant's writable data directory."""
    return os.path.join(DATA_DIR, *parts)


def ensure_data_dirs():
    """Create the writable directories required by the application."""
    os.makedirs(data_path("ml_ranker"), exist_ok=True)

