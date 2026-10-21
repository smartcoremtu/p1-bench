# Using the receiver Pi to check your emulator (student guide)

You have the **reference receiver**: a Raspberry Pi 5 that listens to P1 telegrams and tells you,
for every single one, whether it arrived intact. Use it to check your emulator's output yourself.
It is a test instrument, so please do not change its setup or code. If something looks wrong
with the receiver itself, write down what you saw and tell Nash.

What a telegram must look like: DSMR 5.0.2, and byte-for-byte what `lib/p1telegram.py` builds
(the golden vectors in `tests/vectors/` are examples). Log format for your emulator's own send log: `docs/LOG_FORMAT.md`.

## 1. Power and connecting

- Power it with **the supplied 27 W USB-C supply only**. Wait about 2 minutes after switching on.
- **Switch off properly** (or the SD card can be corrupted): `sudo poweroff`, wait for the green LED to stop, then unplug.

**Network.** The Pi joins Nash's network if it is in range. Anywhere else, after about 60 seconds
it makes its own WiFi:

| WiFi name | `p1-receiver` |
|---|---|
| Password | on the handover sheet |
| Pi address | `10.42.0.1` (also `p1-receiver.local`) |

Connect your laptop to that WiFi (your laptop has no internet while connected), then:
```
ssh p1@p1-receiver.local          # or: ssh p1@10.42.0.1
```
To put the Pi on your home WiFi instead (then your laptop keeps its internet): while logged in over the hotspot,
```
sudo nmcli dev wifi connect "<your WiFi name>" password "<your WiFi password>"
```
The SSH session drops because the Pi leaves its hotspot. Reconnect your laptop to your home WiFi and
`ssh p1@p1-receiver.local` again. From then on it joins your WiFi automatically.
(Home WiFi only. University WiFi such as eduroam will not work.)

## 2. Check the receiver first (2 minutes, every session)

```
cd /opt/p1bench/repo
/opt/p1bench/.venv/bin/python tests/test_receiver.py     # must end with 104/104 passed
bash tools/serial_test.sh internal                       # must end with PASS
```
If either fails, the receiver is at fault, not your emulator: stop and tell Nash.

## 3. Check your emulator

Your emulator should write its own send log (`sent_<UTC>.csv`, `docs/LOG_FORMAT.md`). Then
`tools/compare_logs.py` gives a PASS/FAIL for the whole run. Send at the real rate: **one telegram
per second**. The receiver splits telegrams on the gap between them, so do not send faster than about one every 0.6 s.

### A. Emulator software on your laptop (TCP), no wiring

Make your emulator a TCP server on port 2001 that sends a telegram every second to whoever connects
(this is what network P1 dongles do; `tools/inject_vectors.py --tcp 2001` is a working example).
Start it, then in a second terminal on your laptop:
```
ssh -R 2001:localhost:2001 p1@p1-receiver.local
cd /opt/p1bench/repo
/opt/p1bench/.venv/bin/python receiver/receiver.py --tcp 127.0.0.1:2001 --log-dir ~/runs
```
`-R` carries the Pi's port 2001 back to your laptop through SSH, so no firewall or IP addresses are involved.
Every telegram shows on screen; stop with Ctrl+C. The logs are in `~/runs/` on the Pi.

### B. Emulator on your own Pi, over a serial wire (3.3 V UART)

Three jumper wires between **your** Pi and the receiver, both switched off while you wire:

| Your Pi | Receiver Pi |
|---|---|
| pin 8 (GPIO14, TX) | pin 10 (GPIO15, RX) |
| pin 6 (GND) | pin 6 (GND) |

Pin 1 is the corner pin nearest the SD card slot; even pin numbers are on the outer row.
Your UART must be 115200 baud, 8 data bits, no parity, 1 stop bit, **normal (not inverted)** levels.
On the receiver:
```
cd /opt/p1bench/repo
bash tools/serial_test.sh listen          # start your emulator, Ctrl+C when it has finished
```
**Do not** connect your optocoupler/RJ12 output to the receiver yet. Real P1 output is inverted and
open-collector, and the receiver's matching front-end circuit is not built yet. Never connect anything
5 V (such as RJ12 pin 1) to a Pi pin: Pi pins are 3.3 V only and are damaged by 5 V.

### C. No Pi needed

The receiver is plain Python and also runs on your laptop (`pip install pyserial==3.5`, and `tzdata` on
Windows): `python receiver/receiver.py --tcp 127.0.0.1:2001`. Handy for quick checks; use the Pi for the record.

### Comparing

Copy your send log to the Pi (from your laptop: `scp sent_XXXX.csv p1@p1-receiver.local:~/runs/`), then on the Pi:
```
/opt/p1bench/.venv/bin/python tools/compare_logs.py ~/runs/sent_XXXX.csv ~/runs/recv_YYYY.csv
```
Copy results back to your laptop: `scp -r p1@p1-receiver.local:~/runs .`
No send log yet? Then at least every row in the `recv_*.csv` must have an empty `error` column and `crc_ok` = 1.

## 4. Reading the results

| `error` | Meaning | Usually means in your emulator |
|---|---|---|
| (empty) | good telegram | |
| `crc` | complete telegram, CRC wrong | CRC computed over the wrong bytes (it covers `/` up to and including `!`), lower-case hex, or bytes changed after the CRC |
| `parse` | CRC fine, but a line or register is wrong | the receiver prints which line: number format, missing register, LF instead of CRLF |
| `no-header` | data not starting with `/` | the first row after you start is often this (receiver joined mid-telegram); later ones mean lost bytes |
| `truncated` / `timeout` | telegram stopped before `!XXXX` | output cut off, buffer not flushed, sending too fast |

The `recv_*.raw` file next to each CSV holds the exact bytes of every row. Compare it with what you meant to send.
Full details: `docs/RECEIVER.md` §4 and `docs/LOG_FORMAT.md`.

## 5. Rules

- No real household or meter data on either Pi: synthetic data and the golden vectors only.
- Never connect the emulator or the receiver to a real electricity meter.
- Don't change the receiver's code or setup (`/opt/p1bench`); your own files go in `/home/p1/runs` or elsewhere in `/home/p1`.
- Bring it back with its power supply, and tell Nash about anything odd, even if you fixed it.
