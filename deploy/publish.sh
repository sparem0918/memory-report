#!/usr/bin/env bash
# Ubuntu public HTTPS entry point. Run only on the dedicated Memory Brief VM.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo 'Run with sudo: sudo bash deploy/publish.sh YOUR_HOSTNAME' >&2
    exit 1
fi
web_host=${1:-}
if [[ ! "$web_host" =~ ^[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ || "$web_host" != *.* || ${#web_host} -gt 253 ]]; then
    echo 'Supply a DNS hostname only, without https://, a port, or a path.' >&2
    exit 1
fi
if [[ ! -f /opt/memory-brief/current/app.py ]]; then
    echo 'Install the app on the dedicated memory-brief VM first.' >&2
    exit 1
fi
systemctl is-active --quiet memory-brief.service || { echo 'The Memory Brief service is not active.' >&2; exit 1; }
python3 - "$web_host" <<'PY'
import json, socket, sys, urllib.request
try:
    addresses = sorted({r[4][0] for r in socket.getaddrinfo(sys.argv[1], None, socket.AF_INET)})
except socket.gaierror:
    raise SystemExit('Hostname DNS did not resolve. No public configuration changed.')
print('Hostname IPv4 addresses:', ', '.join(addresses))
with urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5) as response:
    assert json.load(response)['ok'] is True
PY

# Keep any customized, pre-existing web server configuration untouched.
caddy_config=/etc/caddy/Caddyfile
if [[ -f "$caddy_config" ]] && ! head -n 1 "$caddy_config" | grep -qx '# Managed by Memory Brief deploy/publish.sh'; then
    expected_md5=$(dpkg-query -W -f='${Conffiles}\n' caddy 2>/dev/null | awk '$1=="/etc/caddy/Caddyfile" {print $2}' || true)
    actual_md5=$(md5sum "$caddy_config" | cut -d' ' -f1)
    if [[ -z "$expected_md5" || "$actual_md5" != "$expected_md5" ]]; then
        echo 'A customized Caddyfile already exists. It was left unchanged; review manually.' >&2
        exit 1
    fi
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y caddy

choose_web_password() {
    echo 'Choose the website password. It stays on this server; do not send it in chat.'
    echo 'Input is hidden. Press Enter after each entry. Ctrl+C cancels.'
    while true; do
        read -r -s -p 'Password (at least 12 characters): ' web_password </dev/tty
        echo
        read -r -s -p 'Repeat password: ' password_repeat </dev/tty
        echo
        if [[ ${#web_password} -lt 12 ]]; then
            echo 'Too short: use at least 12 characters. Please try again.' >&2
            unset web_password password_repeat
            continue
        fi
        if [[ "$web_password" != "$password_repeat" ]]; then
            echo 'The two entries do not match. Type the SAME password twice. Please try again.' >&2
            unset web_password password_repeat
            continue
        fi
        echo 'Password entries accepted.'
        break
    done
}

choose_web_password
password_hash=$(printf '%s\n' "$web_password" | caddy hash-password)
unset web_password password_repeat

temporary_config=$(mktemp /etc/caddy/.memory-brief-XXXXXXXX)
trap 'rm -f "$temporary_config"' EXIT
cat > "$temporary_config" <<EOF
# Managed by Memory Brief deploy/publish.sh
$web_host {
    encode gzip
    basicauth {
        memory $password_hash
    }
    reverse_proxy 127.0.0.1:8000
}
EOF
caddy validate --config "$temporary_config" --adapter caddyfile
if [[ -f "$caddy_config" ]]; then
    install -d -m 0700 /var/backups/memory-brief
    backup_config=$(mktemp /var/backups/memory-brief/Caddyfile-XXXXXXXX)
    install -m 0600 "$caddy_config" "$backup_config"
    echo "Previous Caddyfile backed up: $backup_config"
fi
install -m 0640 -o root -g caddy "$temporary_config" "$caddy_config"

# Add just the website ports; leave SSH and all existing firewall protections intact.
if command -v ufw >/dev/null && ufw status | grep -q '^Status: active'; then
    ufw allow 80/tcp
    ufw allow 443/tcp
else
    DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
    for port in 80 443; do
        if ! iptables -C INPUT -p tcp --dport "$port" -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT 2>/dev/null; then
            iptables -I INPUT 1 -p tcp --dport "$port" -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT
        fi
    done
    install -d -m 0700 /var/backups/memory-brief
    if [[ -f /etc/iptables/rules.v4 ]]; then
        rules_backup=$(mktemp /var/backups/memory-brief/iptables-v4-XXXXXXXX)
        install -m 0600 /etc/iptables/rules.v4 "$rules_backup"
    fi
    netfilter-persistent save
fi
systemctl enable caddy
systemctl restart caddy
systemctl is-active --quiet caddy || { echo 'Caddy startup failed. Inspect: sudo journalctl -u caddy -n 50' >&2; exit 1; }

echo
echo "HTTPS configured for: https://$web_host"
echo 'Website username: memory'
echo 'Use the password entered above.'
echo 'Oracle ingress TCP 80 and 443 must also be allowed on the NEW memory-brief network.'
echo 'Keep port 8000 private. Certificate issuance and browser access still need verification.'
echo 'For certificate status: sudo journalctl -u caddy -n 50 --no-pager'
