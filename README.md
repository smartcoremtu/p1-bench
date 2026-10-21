# SmartCORE P1 bench

Test bench for the P1 port emulator project (MTU, SmartCORE WP2). Baseline format: DSMR 5.0.2.
Background and requirements: `../02 Emulator audit/P1_Emulator_Workflow_Audit_and_Requirements_2026-10-18.docx`;
receiver background notes: `../03 Receiver/P1_Receiver_Pi_Build_Notes_2026-10-19.docx`.

```
lib/p1telegram.py          reference telegram builder + CRC16 (the contract, work item N2)
tests/make_vectors.py      regenerates the golden vectors (only when the reference changes)
tests/vectors/             16 valid + 6 corrupt telegrams, index.json = expected outcome and values
tests/test_vectors.py      checks the vectors with the reference CRC and dsmr_parser
tools/inject_vectors.py    plays vectors into a receiver over serial or TCP, writes a sent log
receiver/setup_receiver.sh prepares John's Raspberry Pi (UART, venv, service unit)
receiver/receiver.py       reference receiver (UART, TCP or file in; frames, checks CRC, logs per docs/LOG_FORMAT.md)
tools/compare_logs.py      sent log vs received log -> PASS/FAIL
tools/serial_test.sh       serial bench tests on the Pi (self-loopback, or listen for another sender)
tests/test_receiver.py     receiver self-check (104 checks, incl. a TCP run with the injector)
docs/RECEIVER.md           operating guide for the receiver Pi (John)
docs/STUDENT_BENCH.md      checking your emulator with the receiver Pi (student)
receiver/setup_hotspot.sh  WPA2 fallback hotspot 'p1-receiver' when no known WiFi is around
docs/PI_SETUP.md           blank SD card -> Pi reachable over SSH
docs/LOG_FORMAT.md         CSV columns shared by every sender and receiver
```

## 1. Check the vectors (any PC)

```
python -m venv .venv && .venv/bin/pip install pyserial==3.5 dsmr_parser==1.11.2   # Windows: .venv\Scripts\pip
python tests/test_vectors.py        # expect 22/22 passed
```
Windows has no system time-zone database: also `pip install tzdata`, or `ZoneInfo("Europe/Dublin")` fails on import.

## 2. Prepare the receiver Pi (Raspberry Pi OS Lite 64-bit, Pi 4 or 5)

Flash and first boot: `docs/PI_SETUP.md`. Then:

```
ssh-copy-id <user>@<pi>              # first, so the script can turn off password login
scp -r p1-bench <user>@<pi>:~/
ssh <user>@<pi>
sudo bash ~/p1-bench/receiver/setup_receiver.sh p1-receiver
sudo reboot
sudo mv ~/p1-bench /opt/p1bench/repo # the service expects the repo here
```
Or clone from GitHub (how the bench Pi is set up; update later with `git pull`):
```
sudo git clone https://github.com/smartcoremtu/p1-bench.git /opt/p1bench/repo && sudo chown -R <user>: /opt/p1bench/repo
```

## 3. Loopback test (T3b): laptop → USB-serial adapter → Pi UART

Wiring (3.3 V adapter only, normal polarity, NOT through the P1 inverter):
adapter TX → Pi GPIO15 (header pin 10), adapter GND → Pi GND (pin 6). Leave adapter RX and VCC unconnected.

```
python tools/inject_vectors.py --serial COM5 --loop 10       # on the laptop
```
On the Pi run `bash tools/serial_test.sh listen` first; then compare the two logs with `tools/compare_logs.py`
(PASS = all 32 valid telegrams intact, all 12 corrupt ones rejected for `--loop 2`). Without an adapter,
`bash tools/serial_test.sh self` with a jumper from header pin 8 to pin 10 tests the Pi's own UART.
Quick look without a receiver: `cat /dev/ttyAMA0` after `stty -F /dev/ttyAMA0 115200 raw`.

## 4. TCP mode (behaves like a network dongle)

```
python tools/inject_vectors.py --tcp 2001 --set valid --loop 0
```
Home Assistant → Add integration → DSMR Smart Meter → Network, host = this PC, port 2001, version 5.

## Rules
- Vectors are the contract: never edit them by hand. Change `lib/` → run `make_vectors.py` → run the tests → commit both.
- No household data in this repo. The equipment id is fake (`SIMULATOR0001`).
- `*.sh` must keep LF line endings (`.gitattributes`); run it with `bash`, so the exec bit does not matter.
