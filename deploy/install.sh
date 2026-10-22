#!/usr/bin/env bash
# Run on the Oracle VM after extracting the release: sudo bash deploy/install.sh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo 'Run with sudo: sudo bash deploy/install.sh' >&2
    exit 1
fi

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
python_bin=$(command -v "${PYTHON_BIN:-python3}" || true)
if [[ -z "$python_bin" || "$python_bin" == *[[:space:]]* ]]; then
    echo 'Python executable not found. Install Python 3.11+ first.' >&2
    exit 1
fi
"$python_bin" -c 'import sys, sqlite3; assert sys.version_info >= (3, 11), "Python 3.11+ is required"'
command -v systemctl >/dev/null || { echo 'A Linux server with systemd is required.' >&2; exit 1; }
[[ -f "$source_dir/app.py" && -f "$source_dir/static/index.html" ]] || { echo 'Application files are missing.' >&2; exit 1; }
[[ -d "$source_dir/tests" ]] || { echo 'Release tests are missing.' >&2; exit 1; }

# Validate the application before changing the installed service.
(cd "$source_dir" && "$python_bin" -m unittest discover -s tests -v)

unit_path=/etc/systemd/system/memory-brief.service
current_path=/opt/memory-brief/current
if [[ -e "$current_path" && ! -L "$current_path" ]]; then
    echo 'Existing /opt/memory-brief/current is not a managed symlink; refusing to replace it.' >&2
    exit 1
fi
if [[ -e "$unit_path" ]] && ! head -n 1 "$unit_path" | grep -qx '# Managed by Memory Brief deploy/install.sh'; then
    echo "Existing unmanaged $unit_path found; leave it unchanged and review manually." >&2
    exit 1
fi
if id memory-brief >/dev/null 2>&1; then
    account_home=$(getent passwd memory-brief | cut -d: -f6)
    account_uid=$(id -u memory-brief)
    if [[ "$account_home" != /var/lib/memory-brief || "$account_uid" -eq 0 ]]; then
        echo 'An unrelated memory-brief account already exists. No changes made.' >&2
        exit 1
    fi
else
    nologin_bin=$(command -v nologin || echo /sbin/nologin)
    useradd --system --user-group --home-dir /var/lib/memory-brief --shell "$nologin_bin" memory-brief
fi

install -d -m 0755 /opt/memory-brief/releases
install -d -m 0750 -o memory-brief -g memory-brief /var/lib/memory-brief
release_dir=$(mktemp -d /opt/memory-brief/releases/release-XXXXXXXX)
install -m 0644 "$source_dir/app.py" "$release_dir/app.py"
install -m 0644 "$source_dir/README.md" "$release_dir/README.md"
install -d -m 0755 "$release_dir/static"
install -m 0644 "$source_dir/static/"* "$release_dir/static/"
chmod 0755 "$release_dir"

# Keep the database outside releases. Back up an existing database consistently.
if [[ -f /var/lib/memory-brief/memory.db ]]; then
    install -d -m 0700 /var/backups/memory-brief
    "$python_bin" - <<'PY'
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
backup = Path('/var/backups/memory-brief') / ('memory-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.db')
with sqlite3.connect('/var/lib/memory-brief/memory.db') as src, sqlite3.connect(backup) as dst:
    src.backup(dst)
backup.chmod(0o600)
print('Database backup:', backup)
PY
fi

if [[ -e "$unit_path" ]]; then
    cp -p "$unit_path" "$unit_path.backup-$(date -u +%Y%m%dT%H%M%S)"
fi
"$python_bin" - "$source_dir/deploy/memory-brief.service" "$unit_path" "$python_bin" <<'PY'
import sys
from pathlib import Path
source, target, executable = sys.argv[1:]
Path(target).write_text(Path(source).read_text().replace('@PYTHON@', executable))
Path(target).chmod(0o644)
PY

previous_release=$(readlink "$current_path" || true)
ln -s "$release_dir" /opt/memory-brief/.current-next
mv -Tf /opt/memory-brief/.current-next "$current_path"
systemctl daemon-reload
systemctl enable memory-brief.service
systemctl restart memory-brief.service

if "$python_bin" - <<'PY'
import json, time, urllib.request
for attempt in range(15):
    try:
        with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=2) as response:
            assert json.load(response)['ok'] is True
        with urllib.request.urlopen('http://127.0.0.1:8000/api/dashboard', timeout=3) as response:
            data = json.load(response)
        print('Application responded; stored articles:', len(data['articles']))
        print('Next collection (KST):', data['next_run'])
        if data['collecting']:
            print('RSS collection is still running; check last_run after it finishes.')
        elif data['last_run']:
            print('Last collection feeds:', data['last_run']['successful'], '/ 6')
        break
    except Exception:
        if attempt == 14:
            raise SystemExit('Readiness check failed. Inspect: sudo journalctl -u memory-brief -n 50')
        time.sleep(1)
PY
then
    echo 'Installed. The service restarts after failures and server reboots.'
    echo 'Access is local-only. Use an SSH tunnel; do not open public port 8000.'
else
    if [[ -n "$previous_release" && -d "$previous_release" ]]; then
        ln -s "$previous_release" /opt/memory-brief/.current-rollback
        mv -Tf /opt/memory-brief/.current-rollback "$current_path"
        systemctl restart memory-brief.service
        echo 'The previous application release was restored.' >&2
    else
        systemctl stop memory-brief.service
    fi
    exit 1
fi
