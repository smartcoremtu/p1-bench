# The reference receiver: guide for John

The receiver Pi is the **test instrument** for the P1 emulator. When the student's emulator sends
telegrams, this Pi says, for every single one, whether it arrived intact, and keeps the bytes as
evidence. If the instrument is wrong, every test result is wrong, so it is tested itself
(`tests/test_receiver.py`, 104 checks) and checked against the golden vectors before every session.

Background on UARTs, inversion, framing and CRC: `../03 Receiver/P1_Receiver_Pi_Build_Notes_2026-10-19.docx`.
Log columns: `docs/LOG_FORMAT.md`.

## 1. The Pi

| | |
|---|---|
| Hardware | Raspberry Pi 5, Raspberry Pi OS Lite 64-bit (Debian 13 trixie) |
| Name / login | `p1-receiver.local`, user `p1`, SSH keys only (no password login). Ask Nash to add your public key |
| Serial input | `/dev/ttyAMA0` = GPIO15 (RX) on header **pin 10**. GPIO14 (TX) = pin 8. GND = pin 6 |
| Settings | `/opt/p1bench/receiver.env` (device, baud, log folder) |
| Code | `/opt/p1bench/repo` (git checkout of `p1-bench`), Python venv `/opt/p1bench/.venv` |
| Logs | `/opt/p1bench/logs/recv_<UTC>.csv` + `.raw`, a new pair every day at 00:00 UTC |
| Service | `p1-receiver` (systemd): runs the receiver on the UART, restarts it if it crashes |

Rebuilding the Pi from scratch: `docs/PI_SETUP.md`, then `receiver/setup_receiver.sh` (README.md §2).

## 2. Everyday commands (on the Pi)

```
sudo systemctl start p1-receiver        # start receiving (enable instead of start = also at boot)
sudo systemctl stop p1-receiver
systemctl status p1-receiver            # running? last lines of output
journalctl -u p1-receiver -f            # live: errors as they happen + an hourly summary
tail -f /opt/p1bench/logs/recv_*.csv    # live: every row
```
Run it by hand to see every telegram on screen (stop the service first: two programs reading one
port steal bytes from each other):
```
cd /opt/p1bench/repo
/opt/p1bench/.venv/bin/python receiver/receiver.py              # Ctrl+C to stop
```
Other inputs: `--tcp HOST:PORT` (a network P1 source, e.g. `inject_vectors.py --tcp 2001` on a laptop),
`--file capture.raw` (replay saved bytes). `--help` lists all options.

## 3. Bench tests, in this order

Each test ends with `tools/compare_logs.py`, which prints **PASS** only if every valid telegram
arrived with the right values and every corrupt one was rejected.

| # | Test | Wiring | Command (on the Pi, in `/opt/p1bench/repo`) |
|---|---|---|---|
| 0 | Software self-check | none | `/opt/p1bench/.venv/bin/python tests/test_receiver.py` → `104/104 passed` |
| 1 | UART self-loopback: proves the Pi's UART and the receiver | one jumper wire, **pin 8 ↔ pin 10** | `bash tools/serial_test.sh self` |
| 2 | T3b: laptop → 3.3 V USB-serial adapter → Pi | adapter TX → pin 10, adapter GND → pin 6. Nothing else | Pi: `bash tools/serial_test.sh listen`; laptop: `python tools/inject_vectors.py --serial COM5 --loop 2` (README.md §3), then copy the laptop's `logs/sent_*.csv` to the Pi and run the printed `compare_logs.py` line |
| 3 | Real P1 levels: same as 2, but through the NPN inverter front end | see the build notes doc | as test 2 |
| 4 | The student's emulator | emulator RJ12 → front end → pin 10 | `serial_test.sh listen`; the emulator must write a `sent_*.csv` in the same format |
| 5 | Soak: 24 h at 1 telegram/s | as 4 | service running; compare the day's `sent` and `recv` files |

Never connect anything 5 V to a GPIO pin. P1 pin 5 goes only through the front end.

## 4. Reading the results

One row per telegram **or broken fragment**. `error` empty and `crc_ok` = 1 means a good telegram.

| `error` | Meaning | Usual cause |
|---|---|---|
| `crc` | Whole telegram arrived, CRC does not match | a flipped bit (noise, wrong baud, wrong polarity), or the sender computes the CRC wrongly |
| `truncated` | A new `/` (or 4 kB of data) arrived before the `!XXXX` | sender stopped half-way and restarted |
| `timeout` | Line went quiet for 0.5 s before the `!XXXX` | sender stopped half-way, cable pulled |
| `no-header` | Data that did not start with `/` | receiver started mid-telegram (normal for the first row), lost first line |
| `parse` | CRC is right but a line, value or required register is wrong | sender formats a register wrongly: tell the student which (the receiver prints the reason) |

- `seq_gap` > 0 on a good row: that many seconds without a good telegram before it.
- The `.raw` file holds the exact bytes of every row (`#--- <time> <seq>` before each). When a value
  looks wrong, look there first.
- Values on error rows are filled in as far as they could be read: evidence only, never data.
- Lots of `crc` and garbage `no-header` rows on the real line: check polarity (P1 data is inverted),
  baud (115200 8N1) and that GND is connected, in that order.

## 5. Changing the code

The code lives in git (`p1-bench`). Nash's laptop is the master copy for now and deploys with
`git push pi receiver`; once the repo is on GitHub, clone it and send changes as pull requests.

```
receiver/receiver.py     Framer (bytes -> telegrams), check_frame (CRC, parse), RecvLog (CSV/raw), inputs
tools/compare_logs.py    sent vs received -> PASS/FAIL
tools/serial_test.sh     the serial tests above
tests/test_receiver.py   run after every change: must stay all-pass
```
Rules:
- The receiver must stay standard-library Python (pyserial only for serial).
- Changing what goes into the log means changing `docs/LOG_FORMAT.md` first (new version number) and
  telling the student, because the emulator writes the same columns.
- Never hand-edit `tests/vectors/`; never put household data on this Pi.
