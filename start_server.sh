#!/bin/bash

# Launching through macOS Launch Services can inherit environment variables
# from the application that opened Chrome (for example __PYVENV_LAUNCHER__ and
# DAIMON_*).  Start Python with an allow-listed environment so those variables
# cannot alter Python's prefix or module search path.
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="/usr/bin/python3"
PYTHON_ARCH="arm64"
LOG_FILE="$PROJECT_ROOT/local/server.log"
SERVER_SCRIPT="$PROJECT_ROOT/src/arxiv_db_server.py"
SERVER_API_VERSION="1"

cd "$PROJECT_ROOT" || exit 1

# Return immediately when the running server has the API expected by the
# extension. A successful page request alone is not sufficient because an old
# process can keep serving files after the source code has been upgraded.
if /usr/bin/curl --silent --fail --max-time 1 \
  "http://localhost:8765/api/health" 2> /dev/null | \
  /usr/bin/grep --quiet "\"api_version\": $SERVER_API_VERSION"; then
  echo "Server is already running"
  exit 0
fi

# If port 8765 belongs to this checkout's server, it is an outdated process and
# can be replaced safely. Never terminate an unrelated process using the port.
LISTENER_PID=$(/usr/sbin/lsof -tiTCP:8765 -sTCP:LISTEN 2> /dev/null | /usr/bin/head -n 1)
if [ -n "$LISTENER_PID" ]; then
  LISTENER_COMMAND=$(/bin/ps -p "$LISTENER_PID" -o command= 2> /dev/null || true)
  case "$LISTENER_COMMAND" in
    *"$SERVER_SCRIPT"*)
      /bin/kill "$LISTENER_PID"
      for _attempt in 1 2 3 4 5; do
        if ! /bin/kill -0 "$LISTENER_PID" 2> /dev/null; then
          break
        fi
        /bin/sleep 1
      done
      ;;
    *)
      echo "Port 8765 is occupied by another application: $LISTENER_COMMAND" >&2
      exit 1
      ;;
  esac
fi

/usr/bin/nohup /usr/bin/env -i \
  HOME="${HOME}" \
  USER="${USER:-$(id -un)}" \
  LOGNAME="${LOGNAME:-${USER:-$(id -un)}}" \
  PATH="/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin" \
  LANG="${LANG:-en_US.UTF-8}" \
  TMPDIR="${TMPDIR:-/tmp}" \
  /usr/bin/arch -"$PYTHON_ARCH" \
  "$PYTHON" "$SERVER_SCRIPT" \
  > "$LOG_FILE" 2>&1 < /dev/null &

echo $!
