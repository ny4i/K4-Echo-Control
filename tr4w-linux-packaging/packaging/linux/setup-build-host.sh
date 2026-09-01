#!/usr/bin/env bash
#
# One-time setup for the TR4W Linux build host (Debian/Ubuntu).
# Run this once on the self-hosted runner, by hand, with sudo. The CI workflow
# does not install anything -- it only checks that this has been done.
#
#   sudo ./packaging/linux/setup-build-host.sh
#
# IMPORTANT: whatever distro you run this on becomes the OLDEST distro a TR4W
# AppImage will run on. An AppImage is portable downward from its build host's
# glibc, never upward. Ubuntu 22.04 (glibc 2.35) covers Ubuntu 22.04+,
# Debian 12+, Mint 21+ and Fedora 36+, which is a sensible floor in 2026.
# build-appimage.sh prints the resulting floor at the end of every build.
#
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run me as root (sudo)" >&2; exit 1; }

export DEBIAN_FRONTEND=noninteractive

echo "==> build host: $(. /etc/os-release && echo "$PRETTY_NAME")  glibc $(ldd --version | head -1 | grep -o '[0-9]\+\.[0-9]\+$')"

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates wget curl file git make patch unzip xz-utils \
  binutils libc6-dev pkg-config \
  fpc fpc-source lazarus lazarus-src \
  qtbase5-dev qtbase5-dev-tools qttools5-dev-tools \
  libqt5gui5 libqt5widgets5 libqt5core5a libqt5x11extras5 \
  libx11-dev libxext-dev libxrender-dev libxrandr-dev libxi-dev

# --- libQt5Pas ------------------------------------------------------------
# The Pascal<->Qt binding shim that LCL-Qt5 links against. Packaged on some
# distros and not others, so try apt and fall back to upstream releases.
if ! apt-get install -y --no-install-recommends libqt5pas-dev libqt5pas1 2>/dev/null; then
  echo "==> libqt5pas not in apt; installing from upstream releases"
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  # Pin a known release rather than tracking latest, so builds stay reproducible.
  LIBQT5PAS_VER="${LIBQT5PAS_VER:-1.2.15}"
  base="https://github.com/davidbannon/libqt5pas/releases/download/v${LIBQT5PAS_VER}"
  for deb in "libqt5pas1_${LIBQT5PAS_VER}-1_amd64.deb" "libqt5pas-dev_${LIBQT5PAS_VER}-1_amd64.deb"; do
    wget -q -O "${tmp}/${deb}" "${base}/${deb}" || {
      echo "!! could not fetch ${deb}." >&2
      echo "   Check the asset names at https://github.com/davidbannon/libqt5pas/releases" >&2
      echo "   and set LIBQT5PAS_VER, or build the bindings from the Lazarus source tree" >&2
      echo "   (lcl/interfaces/qt5/cbindings)." >&2
      exit 1
    }
  done
  dpkg -i "${tmp}"/*.deb || apt-get install -f -y
fi

echo
echo "==> toolchain:"
echo "    fpc      $(fpc -iV 2>/dev/null || echo MISSING)"
echo "    lazbuild $(lazbuild --version 2>/dev/null | tail -1 || echo MISSING)"
echo "    libQt5Pas $(ldconfig -p | grep -c libQt5Pas) entr(y|ies) in ldconfig"
echo
echo "Done. Now run: ./packaging/linux/build-appimage.sh"
