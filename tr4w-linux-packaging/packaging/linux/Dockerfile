# TR4W Linux build container.
#
# WHY A CONTAINER: an AppImage is portable only *downward* from the glibc it was
# compiled against, so the build host's distro silently becomes the oldest distro
# users can run on. Pinning that here means upgrading the runner's OS never
# changes what TR4W supports. Ubuntu 22.04 (glibc 2.35) covers Ubuntu 22.04+,
# Debian 12+, Mint 21+ and Fedora 36+ -- a sensible floor in 2026.
#
# This ships nothing to users. It is a build tool; the output is a plain
# AppImage that runs with no container runtime anywhere in sight.
#
# Do NOT bump this base image for convenience -- doing so drops support for
# every distro older than the new base. build-appimage.sh prints the resulting
# glibc floor at the end of every build; that line is the canary.
FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive

# The package list lives in setup-build-host.sh so the container and bare-metal
# paths cannot drift apart. One source of truth, two ways to consume it.
COPY setup-build-host.sh /tmp/setup-build-host.sh
RUN /tmp/setup-build-host.sh \
 && rm -f /tmp/setup-build-host.sh \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /src
