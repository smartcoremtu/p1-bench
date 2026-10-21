# Receiver Pi: from blank SD card to SSH

Do this once per Pi. Takes about 20 minutes. After step 6 everything else is done over SSH
(`receiver/setup_receiver.sh`, see README.md §2).

## What you need

| Item | Notes |
|---|---|
| Raspberry Pi 4 or 5 | Pi 5 preferred (same as the HEMS fleet) |
| Official power supply | Pi 5: 27 W USB-C (5 V 5 A). Pi 4: 15 W USB-C (5 V 3 A). Phone chargers cause undervoltage and flaky serial |
| microSD card, 16 GB or more | Class A1/A2. Everything on it will be erased |
| SD card reader on the laptop | |
| Network the laptop is also on | Ethernet cable to a home router is easiest. WiFi works if it is WPA2/WPA3 Personal (a password, no username). **eduroam / MTU staff WiFi will not work** (enterprise login, and client isolation stops laptop↔Pi traffic). A phone hotspot works as a fallback |
| *Later, for the loopback test (T3b)* | A **3.3 V** USB-serial adapter (FTDI FT232RL or CP2102 with the jumper set to 3.3 V) and 3 female-female jumper wires. A 5 V adapter can damage the Pi's GPIO |
| *Later, for real P1 input* | Front-end parts in `03 Receiver/P1_Receiver_Pi_Build_Notes_2026-10-19.docx` (BC547/2N3904, 3 × 10 k, 1 k, RJ12 breakout) |

## Steps

1. **Install Raspberry Pi Imager** on the laptop: https://www.raspberrypi.com/software/ (version 1.8 or later).

2. **Choose what to write.** Insert the SD card, open Imager:
   - *Raspberry Pi Device*: your model (Pi 5 or Pi 4)
   - *Operating System*: Raspberry Pi OS (other) → **Raspberry Pi OS Lite (64-bit)** (no desktop)
   - *Storage*: the SD card. Check the size matches the card you just inserted. Do not pick any
     other card, especially a balenaOS HEMS card (drive label `resin-boot`)

3. **Next → Edit settings** (OS customisation):
   - *General* tab
     - Hostname: `p1-receiver`
     - Username: `p1`, and a password you will keep (needed for `sudo`)
     - Wireless LAN: only if not using Ethernet. Your SSID + password, country `IE`
     - Locale: time zone `Europe/Dublin`, keyboard `ie` (or `gb`)
   - *Services* tab
     - Tick **Enable SSH** → **Allow public-key authentication only**
     - Paste this key (one line; it is the bench key made on Nash's laptop, `~/.ssh/p1bench_ed25519.pub`):
       ```
       ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMnatahkBnPczZA2gGoa/2A/wnjmV4Qs9ZfrB3pMz684 nash-laptop-p1bench
       ```
   - *Options* tab: untick telemetry if you like
   - Save → **Yes** to apply the settings → Yes to erase the card. Wait for write + verify.

4. **Boot.** Eject the card, put it in the Pi, connect Ethernet (if used), then power.
   First boot resizes the card and reboots once: give it 2–3 minutes.

5. **Check from the laptop** (in Claude Code, type the line with the leading `!`):
   ```
   ! ssh p1-receiver "hostname; cat /proc/device-tree/model; echo; uname -m; ip -4 -br addr"
   ```
   Expected: `p1-receiver`, the Pi model, `aarch64`, and an IP address.
   The laptop's `~/.ssh/config` already maps `p1-receiver` to user `p1`, the bench key and `p1-receiver.local`.
   - `Could not resolve hostname`: find the Pi's IP on the router's device list (or `ping p1-receiver.local`)
     and run `! ssh p1@<IP> -i ~/.ssh/p1bench_ed25519 hostname`.
   - `Permission denied (publickey)`: the key was not pasted, or the username is not `p1`. Re-flash.
   - No reply at all: wait another minute; check the green LED flickers; check the Pi is on the same network.

6. **Hand over** to Claude: the Pi model, the IP if `.local` did not work, and whether a 3.3 V USB-serial adapter is available.
   Claude then runs `receiver/setup_receiver.sh` over SSH. It asks for the `p1` password only through `sudo`,
   so either type it when asked or allow passwordless sudo for `p1` (Raspberry Pi OS does this by default).
