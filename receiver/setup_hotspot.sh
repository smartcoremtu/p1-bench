#!/usr/bin/env bash
# Fallback WiFi hotspot for the receiver Pi, so it can be reached anywhere without a known network.
#
#   sudo bash receiver/setup_hotspot.sh          (safe to run again; keeps the existing passphrase)
#
# At boot, p1-wifi-fallback.service waits up to 60 s for WiFi or Ethernet. If neither connects, it
# starts the hotspot: SSID "p1-receiver", WPA2 (never open), Pi = 10.42.0.1, DHCP for the laptop.
# Where a known network exists the Pi joins it as usual and the hotspot stays off.
# The passphrase is generated once and kept in /opt/p1bench/hotspot.env (root only).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }
BENCH=/opt/p1bench
ENV="$BENCH/hotspot.env"
SSID="${HOTSPOT_SSID:-p1-receiver}"

export DEBIAN_FRONTEND=noninteractive
command -v dnsmasq >/dev/null || apt-get install -y -qq dnsmasq-base   # NM 'shared' mode needs it

if [[ ! -s $ENV ]]; then
  PSK=$(head -c 512 /dev/urandom | tr -dc 'a-km-z2-9' | cut -c1-12)   # no l/1/0/o: easy to read off paper
  printf 'HOTSPOT_SSID=%s\nHOTSPOT_PSK=%s\n' "$SSID" "$PSK" >"$ENV"
  chmod 600 "$ENV"
fi
source "$ENV"

nmcli con delete p1-hotspot >/dev/null 2>&1 || true
nmcli con add type wifi ifname wlan0 con-name p1-hotspot autoconnect no ssid "$HOTSPOT_SSID" \
  802-11-wireless.mode ap 802-11-wireless.band bg ipv4.method shared ipv6.method disabled \
  wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp \
  wifi-sec.psk "$HOTSPOT_PSK" >/dev/null

cat >/usr/local/sbin/p1-wifi-fallback <<'EOF'
#!/bin/sh
# Start the p1-hotspot only if no WiFi or Ethernet connection comes up within 60 s of boot.
for i in $(seq 1 60); do
  nmcli -t -f DEVICE,STATE dev | grep -Eq '^(wlan0|eth0):connected$' && exit 0
  sleep 1
done
echo "no network after 60 s: starting hotspot"
exec nmcli con up p1-hotspot
EOF
chmod 755 /usr/local/sbin/p1-wifi-fallback

cat >/etc/systemd/system/p1-wifi-fallback.service <<'EOF'
[Unit]
Description=P1 bench: WiFi hotspot when no known network is found
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/p1-wifi-fallback

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable p1-wifi-fallback.service >/dev/null
echo "hotspot ready: SSID $HOTSPOT_SSID, passphrase in $ENV, Pi address 10.42.0.1 when active"
