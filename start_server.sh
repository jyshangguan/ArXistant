#!/bin/bash

# Launching through macOS Launch Services can inherit environment variables
# from the application that opened Chrome (for example __PYVENV_LAUNCHER__ and
# DAIMON_*).  Start Python with an allow-listed environment so those variables
# cannot alter Python's prefix or module search path.
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="/usr/bin/python3"
PYTHON_ARCH="arm64"
LOG_FILE="$PROJECT_ROOT/local/server.log"

cd "$PROJECT_ROOT" || exit 1

# Avoid replacing the useful log with an "address already in use" traceback if
# the button is clicked twice or Chrome's status check races with startup.
if /usr/bin/curl --silent --fail --max-time 1 \
  "http://localhost:8765/daily.html" > /dev/null 2>&1; then
  echo "Server is already running"
  exit 0
fi

/usr/bin/nohup /usr/bin/env -i \
  HOME="${HOME}" \
  USER="${USER:-$(id -un)}" \
  LOGNAME="${LOGNAME:-${USER:-$(id -un)}}" \
  PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
  LANG="${LANG:-en_US.UTF-8}" \
  TMPDIR="${TMPDIR:-/tmp}" \
  /usr/bin/arch -"$PYTHON_ARCH" \
  "$PYTHON" "$PROJECT_ROOT/src/arxiv_db_server.py" \
  > "$LOG_FILE" 2>&1 < /dev/null &

echo $!
