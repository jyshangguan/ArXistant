#!/bin/bash

# Launching through macOS Launch Services can inherit environment variables
# from the application that opened Chrome (for example __PYVENV_LAUNCHER__ and
# DAIMON_*).  Start Python with an allow-listed environment so those variables
# cannot alter Python's prefix or module search path.
PROJECT_ROOT="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PYTHON="${ARXISTANT_PYTHON:-/usr/bin/python3}"
LOG_FILE="$PROJECT_ROOT/local/server.log"
SERVER_SCRIPT="$PROJECT_ROOT/src/arxiv_db_server.py"
SERVER_API_VERSION="4"
SERVER_PORT="${ARXISTANT_PORT:-8765}"

# Platform tooling.  macOS wraps Python in `arch` to force the native slice;
# Linux has no equivalent.
ARCH_CMD=()
if [ "$(uname -s)" = "Darwin" ]; then
    case "$(uname -m)" in
        arm64) PYTHON_ARCH="arm64" ;;
        *)     PYTHON_ARCH="x86_64" ;;
    esac
    ARCH_CMD=(/usr/bin/arch -"$PYTHON_ARCH")
fi

# curl and grep live in /usr/bin on both macOS and Debian/Ubuntu, but fall
# back to PATH lookup so minimal or non-standard installs still work.
CURL=/usr/bin/curl
[ -x "$CURL" ] || CURL="$(command -v curl || true)"
GREP=/usr/bin/grep
[ -x "$GREP" ] || GREP="$(command -v grep || true)"

cd "$PROJECT_ROOT" || exit 1

# The log file lives in the gitignored data directory, which does not exist
# on a fresh clone; create it before the nohup redirect below.
mkdir -p "$PROJECT_ROOT/local" || exit 1

# Return immediately when the running server has the API expected by the
# extension. A successful page request alone is not sufficient because an old
# process can keep serving files after the source code has been upgraded.
if [ -n "$CURL" ] && [ -n "$GREP" ] && \
  "$CURL" --silent --fail --max-time 1 \
  "http://localhost:${SERVER_PORT}/api/health" 2> /dev/null | \
  "$GREP" --quiet "\"api_version\": $SERVER_API_VERSION"; then
  echo "Server is already running"
  exit 0
fi

# Find the PID listening on the server port.  lsof is an optional package on
# Debian/Ubuntu, so fall back to fuser or ss (iproute2, almost universal).
port_listener_pid() {
  if command -v lsof > /dev/null 2>&1; then
    lsof -tiTCP:"$SERVER_PORT" -sTCP:LISTEN 2> /dev/null | head -n 1
  elif command -v fuser > /dev/null 2>&1; then
    fuser "$SERVER_PORT"/tcp 2> /dev/null | tr -s ' ' '\n' | head -n 1
  elif command -v ss > /dev/null 2>&1; then
    ss -ltnpH "( sport = :$SERVER_PORT )" 2> /dev/null | \
      grep -o 'pid=[0-9]*' | head -n 1 | cut -d= -f2
  fi
}

# If the port belongs to this checkout's server, it is an outdated process and
# can be replaced safely. Never terminate an unrelated process using the port.
LISTENER_PID="$(port_listener_pid)"
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
      echo "Port ${SERVER_PORT} is occupied by another application: $LISTENER_COMMAND" >&2
      exit 1
      ;;
  esac
fi

# Build the allow-listed environment.  Beyond the base variables, pass through
# the desktop session variables the server needs on Linux (keyring's Secret
# Service backend reaches gnome-keyring/KWallet over the D-Bus session bus),
# optional proxy/TLS settings for outbound HTTPS, and ArXistant's own
# configuration overrides.
ENV_ARGS=(
  "HOME=${HOME}"
  "USER=${USER:-$(id -un)}"
  "LOGNAME=${LOGNAME:-${USER:-$(id -un)}}"
  "PATH=/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
  "LANG=${LANG:-en_US.UTF-8}"
  "TMPDIR=${TMPDIR:-/tmp}"
)
for var in \
  DBUS_SESSION_BUS_ADDRESS XDG_RUNTIME_DIR XDG_DATA_HOME SSH_AUTH_SOCK \
  http_proxy https_proxy no_proxy HTTP_PROXY HTTPS_PROXY NO_PROXY \
  SSL_CERT_FILE REQUESTS_CA_BUNDLE \
  ARXISTANT_DATA_DIR ARXISTANT_PORT ARXISTANT_BIND ARXISTANT_IN_PROCESS; do
  if [ -n "${!var:-}" ]; then
    ENV_ARGS+=("$var=${!var}")
  fi
done

/usr/bin/nohup /usr/bin/env -i \
  "${ENV_ARGS[@]}" \
  "${ARCH_CMD[@]}" \
  "$PYTHON" "$SERVER_SCRIPT" \
  > "$LOG_FILE" 2>&1 < /dev/null &

NEW_PID=$!
echo "$NEW_PID"

# Verify the server actually came up so a silent failure (missing Python
# dependency, an occupied port that could not be inspected, etc.) is reported
# instead of printing the PID of a process that already died.
for _attempt in 1 2 3 4 5 6 7 8 9 10; do
  if [ -n "$CURL" ] && [ -n "$GREP" ] && \
    "$CURL" --silent --fail --max-time 1 \
    "http://localhost:${SERVER_PORT}/api/health" 2> /dev/null | \
    "$GREP" --quiet "\"api_version\": $SERVER_API_VERSION"; then
    exit 0
  fi
  if ! /bin/kill -0 "$NEW_PID" 2> /dev/null; then
    break
  fi
  /bin/sleep 0.5
done

echo "ArXistant server failed to start; last log lines:" >&2
tail -n 5 "$LOG_FILE" >&2 2> /dev/null || true
exit 1
