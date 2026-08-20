"""Android bootstrap for ArXistant.

Called from the Android app (ServerService) via Chaquopy:

    Python.getInstance().getModule("android_bootstrap")
        .callAttr("start_server", data_dir, secret_store)

This sets the required environment variables *before* importing the server so
that arxistant_paths picks up the writable data directory and arxistant_tasks
selects in-process execution (no subprocesses on Android).
"""

import os

import arxistant_secrets


def start_server(data_dir, secret_backend=None):
    # Must be set before arxistant_paths / arxistant_tasks are first imported.
    os.environ["ARXISTANT_DATA_DIR"] = data_dir
    os.environ["ARXISTANT_IN_PROCESS"] = "1"
    # Bind to an explicit IPv4 loopback so the WebView at 127.0.0.1 can reach it
    # ("localhost" can resolve to ::1 on some Android builds).
    os.environ["ARXISTANT_BIND"] = "127.0.0.1"

    if secret_backend is not None:
        arxistant_secrets.set_backend(secret_backend)

    import arxiv_db_server
    arxiv_db_server.run_server(8765)
