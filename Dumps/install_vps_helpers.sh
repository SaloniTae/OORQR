# --- BEGIN: install VPS helpers (run this ON the VPS as root) ---
# set -eux (it prints traces)
set +x # (stop the printing traces)

# Where your python bot files reside (change if needed)
SCRIPTS_DIR="${SCRIPTS_DIR:-/root/OORBOTS}"

# helper file path
HELPER="/usr/local/bin/vps_helpers.sh"

cat > "$HELPER" <<'EOF'
#!/bin/bash
# vps_helpers.sh
# Source this from ~/.bashrc (the installer will do that).
# Provides case-insensitive service start/stop/status and
# dynamic "<script>.py stop" -> pkill -f <script>.py

# Directory where your python scripts live (can be overridden by exporting SCRIPTS_DIR)
SCRIPTS_DIR="${SCRIPTS_DIR:-/root/OORBOTS}"

# Map service name -> script filename (edit if scripts are in different names/paths)
declare -A SERVICE_MAP
CONFIG_FILE="/root/services.conf"

# default scripts dir (will be overridden if config provides SCRIPTS_DIR=)
: "${SCRIPTS_DIR:=/root/OORBOTS}"

if [[ -f "$CONFIG_FILE" ]]; then
  # First scan for SCRIPTS_DIR= line (if present), set it (allow relative or absolute)
  while IFS= read -r line; do
    # skip blank and comment lines
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" =~ ^SCRIPTS_DIR= ]]; then
      # shell-safe parse (remove surrounding quotes if any)
      val="${line#SCRIPTS_DIR=}"
      # trim quotes
      val="${val%\"}"; val="${val#\"}"
      val="${val%\'}"; val="${val#\'}"
      SCRIPTS_DIR="$val"
      break
    fi
  done < "$CONFIG_FILE"

  # Now load service mappings (skip SCRIPTS_DIR line and comments)
  while IFS='=' read -r key value; do
    # strip whitespace
    key="$(echo "$key" | tr -d '[:space:]')"
    value="$(echo "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$key" || "$key" =~ ^# || "$key" == "SCRIPTS_DIR" ]] && continue
    SERVICE_MAP["$key"]="$value"
  done < "$CONFIG_FILE"
else
  echo "⚠️ Config file not found: $CONFIG_FILE"
fi

# show screen sessions when typing "screen" with no args; otherwise call real screen
screen() {
  if [ $# -eq 0 ]; then
    ps aux | grep SCREEN | grep -v grep || true
  else
    command screen "$@"
  fi
}

# helper: start service (name lowercased) -> will use SERVICE_MAP to find script
_vps_start() {
  svc="$1"
  script="${SERVICE_MAP[$svc]}"
  if [ -z "$script" ]; then
    echo "Unknown service: $svc"
    return 2
  fi
  screen -dmS "$svc" python3 "$SCRIPTS_DIR/$script"
  echo "Started $svc -> $SCRIPTS_DIR/$script (detached screen)"
  return 0
}

_vps_stop() {
  svc="$1"
  script="${SERVICE_MAP[$svc]}"
  if [ -z "$script" ]; then
    echo "Unknown service: $svc"
    return 2
  fi
  screen -S "$svc" -X quit 2>/dev/null || true
  echo "Stopped $svc (screen quit) — if still running, check PID and pkill -f."
  return 0
}

_vps_status() {
  svc="$1"
  ps aux | grep SCREEN | grep -i "$svc" | grep -v grep || true
}

# This function is called by bash when a command is not found.
# We'll use it to:
#  1) accept case-insensitive service names like "Prime start"
#  2) catch "<some>.py stop" and pkill dynamically
command_not_found_handle() {
  cmd="$1"; shift || true
  # lowercase command name and action for case-insensitive matching
  lc_cmd="$(echo "$cmd" | tr '[:upper:]' '[:lower:]')"
  action="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')"

  # If cmd matches a known service (case-insensitive)
  if [[ -n "${SERVICE_MAP[$lc_cmd]}" ]]; then
    case "$action" in
      start)
        _vps_start "$lc_cmd"
        return 0
        ;;
      stop)
        _vps_stop "$lc_cmd"
        return 0
        ;;
      status)
        _vps_status "$lc_cmd"
        return 0
        ;;
      *)
        echo "Usage: $cmd start|stop|status"
        return 0
        ;;
    esac
  fi

  # If user typed "<script>.py stop", dynamically pkill that script (works for any name)
  if [[ "$cmd" == *.py ]] && [[ "$action" == "stop" ]]; then
    # use the original case-sensitive name as the pattern (safer)
    pattern="$cmd"
    echo "pkill -f $pattern"
    pkill -f "$pattern" 2>/dev/null || true
    echo "Requested stop for $pattern"
    return 0
  fi

  # fallback to default behaviour (command not found)
  printf "%s: command not found\n" "$cmd" >&2
  return 127
}
EOF

# make it executable
chmod 755 "$HELPER"

# ensure it's sourced from root's ~/.bashrc (only add once)
BASHRC="$HOME/.bashrc"
if ! grep -q "source $HELPER" "$BASHRC" 2>/dev/null; then
  echo "" >> "$BASHRC"
  echo "# Source vps quick helpers" >> "$BASHRC"
  echo "if [ -f $HELPER ]; then" >> "$BASHRC"
  echo "  source $HELPER" >> "$BASHRC"
  echo "fi" >> "$BASHRC"
fi

# apply to current shell immediately
source "$HELPER"

echo "vps helper installed and sourced. You can now use: prime start|stop|status, netflix start|stop|status, etc."
echo "And any <script>.py stop will run pkill -f <script>.py dynamically."
# --- END ---
