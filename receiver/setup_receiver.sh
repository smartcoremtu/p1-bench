#!/usr/bin/env bash
# Prepare a Raspberry Pi (4 or 5, Raspberry Pi OS Lite 64-bit, Bookworm or later) as the
# P1 reference receiver bench for John. Safe to run more than once.
#
#   sudo ./setup_receiver.sh [hostname]      (default hostname: p1-receiver)
#
# What it does:
#   1. enables the full PL011 UART on GPIO14 (TX) / GPIO15 (RX) and removes the serial console
#   2. adds the login user to 'dialout' so it can open the serial port without sudo
#   3. installs git, Python venv, chrony (accurate time for log comparison)
#   4. creates /opt/p1bench/.venv with pinned pyserial and dsmr_parser
#   5. installs a systemd unit for John's receiver (installed but NOT enabled)
#   6. turns off SSH password login, but only if the user already has an SSH key installed
# It changes boot files, so reboot afterwards. Backups are written next to each file (*.p1bak).
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
HOST="${1:-p1-receiver}"
USER_NAME="${SUDO_USER:-pi}"
BENCH=/opt/p1bench
PYSERIAL=3.5
DSMR_PARSER=1.11.2

# Boot files moved to /boot/firmware in Bookworm
if [[ -f /boot/firmware/config.txt ]]; then BOOT=/boot/firmware; else BOOT=/boot; fi
CONFIG="$BOOT/config.txt"
CMDLINE="$BOOT/cmdline.txt"
MODEL=$(tr -d '\0' </proc/device-tree/model)
echo "== $MODEL, boot files in $BOOT, user $USER_NAME"

backup() { [[ -f "$1.p1bak" ]] || cp -a "$1" "$1.p1bak"; }

# ---- 1. UART -------------------------------------------------------------------------
backup "$CONFIG"; backup "$CMDLINE"
case "$MODEL" in
  *"Raspberry Pi 5"*)
    OVERLAY="dtparam=uart0=on"           # GPIO14/15 -> /dev/ttyAMA0 (the debug header is ttyAMA10)
    UART_DEV=/dev/ttyAMA0 ;;
  *"Raspberry Pi 4"*|*"Raspberry Pi 3"*)
    OVERLAY="dtoverlay=disable-bt"       # gives the PL011 back to GPIO14/15 instead of Bluetooth
    systemctl disable --now hciuart.service 2>/dev/null || true
    UART_DEV=/dev/ttyAMA0 ;;
  *)
    echo "!! untested model; check the UART overlay by hand"; OVERLAY=""; UART_DEV=/dev/serial0 ;;
esac
# Appended as one block under its own [all], so it applies whatever section ends the file
if ! grep -q "^# p1bench" "$CONFIG"; then
  printf '\n# p1bench: full UART on GPIO14/15 for the P1 receiver\n[all]\nenable_uart=1\n%s\n' "$OVERLAY" >>"$CONFIG"
fi
# no Linux console on the UART, or it will talk back over the data line
sed -i -E 's/console=(serial0|ttyAMA0|ttyS0),[0-9]+ ?//g' "$CMDLINE"
systemctl disable --now serial-getty@ttyAMA0.service serial-getty@serial0.service serial-getty@ttyS0.service 2>/dev/null || true

# ---- 2. permissions -------------------------------------------------------------------
usermod -aG dialout "$USER_NAME"

# ---- 3. packages ----------------------------------------------------------------------
apt-get update -qq
apt-get install -y -qq git python3-venv python3-pip chrony
timedatectl set-ntp true || true

# ---- 4. Python environment ------------------------------------------------------------
mkdir -p "$BENCH/logs"
[[ -d "$BENCH/.venv" ]] || python3 -m venv "$BENCH/.venv"
"$BENCH/.venv/bin/pip" install -q --upgrade pip
"$BENCH/.venv/bin/pip" install -q "pyserial==$PYSERIAL" "dsmr_parser==$DSMR_PARSER"
cat >"$BENCH/receiver.env" <<EOF
# Settings for John's receiver (read by p1-receiver.service)
P1_SERIAL_DEVICE=$UART_DEV
P1_BAUD=115200
P1_LOG_DIR=$BENCH/logs
EOF
chown -R "$USER_NAME:$USER_NAME" "$BENCH"

# ---- 5. service for the receiver (John's code goes in $BENCH/repo/receiver/receiver.py) --
cat >/etc/systemd/system/p1-receiver.service <<EOF
[Unit]
Description=SmartCORE P1 reference receiver
After=network-online.target time-sync.target

[Service]
User=$USER_NAME
EnvironmentFile=$BENCH/receiver.env
WorkingDirectory=$BENCH/repo
ExecStart=$BENCH/.venv/bin/python receiver/receiver.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
echo "   p1-receiver.service installed (enable later: sudo systemctl enable --now p1-receiver)"

# ---- 6. hostname and SSH ----------------------------------------------------------------
if [[ "$(hostname)" != "$HOST" ]]; then
  hostnamectl set-hostname "$HOST"
  sed -i "s/127.0.1.1.*/127.0.1.1\t$HOST/" /etc/hosts
fi
KEYS="/home/$USER_NAME/.ssh/authorized_keys"
if [[ -s "$KEYS" ]]; then
  echo "PasswordAuthentication no" >/etc/ssh/sshd_config.d/10-p1bench.conf
  systemctl reload ssh || true
  echo "   SSH password login disabled (key found in $KEYS)"
else
  echo "!! no SSH key in $KEYS: password login left ON. Add a key (ssh-copy-id) and re-run."
fi

cat <<EOF

== Done. Reboot now:  sudo reboot
After the reboot, check:
  ls -l /dev/serial* $UART_DEV            # the UART exists
  grep console $CMDLINE                   # no console=serial0 left
  groups $USER_NAME | grep dialout
Then clone the bench repo into $BENCH/repo and run the loopback test (README.md, step 3).
EOF
