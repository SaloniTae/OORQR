#!/usr/bin/env bash
# onetime.sh — all-in-one OORBOTS setup for user "iyushh"
# - No package installs
# - Everything under /home/iyushh
# - No changes to /root or /usr/local/bin
# Run as root:  sudo bash onetime.sh

set -euo pipefail

USER_NAME="iyushh"
HOME_DIR="/home/${USER_NAME}"
MOUNT_DIR="${HOME_DIR}/OORBOTS"
CRYPT_DIR="${HOME_DIR}/OORBOTS_encrypted"
SERVICES_CONF="${HOME_DIR}/services.conf"
USER_CFG_DIR="${HOME_DIR}/.config/bash"
USER_HELPER="${USER_CFG_DIR}/vps_helpers.sh"

say()  { printf "\033[1;36m%s\033[0m\n" "$*"; }
ok()   { printf "\033[1;32m%s\033[0m\n" "$*"; }
warn() { printf "\033[1;33m%s\033[0m\n" "$*"; }
err()  { printf "\033[1;31m%s\033[0m\n" "$*"; }

need_root(){ [ "$(id -u)" -eq 0 ] || { err "Run as root"; exit 1; }; }
as_user(){ sudo -i -u "${USER_NAME}" bash -lc "$*"; }
is_mounted(){ as_user "mountpoint -q '${MOUNT_DIR}'"; }
has_meta(){ as_user "[ -f '${CRYPT_DIR}/gocryptfs.conf' ] && [ -f '${CRYPT_DIR}/gocryptfs.diriv' ]"; }

ensure_user() {
  if ! id -u "${USER_NAME}" >/dev/null 2>&1; then
    say "Creating user ${USER_NAME}…"
    adduser "${USER_NAME}"
  else
    ok "User ${USER_NAME} exists."
  fi
  usermod -aG fuse "${USER_NAME}" 2>/dev/null || true
}

ensure_dirs() {
  as_user "mkdir -p '${CRYPT_DIR}' '${MOUNT_DIR}'"
  as_user "chmod 700 '${CRYPT_DIR}' '${MOUNT_DIR}'"
}

# ---- SAFE user-heredoc runners (prevents outer $ expansion) ----
run_user_init() {
  sudo -i -u "${USER_NAME}" CRYPT_DIR="${CRYPT_DIR}" bash -s <<'EOS'
set -e
PF="$(mktemp "$HOME/.gcfpw.init.XXXXXX")"
# read from tty so it's always interactive
read -p "Set gocryptfs password (VISIBLE): " P </dev/tty
printf '%s' "$P" > "$PF"; chmod 600 "$PF"; unset P
gocryptfs -init -passfile "$PF" "$CRYPT_DIR"
(shred -u "$PF" 2>/dev/null || rm -f "$PF")
EOS
}
run_user_mount() {
  sudo -i -u "${USER_NAME}" CRYPT_DIR="${CRYPT_DIR}" MOUNT_DIR="${MOUNT_DIR}" bash -s <<'EOS'
set -e
PF="$(mktemp "$HOME/.gcfpw.mount.XXXXXX")"
read -p "Enter gocryptfs password (VISIBLE): " P </dev/tty
printf '%s' "$P" > "$PF"; chmod 600 "$PF"; unset P
gocryptfs -passfile "$PF" "$CRYPT_DIR" "$MOUNT_DIR"
(shred -u "$PF" 2>/dev/null || rm -f "$PF")
EOS
}
run_user_lock_lazy() {
  sudo -i -u "${USER_NAME}" MOUNT_DIR="${MOUNT_DIR}" bash -s <<'EOS'
set -e
[[ "$(pwd)" == "$MOUNT_DIR"* ]] && cd "$HOME" || true
fusermount3 -u "$MOUNT_DIR" 2>/dev/null || fusermount -u "$MOUNT_DIR" 2>/dev/null || umount -l "$MOUNT_DIR" 2>/dev/null || true
EOS
}
# ----------------------------------------------------------------

init_store() {
  say "Encrypted store missing → initializing…"
  run_user_init
  ok "Encrypted store initialized."
}

mount_store() {
  say "Mounting encrypted OORBOTS…"
  run_user_mount
  ok "Mounted at ${MOUNT_DIR}."
}

kill_blockers() {
  say "Checking for processes using ${MOUNT_DIR}…"
  as_user "fuser -vm '${MOUNT_DIR}' || true"
  read -r -p "Kill blockers automatically? [y/N]: " yn
  [[ "${yn,,}" != "y" ]] && return 0
  PIDS="$(as_user "fuser -vm '${MOUNT_DIR}' 2>/dev/null | awk 'NR>1 {print \$2}' | sort -u" || true)"
  for p in ${PIDS:-}; do kill -HUP "$p" 2>/dev/null || true; done
  sleep 1
  for p in ${PIDS:-}; do kill "$p" 2>/dev/null || true; done
  sleep 1
  for p in ${PIDS:-}; do kill -9 "$p" 2>/dev/null || true; done
}

umount_store() {
  say "Unmounting (lock)…"
  if ! run_user_lock_lazy; then
    warn "Mount busy; handling…"
    kill_blockers
    run_user_lock_lazy || true
  fi
  ok "Locked."
}

migrate_plaintext() {
  # If locked but files exist in mount dir, stage them, mount, then copy back in.
  if ! is_mounted && as_user "shopt -s nullglob dotglob; files=( '${MOUNT_DIR}'/* ); (( \${#files[@]} > 0 ))"; then
    say "Found plaintext in ${MOUNT_DIR} while locked. Staging & re-mounting…"
    as_user "mkdir -p '${HOME_DIR}/OORBOTS_plain'"
    as_user "rsync -a '${MOUNT_DIR}/' '${HOME_DIR}/OORBOTS_plain/'"
    mount_store
    as_user "rsync -a '${HOME_DIR}/OORBOTS_plain/' '${MOUNT_DIR}/'"
    as_user "rm -rf '${HOME_DIR}/OORBOTS_plain'"
    ok "Plaintext moved into encrypted mount."
  fi
}

maybe_clone_repo() {
  if ! is_mounted; then mount_store; fi
  if as_user "[ -d '${MOUNT_DIR}/.git' ]"; then
    ok "Git repo already present."
    return
  fi
  say "Clone your GitHub repo (Git prompts: username, then paste PAT)."
  read -r -p "Clone now? [y/N]: " yn
  [[ "${yn,,}" != "y" ]] && return 0
  read -r -p "Enter GitHub repo (OWNER/REPO): " REPOPATH
  [[ -z "$REPOPATH" ]] && { warn "No repo provided; skipping clone."; return 0; }
  as_user "cd '${MOUNT_DIR}' && git clone 'https://github.com/${REPOPATH}.git' ."
  ok "Repo cloned into ${MOUNT_DIR}."
}

install_lock_unlock_user() {
  # Aliases for interactive shells
  as_user "grep -q 'alias unlock=' ~/.bashrc 2>/dev/null || echo 'alias unlock=\"gocryptfs ~/OORBOTS_encrypted ~/OORBOTS\"' >> ~/.bashrc"
  as_user "grep -q 'alias lock='   ~/.bashrc 2>/dev/null || echo 'alias lock=\"fusermount3 -u ~/OORBOTS 2>/dev/null || fusermount -u ~/OORBOTS 2>/dev/null || umount -l ~/OORBOTS 2>/dev/null || true\"' >> ~/.bashrc"

  # Executables for non-interactive shells too
  as_user "mkdir -p ~/.local/bin"
  as_user "cat > ~/.local/bin/unlock <<'EOU'
#!/usr/bin/env bash
set -e
gocryptfs "$HOME/OORBOTS_encrypted" "$HOME/OORBOTS"
mountpoint "$HOME/OORBOTS" && echo "Mounted."
EOU
chmod +x ~/.local/bin/unlock"

  as_user "cat > ~/.local/bin/lock <<'EOL'
#!/usr/bin/env bash
set -e
[[ "$(pwd)" == "$HOME/OORBOTS"* ]] && cd "$HOME" || true
fusermount3 -u "$HOME/OORBOTS" 2>/dev/null || fusermount -u "$HOME/OORBOTS" 2>/dev/null || umount -l "$HOME/OORBOTS" 2>/dev/null || true
mountpoint "$HOME/OORBOTS" >/dev/null 2>&1 || echo "Locked."
EOL
chmod +x ~/.local/bin/lock"

  as_user "grep -q 'export PATH=\$HOME/.local/bin:\$PATH' ~/.bashrc 2>/dev/null || echo 'export PATH=\$HOME/.local/bin:\$PATH' >> ~/.bashrc"
  ok "lock/unlock installed for ${USER_NAME}."
}

install_services_conf_and_user_helper() {
  # services.conf (user-owned)
  cat > "${SERVICES_CONF}" <<'EOC'
# Service configuration file
# These scripts live inside encrypted ~/OORBOTS
SCRIPTS_DIR=/home/iyushh/OORBOTS

prime=PrimeVideo-NextGen.py
netflix=Netflix-NextGen.py
crunchyroll=Crunchroll-NextGen.py
onesignal=OneSignal.py
broadcast=Multi-Broadcast.py
adult=Adult-NextGen.py
oorverse=OORverse.py
ai=Ai-NextGen.py
EOC
  chown "${USER_NAME}:${USER_NAME}" "${SERVICES_CONF}"
  chmod 600 "${SERVICES_CONF}"

  # user-local helper sourced by ~/.bashrc
  as_user "mkdir -p '${USER_CFG_DIR}'"
  cat > "${USER_HELPER}" <<'EOF'
# ~/.config/bash/vps_helpers.sh — user-local helpers for screen-managed services.
# Source from ~/.bashrc

SCRIPTS_DIR="${SCRIPTS_DIR:-$HOME/OORBOTS}"
CONFIG_FILE="$HOME/services.conf"
declare -A SERVICE_MAP

# Load SCRIPTS_DIR and mappings
if [[ -f "$CONFIG_FILE" ]]; then
  while IFS= read -r line; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    if [[ "$line" =~ ^SCRIPTS_DIR= ]]; then
      val="${line#SCRIPTS_DIR=}"
      val="${val%\"}"; val="${val#\"}"; val="${val%\'}"; val="${val#\'}"
      SCRIPTS_DIR="$val"; break
    fi
  done < "$CONFIG_FILE"
  while IFS='=' read -r key value; do
    key="$(echo "$key"   | tr -d '[:space:]')"
    value="$(echo "$value" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [[ -z "$key" || "$key" =~ ^# || "$key" == "SCRIPTS_DIR" ]] && continue
    lkey="$(echo "$key" | tr '[:upper:]' '[:lower:]')"
    SERVICE_MAP["$lkey"]="$value"
  done < "$CONFIG_FILE"
fi

# Show screen sessions if called with no args
screen() { if [ $# -eq 0 ]; then ps aux | grep SCREEN | grep -v grep || true; else command screen "$@"; fi; }

_ensure_mounted() {
  if ! mountpoint -q "$SCRIPTS_DIR"; then
    echo "❌ '$SCRIPTS_DIR' not mounted. Run: unlock"
    return 1
  fi
}

_vps_start() {
  local svc="$1"; local script="${SERVICE_MAP[$svc]}"
  if [ -z "$script" ]; then echo "Unknown service: $svc"; return 2; fi
  _ensure_mounted || return 3
  command screen -dmS "$svc" python3 "$SCRIPTS_DIR/$script"
  echo "✅ Started $svc -> $SCRIPTS_DIR/$script"
}

_vps_stop() {
  local svc="$1"
  command screen -S "$svc" -X quit 2>/dev/null || true
  echo "🛑 Stopped $svc"
}

_vps_status() {
  local svc="$1"
  ps aux | grep SCREEN | grep -i "$svc" | grep -v grep || true
}

# Case-insensitive service commands + "<script>.py stop"
command_not_found_handle() {
  local cmd="$1"; shift || true
  local lc_cmd="$(echo "$cmd" | tr '[:upper:]' '[:lower:]')"
  local action="$(echo "${1:-}" | tr '[:upper:]' '[:lower:]')"

  if [[ -n "${SERVICE_MAP[$lc_cmd]}" ]]; then
    case "$action" in
      start)  _vps_start "$lc_cmd"; return 0 ;;
      stop)   _vps_stop  "$lc_cmd"; return 0 ;;
      status) _vps_status "$lc_cmd"; return 0 ;;
      *) echo "Usage: $cmd start|stop|status"; return 0 ;;
    esac
  fi

  if [[ "$cmd" == *.py && "$action" == "stop" ]]; then
    pkill -f "$cmd" 2>/dev/null || true
    echo "🛑 Requested stop for $cmd"
    return 0
  fi

  printf "%s: command not found\n" "$cmd" >&2
  return 127
}
EOF

  chown "${USER_NAME}:${USER_NAME}" "${USER_HELPER}"
  chmod 644 "${USER_HELPER}"
  as_user "grep -q '. ~/.config/bash/vps_helpers.sh' ~/.bashrc || echo '. ~/.config/bash/vps_helpers.sh' >> ~/.bashrc"

  ok "services.conf and user helper installed."
}

final_message() {
  say "✅ COMPLETE — everything is under ${HOME_DIR} (no /root changes, no package installs)"
  cat <<EOF
------------------------------------------------------------
As user '${USER_NAME}':

  unlock      # mount (hidden prompt via alias)  OR run 'gocryptfs ~/OORBOTS_encrypted ~/OORBOTS'
  lock        # unmount (handles busy via lazy if needed)

Service commands (after unlock):
  prime start|stop|status
  netflix start|stop|status
  oorverse start|stop|status
  ai start|stop|status
  crunchyroll start|stop|status
  onesignal start|stop|status
  broadcast start|stop|status
  adult start|stop|status

Stop any script quickly:
  SomeScript.py stop

Config files:
  ~/services.conf
  ~/.config/bash/vps_helpers.sh

Encrypted data at rest:
  ~/OORBOTS_encrypted   (ciphertext — do not edit manually)
  ~/OORBOTS             (mountpoint — shows plaintext only when unlocked)
------------------------------------------------------------
EOF
}

### MAIN ###
need_root
ensure_user
ensure_dirs
if ! has_meta; then init_store; fi
if ! is_mounted; then mount_store; fi
migrate_plaintext
maybe_clone_repo
install_lock_unlock_user
install_services_conf_and_user_helper
final_message
