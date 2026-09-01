#!/usr/bin/env bash
#
# Build a TR4W AppImage. Runs directly on the build host -- no container.
# Prerequisites are installed once by setup-build-host.sh.
#
# Tunables, all environment variables:
#   WIDGETSET=qt5|qt6|gtk3        LCL interface to compile against (default qt5)
#   PROJECT=path/to/tr4w.lpi      Lazarus project file, relative to repo root
#   BINARY=path/to/binary         override if lazbuild's output path is unusual
#   VERSION=1.2.3                 defaults to `git describe`
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"

APP_ID="${APP_ID:-net.tr4w.TR4W}"
APP_NAME="${APP_NAME:-TR4W}"
BIN_NAME="${BIN_NAME:-tr4w}"
WIDGETSET="${WIDGETSET:-qt5}"
PROJECT="${PROJECT:-tr4w.lpi}"
BUILD_MODE="${BUILD_MODE:-Release}"
ARCH="${ARCH:-$(uname -m)}"
VERSION="${VERSION:-$(git -C "$REPO_ROOT" describe --tags --always 2>/dev/null || echo 0.0.0)}"
VERSION="${VERSION#v}"

BUILD_DIR="${REPO_ROOT}/build"
APPDIR="${BUILD_DIR}/AppDir"
OUTDIR="${OUTDIR:-${REPO_ROOT}/dist}"
TOOLDIR="${TOOLDIR:-${BUILD_DIR}/.appimage-tools}"

# linuxdeploy and appimagetool are themselves AppImages. Hosts without libfuse2
# (Ubuntu 22.04+ does not install it by default) cannot self-mount them, so tell
# them to self-extract instead. Harmless when FUSE is present.
export APPIMAGE_EXTRACT_AND_RUN=1
export ARCH VERSION

mkdir -p "$TOOLDIR" "$OUTDIR"
log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

command -v lazbuild >/dev/null || die "lazbuild not found -- run packaging/linux/setup-build-host.sh first"

fetch_tool() { # url dest
  [ -x "$2" ] && return 0
  log "fetching $(basename "$2")"
  wget -q --show-progress -O "$2" "$1"
  chmod +x "$2"
}

# ---------------------------------------------------------------- toolchain
LINUXDEPLOY="${TOOLDIR}/linuxdeploy-${ARCH}.AppImage"
APPIMAGETOOL="${TOOLDIR}/appimagetool-${ARCH}.AppImage"
fetch_tool "https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-${ARCH}.AppImage" "$LINUXDEPLOY"
fetch_tool "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-${ARCH}.AppImage" "$APPIMAGETOOL"

export PATH="${TOOLDIR}:${PATH}"
DEPLOY_ARGS=()
case "$WIDGETSET" in
  qt5|qt6)
    # Qt loads its platform plugin (platforms/libqxcb.so) with dlopen, so ldd
    # never sees it and linuxdeploy alone will not bundle it. Without the qt
    # plugin the AppImage dies at startup with
    #   "could not load the Qt platform plugin xcb"
    # even though every library it links resolved fine.
    fetch_tool "https://github.com/linuxdeploy/linuxdeploy-plugin-qt/releases/download/continuous/linuxdeploy-plugin-qt-${ARCH}.AppImage" \
               "${TOOLDIR}/linuxdeploy-plugin-qt-${ARCH}.AppImage"
    DEPLOY_ARGS+=(--plugin qt)
    # LCL reaches Qt indirectly through libQt5Pas, which sometimes defeats the
    # plugin's autodetection. Naming the modules explicitly makes it deterministic.
    export EXTRA_QT_MODULES="${EXTRA_QT_MODULES:-widgets gui core}"
    ;;
  gtk3|gtk2)
    fetch_tool "https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/master/linuxdeploy-plugin-gtk.sh" \
               "${TOOLDIR}/linuxdeploy-plugin-gtk.sh"
    DEPLOY_ARGS+=(--plugin gtk)
    ;;
  *) die "unknown WIDGETSET '${WIDGETSET}'" ;;
esac

# -------------------------------------------------------------------- build
log "building ${PROJECT}  (widgetset=${WIDGETSET}, mode=${BUILD_MODE}, version=${VERSION})"
lazbuild --build-mode="${BUILD_MODE}" --widgetset="${WIDGETSET}" "${REPO_ROOT}/${PROJECT}"

if [ -z "${BINARY:-}" ]; then
  guess="${REPO_ROOT}/$(dirname "$PROJECT")/${BIN_NAME}"
  if [ -x "$guess" ]; then
    BINARY="$guess"
  else
    BINARY="$(find "$REPO_ROOT" -maxdepth 4 -type f -perm -u+x -name "$BIN_NAME" -print -quit)"
  fi
fi
[ -n "${BINARY:-}" ] && [ -x "$BINARY" ] || die "built binary not found; set BINARY="
log "binary: ${BINARY}"

# ------------------------------------------------------------------- AppDir
log "assembling AppDir"
rm -rf "$APPDIR"
install -Dm755 "$BINARY"                        "${APPDIR}/usr/bin/${BIN_NAME}"
install -Dm644 "${HERE}/${APP_ID}.desktop"      "${APPDIR}/usr/share/applications/${APP_ID}.desktop"
install -Dm644 "${HERE}/${APP_ID}.metainfo.xml" "${APPDIR}/usr/share/metainfo/${APP_ID}.metainfo.xml"

icon_installed=""
for px in 256 128 64; do
  src="${HERE}/icons/${px}.png"
  [ -f "$src" ] || continue
  install -Dm644 "$src" "${APPDIR}/usr/share/icons/hicolor/${px}x${px}/apps/${APP_ID}.png"
  [ "$px" = 256 ] && icon_installed="${APPDIR}/usr/share/icons/hicolor/256x256/apps/${APP_ID}.png"
done
[ -n "$icon_installed" ] || die "packaging/linux/icons/256.png is required"

sed -i "s|@VERSION@|${VERSION}|g; s|@DATE@|$(date -u +%Y-%m-%d)|g" \
  "${APPDIR}/usr/share/metainfo/${APP_ID}.metainfo.xml"

log "bundling dependencies"
"$LINUXDEPLOY" \
  --appdir "$APPDIR" \
  --desktop-file "${APPDIR}/usr/share/applications/${APP_ID}.desktop" \
  --icon-file "$icon_installed" \
  --executable "${APPDIR}/usr/bin/${BIN_NAME}" \
  "${DEPLOY_ARGS[@]}"

# Fallback: if the qt plugin missed the platform plugin (it can, when Qt is
# reached only through libQt5Pas), place it by hand so the app can start.
if [ "${WIDGETSET#qt}" != "$WIDGETSET" ] && ! find "${APPDIR}" -name 'libqxcb.so' -print -quit | grep -q .; then
  log "qt plugin did not deploy libqxcb.so -- copying it manually"
  qxcb="$(find /usr/lib /usr/lib64 -path '*plugins/platforms/libqxcb.so' -print -quit 2>/dev/null || true)"
  [ -n "$qxcb" ] || die "libqxcb.so not found on this host; install libqt5gui5"
  install -Dm644 "$qxcb" "${APPDIR}/usr/plugins/platforms/libqxcb.so"
  cat > "${APPDIR}/usr/bin/qt.conf" <<'QTCONF'
[Paths]
Prefix = ../
Plugins = plugins
QTCONF
fi

# ---------------------------------------------------------------- AppImage
log "packing AppImage"
OUTPUT="${OUTDIR}/${APP_NAME}-${VERSION}-${ARCH}.AppImage"
rm -f "$OUTPUT"
# Add -u "gh-releases-zsync|ny4i|TR4W|latest|${APP_NAME}-*-${ARCH}.AppImage.zsync"
# once releases are published, to enable delta updates via AppImageUpdate.
"$APPIMAGETOOL" --comp zstd "$APPDIR" "$OUTPUT"
sha256sum "$OUTPUT" > "${OUTPUT}.sha256"

# ------------------------------------------------------- compatibility floor
# An AppImage does not bundle glibc, so it runs only on hosts whose glibc is at
# least as new as the one it was compiled against. Report that explicitly --
# otherwise the first sign of trouble is a user on an older distro getting
# "version `GLIBC_2.38' not found" and no idea why.
floor="$( { objdump -T "${APPDIR}/usr/bin/${BIN_NAME}" 2>/dev/null
            find "${APPDIR}/usr/lib" -name '*.so*' -type f -exec objdump -T {} + 2>/dev/null
          } | sed -n 's/.*GLIBC_\([0-9][0-9.]*\).*/\1/p' | sort -V | tail -1 )"

cat <<EOF

  $(printf '\033[1;32m%s\033[0m' "AppImage built")
    file        $OUTPUT
    size        $(du -h "$OUTPUT" | cut -f1)
    widgetset   $WIDGETSET
    needs glibc >= ${floor:-unknown}   <- oldest distro this will run on

  Sanity-check on a machine OLDER than this build host before every release.
EOF
