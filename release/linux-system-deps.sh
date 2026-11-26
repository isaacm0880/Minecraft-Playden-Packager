#!/bin/sh
set -eu
# Build/test-container dependencies, not an instruction to modify the end user's OS.
apt-get update
apt-get install -y --no-install-recommends git build-essential binutils file ca-certificates desktop-file-utils \
  libgl1 libegl1 libgl1-mesa-dri libdbus-1-3 libxkbcommon0 libxkbcommon-x11-0 \
  libxcb-cursor0 libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 \
  libxcb-render-util0 libxcb-shape0 libxcb-xfixes0 libxcb-xinerama0 libxcb-sync1 \
  libxcb-xkb1 libfontconfig1 libfreetype6 libxrender1 libxi6 libsm6 libice6 \
  libwayland-client0 libwayland-cursor0 libwayland-egl1 libxext6 xvfb xauth \
  fonts-dejavu-core default-jdk-headless
