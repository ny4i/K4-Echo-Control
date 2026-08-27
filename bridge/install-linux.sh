#!/usr/bin/env bash
# Install the bridge on a Raspberry Pi (or any systemd Linux box).
#
#   sudo ./bridge/install-linux.sh
#
# Creates a dedicated unprivileged user, a virtualenv under /opt/k4echo, and a
# systemd unit. Re-running it upgrades the code in place.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "run this with sudo" >&2
    exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PREFIX=/opt/k4echo
CONFDIR=/etc/k4echo
SERVICE_USER=k4bridge

echo "==> creating ${SERVICE_USER} service account"
if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --home-dir "${PREFIX}" --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

echo "==> installing code to ${PREFIX}"
install -d -o root -g root -m 0755 "${PREFIX}"
rm -rf "${PREFIX}/k4echo"
cp -r "${ROOT}/k4echo" "${PREFIX}/k4echo"
find "${PREFIX}/k4echo" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

echo "==> creating virtualenv"
if [[ ! -x "${PREFIX}/venv/bin/python" ]]; then
    python3 -m venv "${PREFIX}/venv"
fi
"${PREFIX}/venv/bin/pip" install --quiet --upgrade pip
"${PREFIX}/venv/bin/pip" install --quiet -r "${ROOT}/bridge/requirements.txt"

echo "==> setting up ${CONFDIR}"
install -d -o root -g "${SERVICE_USER}" -m 0750 "${CONFDIR}"
if [[ ! -f "${CONFDIR}/bridge.ini" ]]; then
    install -o root -g "${SERVICE_USER}" -m 0640 \
        "${ROOT}/bridge/bridge.ini.example" "${CONFDIR}/bridge.ini"
    echo "    wrote ${CONFDIR}/bridge.ini -- EDIT IT before starting the service"
else
    echo "    ${CONFDIR}/bridge.ini already exists, left untouched"
fi

echo "==> installing systemd unit"
install -m 0644 "${ROOT}/bridge/systemd/k4-bridge.service" /etc/systemd/system/
systemctl daemon-reload

cat <<EOF

Installed.

  1. Edit ${CONFDIR}/bridge.ini  (at minimum: [radio] host)
  2. Check the radio is reachable:
       sudo -u ${SERVICE_USER} ${PREFIX}/venv/bin/python -m k4echo.bridge \\
           --config ${CONFDIR}/bridge.ini --selftest
  3. Start it:
       sudo systemctl enable --now k4-bridge
       journalctl -u k4-bridge -f
EOF
