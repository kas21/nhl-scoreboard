#!/usr/bin/env bash
# Optional Pi tuning for flicker-free output (reboot required):
#  - blacklist the onboard audio driver (conflicts with the matrix PWM)
#  - reserve CPU core 3 for the matrix refresh thread
set -euo pipefail
[ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "$@"
echo "blacklist snd_bcm2835" > /etc/modprobe.d/blacklist-rgb-matrix.conf
CMDLINE=/boot/firmware/cmdline.txt; [ -f "$CMDLINE" ] || CMDLINE=/boot/cmdline.txt
grep -q isolcpus "$CMDLINE" || sed -i 's/$/ isolcpus=3/' "$CMDLINE"
echo "done — reboot to apply"
