#!/usr/bin/env bash
set -euo pipefail

OUTDIR="/tmp/requirements_export_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUTDIR"

echo "Export directory: $OUTDIR"

# 1) Python (system)
echo "[*] Exporting system python packages (pip)"
if command -v pip >/dev/null 2>&1; then
  pip freeze --all > "$OUTDIR/pip_freeze.txt" || pip list --format=freeze > "$OUTDIR/pip_freeze.txt"
fi
if command -v pip3 >/dev/null 2>&1; then
  pip3 freeze --all > "$OUTDIR/pip3_freeze.txt" || pip3 list --format=freeze > "$OUTDIR/pip3_freeze.txt"
fi

# 1b) Attempt to find virtualenvs/venvs under /home, /root, /opt and export pip from each
echo "[*] Searching for virtualenvs (this may take a little)"
find /home /root /opt -maxdepth 4 -type f -name "activate" 2>/dev/null | while read -r ACT; do
  VENV_DIR="$(dirname "$ACT")"
  PIP_BIN="$VENV_DIR/pip"
  PIP3_BIN="$VENV_DIR/pip3"
  name="$(echo "$VENV_DIR" | sed 's#/##g' | tr '/' '_' )"
  if [ -x "$PIP_BIN" ]; then
    "$PIP_BIN" freeze > "$OUTDIR/venv_${name}_pip_freeze.txt" || true
  elif [ -x "$PIP3_BIN" ]; then
    "$PIP3_BIN" freeze > "$OUTDIR/venv_${name}_pip_freeze.txt" || true
  fi
done || true

# 2) Debian/Ubuntu (dpkg/apt)
if command -v dpkg >/dev/null 2>&1 && command -v apt >/dev/null 2>&1; then
  echo "[*] Exporting dpkg/apt lists"
  dpkg --get-selections > "$OUTDIR/dpkg_get_selections.txt" || true
  apt-mark showmanual > "$OUTDIR/apt_manual_packages.txt" || true
  # history logs
  if [ -f /var/log/apt/history.log ]; then
    cp /var/log/apt/history.log "$OUTDIR/apt_history.log" || true
  fi
fi

# 3) RHEL/CentOS/Fedora (rpm / dnf / yum)
if command -v rpm >/dev/null 2>&1; then
  echo "[*] Exporting rpm package list"
  rpm -qa --qf '%{NAME}-%{VERSION}-%{RELEASE}.%{ARCH}\n' > "$OUTDIR/rpm_packages.txt" || rpm -qa > "$OUTDIR/rpm_packages.txt"
fi
if command -v yum >/dev/null 2>&1; then
  yum history > "$OUTDIR/yum_history.txt" || true
fi
if command -v dnf >/dev/null 2>&1; then
  dnf history > "$OUTDIR/dnf_history.txt" || true
fi

# 4) Node / npm (global)
if command -v npm >/dev/null 2>&1; then
  echo "[*] Exporting npm global packages"
  npm ls -g --depth=0 --json > "$OUTDIR/npm_global.json" || npm ls -g --depth=0 > "$OUTDIR/npm_global.txt"
fi
if command -v yarn >/dev/null 2>&1; then
  yarn global list --json > "$OUTDIR/yarn_global.json" || true
fi

# 5) Ruby gems
if command -v gem >/dev/null 2>&1; then
  echo "[*] Exporting gem list"
  gem list --local > "$OUTDIR/gem_list.txt" || true
fi

# 6) PHP composer (global)
if command -v composer >/dev/null 2>&1; then
  echo "[*] Exporting composer global packages"
  composer global show --format=json > "$OUTDIR/composer_global.json" || composer global show > "$OUTDIR/composer_global.txt"
fi

# 7) Snap & flatpak
if command -v snap >/dev/null 2>&1; then
  echo "[*] Exporting snap list"
  snap list > "$OUTDIR/snap_list.txt" || true
fi
if command -v flatpak >/dev/null 2>&1; then
  echo "[*] Exporting flatpak list"
  flatpak list --app --columns=application,branch,origin > "$OUTDIR/flatpak_list.txt" || true
fi

# 8) pipx
if command -v pipx >/dev/null 2>&1; then
  echo "[*] Exporting pipx list"
  pipx list > "$OUTDIR/pipx_list.txt" || true
fi

# 9) Docker images / containers
if command -v docker >/dev/null 2>&1; then
  echo "[*] Exporting docker images and containers"
  docker images --format "{{.Repository}}:{{.Tag}} {{.ID}}" > "$OUTDIR/docker_images.txt" || true
  docker ps -a --format "{{.Names}} {{.Image}} {{.Status}}" > "$OUTDIR/docker_containers.txt" || true
fi

# 10) System info and PATH for reproducibility
echo "[*] Exporting system info"
uname -a > "$OUTDIR/uname.txt"
lsb_release -a 2>/dev/null > "$OUTDIR/lsb_release.txt" || true
cat /etc/os-release > "$OUTDIR/os_release.txt" || true
echo "$PATH" > "$OUTDIR/PATH.txt"

# 11) Optional: installed services (systemd)
if command -v systemctl >/dev/null 2>&1; then
  systemctl list-unit-files --type=service > "$OUTDIR/systemd_unit_files.txt" || true
  systemctl --type=service --state=running --no-pager > "$OUTDIR/systemd_running_services.txt" || true
fi

# 12) Helpful hints file
cat > "$OUTDIR/README.txt" <<'EOF'
This folder contains exported package lists from the server.
Files:
 - pip_freeze.txt, pip3_freeze.txt : system python packages
 - venv_* : pip freeze for detected virtualenvs (if found)
 - dpkg_get_selections.txt / apt_manual_packages.txt : Debian/Ubuntu installed packages
 - rpm_packages.txt : RPM-based packages (if applicable)
 - npm_global.json : global npm packages
 - gem_list.txt : ruby gems
 - composer_global.json : composer packages
 - docker_images.txt / docker_containers.txt : docker images & containers
 - snap_list.txt / flatpak_list.txt : snaps and flatpaks
 - systemd_*.txt : systemd services
Use these outputs as a starting point. For full reproducibility you will often also need config files, service unit files, and project-level dependency files (requirements.txt in project folders).
EOF

# 13) Compress for download
ARCHIVE="/tmp/requirements_bundle_$(date +%Y%m%d_%H%M%S).tar.gz"
tar -czf "$ARCHIVE" -C "$(dirname "$OUTDIR")" "$(basename "$OUTDIR")"
echo "Created archive: $ARCHIVE"
echo
echo "Done. You can download the archive with scp, rsync, sftp, or start a temporary HTTP server inside $(dirname "$OUTDIR") (see instructions)."
