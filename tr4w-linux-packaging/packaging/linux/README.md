# TR4W Linux packaging

Produces a single-file `TR4W-<version>-x86_64.AppImage` that users download,
`chmod +x`, and run. No installer, no package manager, no runtime to install.

## Files

| File | Purpose |
|---|---|
| `build-appimage.sh` | The build. Everything is env-var driven. |
| `Dockerfile` | Ubuntu 22.04 build image — pins the glibc floor. |
| `setup-build-host.sh` | Same dependencies, installed on bare metal instead. |
| `net.tr4w.TR4W.desktop` | Desktop entry. |
| `net.tr4w.TR4W.metainfo.xml` | AppStream metadata (also required by Flathub later). |
| `icons/256.png` | Required. 128/64 optional. |

## Build

Containerized (what CI does — the container is a build tool, users never see it):

```sh
docker build -t tr4w-build:ubuntu-22.04 -f packaging/linux/Dockerfile packaging/linux
docker run --rm -v "$PWD:/src" -w /src --user "$(id -u):$(id -g)" \
  -e HOME=/tmp -e WIDGETSET=qt5 tr4w-build:ubuntu-22.04 \
  ./packaging/linux/build-appimage.sh
```

Bare metal:

```sh
sudo ./packaging/linux/setup-build-host.sh   # once
./packaging/linux/build-appimage.sh
```

## The glibc floor

An AppImage does not bundle glibc, so it runs only where glibc is at least as
new as the machine that compiled it. **The build host's distro is the oldest
distro your users can be on.** Every build ends with:

```
needs glibc >= 2.35   <- oldest distro this will run on
```

Ubuntu 22.04 → glibc 2.35 → works on Ubuntu 22.04+, Debian 12+, Mint 21+,
Fedora 36+. Building bare metal on a current distro will silently raise this.

## Widgetset

`WIDGETSET=qt5` (default). LCL-Qt5 links `libQt5Pas`, which most distros do not
package — normally the worst part of shipping a Lazarus/Qt app, and a non-issue
here because the AppImage bundles it.

Qt loads its platform plugin (`platforms/libqxcb.so`) via `dlopen`, so `ldd`
never sees it and it will not be bundled automatically. `linuxdeploy-plugin-qt`
handles this; the script falls back to placing `libqxcb.so` and a `qt.conf` by
hand if the plugin fails to detect Qt through the `libQt5Pas` indirection.
Symptom if this ever regresses: *"could not load the Qt platform plugin xcb"*.

`WIDGETSET=gtk3` also works and switches to `linuxdeploy-plugin-gtk`.

## libfuse2

AppImages self-mount via FUSE, and Ubuntu 22.04+ does not install `libfuse2` by
default. Users hitting `dlopen(): error loading libfuse.so.2` can either install
`libfuse2` or run `./TR4W-*.AppImage --appimage-extract-and-run`. Worth putting
in the download page.

## Serial ports

No sandbox, so CAT control, WinKeyer and friends behave exactly as a native
build. Users still need to be in the `dialout` group.

## Later: Flatpak

`net.tr4w.TR4W.metainfo.xml` is already written to Flathub's requirements and
moves over unchanged. Note Flathub builds from source *on their infrastructure*
(users still just `flatpak install` a binary), and a Lazarus app there depends
on `org.freedesktop.Sdk.Extension.freepascal`, which is thinly maintained.
