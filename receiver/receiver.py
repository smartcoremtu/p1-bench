"""SmartCORE P1 reference receiver: reads DSMR P1 telegrams, checks them, logs every one.

This is the test instrument for the P1 emulator: it accepts every valid telegram, rejects every
corrupt one, and writes one CSV row per telegram or broken fragment (docs/LOG_FORMAT.md), plus
the raw bytes. Standard library only; pyserial is needed for --serial.

    python receiver/receiver.py                          # serial, settings from the environment
    python receiver/receiver.py --serial /dev/ttyAMA0    # Pi UART (GPIO15 = RX, header pin 10)
    python receiver/receiver.py --tcp 192.168.0.20:2001  # network P1 source (inject_vectors.py --tcp)
    python receiver/receiver.py --file capture.raw       # replay bytes from a file ('-' = stdin)

Environment (set by receiver/setup_receiver.sh in /opt/p1bench/receiver.env, used when the
matching option is not given): P1_SERIAL_DEVICE, P1_BAUD, P1_LOG_DIR, P1_SOURCE.

How a byte stream becomes rows (the "framer"):
  '/'            starts a telegram. A '/' arriving before the current one ended closes the current
                 one as error 'truncated'.
  '!' + 4 hex    is the CRC; the telegram ends at the line break after it.
  other bytes    outside a telegram start a fragment without a header: error 'no-header'.
                 Line breaks and NUL bytes between telegrams are ignored.
  silence        longer than --timeout (default 0.5 s) in the middle of a telegram closes it as 'timeout'.
                 A meter sends one telegram (~35 ms of data) per second and is then silent, so the gap
                 is the only boundary when a broken telegram is followed by one whose header is lost.
A complete telegram then gets its CRC checked ('crc' if wrong) and its lines parsed ('parse' if a
line or a required register is malformed or missing). Values are logged as far as they could be
read, even for broken rows, so the raw evidence and the numbers stay side by side.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import signal
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from p1telegram import check_crc  # noqa: E402

LOG_COLUMNS = ["time_utc", "source", "seq", "seq_gap", "crc_ok", "raw_len", "telegram_time", "tariff",
               "imp_t1_kwh", "imp_t2_kwh", "exp_t1_kwh", "exp_t2_kwh", "imp_kw", "exp_kw",
               "v_l1", "i_l1", "v_l2", "i_l2", "v_l3", "i_l3", "vector_id", "error"]

# OBIS code -> log column
OBIS_COLUMNS = {
    "0-0:1.0.0": "telegram_time", "0-0:96.14.0": "tariff",
    "1-0:1.8.1": "imp_t1_kwh", "1-0:1.8.2": "imp_t2_kwh",
    "1-0:2.8.1": "exp_t1_kwh", "1-0:2.8.2": "exp_t2_kwh",
    "1-0:1.7.0": "imp_kw", "1-0:2.7.0": "exp_kw",
    "1-0:32.7.0": "v_l1", "1-0:31.7.0": "i_l1",
    "1-0:52.7.0": "v_l2", "1-0:51.7.0": "i_l2",
    "1-0:72.7.0": "v_l3", "1-0:71.7.0": "i_l3",
}
# A telegram without these is a 'parse' error (DSMR 5 mandatory electricity registers)
REQUIRED = ["0-0:1.0.0", "1-0:1.8.1", "1-0:1.8.2", "1-0:2.8.1", "1-0:2.8.2", "0-0:96.14.0", "1-0:1.7.0", "1-0:2.7.0"]

MAX_FRAME = 4096        # bytes; DSMR 5 telegrams are well under 2 kB
OBIS_LINE = re.compile(r"^(\d+-\d+:\d+\.\d+\.\d+)((?:\([^()]*\))+)$")
NUMBER = re.compile(r"^(\d+(?:\.\d+)?)(?:\*([A-Za-z0-9]+))?$")


# ---- framing ---------------------------------------------------------------------------------
class Frame:
    """One telegram or broken fragment as cut from the byte stream."""

    def __init__(self, raw: bytes, has_header: bool, reason: str, closed_at: datetime):
        self.raw = raw                # exact bytes received
        self.has_header = has_header  # started with '/'
        self.reason = reason          # 'complete', 'truncated' or 'timeout'
        self.closed_at = closed_at    # UTC time the fragment ended


class Framer:
    """Cuts a byte stream into Frames. Feed it bytes as they arrive; call tick() when idle."""
    IDLE, BODY, CRC = range(3)

    def __init__(self, timeout: float = 0.5):
        self.timeout = timeout
        self.reset()

    def reset(self):
        self.state = self.IDLE
        self.buf = bytearray()
        self.has_header = False
        self.last_byte = 0.0          # monotonic time of the last byte

    def feed(self, data: bytes, now: datetime, mono: float) -> list[Frame]:
        out = []
        for b in data:
            c = bytes((b,))
            if self.state == self.IDLE:
                if c == b"/":
                    self._start(True, c)
                elif c not in b"\r\n\x00 ":
                    self._start(False, c)          # data without a header line
                    if c == b"!":
                        self.state = self.CRC
            elif self.state == self.BODY:
                if c == b"/":                      # next telegram began before this one ended
                    out.append(self._close("truncated", now))
                    self._start(True, c)
                else:
                    self.buf += c
                    if c == b"!":
                        self.state = self.CRC
                    elif len(self.buf) > MAX_FRAME:
                        out.append(self._close("truncated", now))
            else:  # CRC: collecting '!XXXX' up to the line break
                if c == b"/":
                    out.append(self._close("complete", now))
                    self._start(True, c)
                else:
                    self.buf += c
                    if c == b"\n" or len(self.buf) > MAX_FRAME:
                        out.append(self._close("complete", now))
        if data:
            self.last_byte = mono
        return out

    def tick(self, now: datetime, mono: float) -> list[Frame]:
        """Close a fragment the line has gone quiet on."""
        if self.state != self.IDLE and mono - self.last_byte > self.timeout:
            # CRC already arrived and only the line break is missing: still a whole telegram
            return [self._close("complete" if self.state == self.CRC else "timeout", now)]
        return []

    def flush(self, now: datetime) -> list[Frame]:
        """End of input: close whatever is open."""
        if self.state == self.IDLE:
            return []
        return [self._close("complete" if self.state == self.CRC else "timeout", now)]

    def _start(self, has_header: bool, first: bytes):
        self.state = self.BODY
        self.has_header = has_header
        self.buf = bytearray(first)

    def _close(self, reason: str, now: datetime) -> Frame:
        f = Frame(bytes(self.buf), self.has_header, reason, now)
        self.state, self.buf = self.IDLE, bytearray()
        return f


# ---- checking and parsing --------------------------------------------------------------------
def _number(text: str):
    """'001234.567*kWh' -> 1234.567, '002*A' -> 2, '0002' -> 2. None if not a number."""
    m = NUMBER.match(text)
    if not m:
        return None
    n = m.group(1)
    return float(n) if "." in n else int(n)


def parse_values(raw: bytes) -> tuple[dict, list[str]]:
    """Read the logged registers from a telegram. Returns (values, problems)."""
    values, problems = {}, []
    text = raw.decode("ascii", errors="replace")
    if "\r\n" not in text and "\n" in text:
        problems.append("line endings are LF, DSMR needs CRLF")
    seen = set()
    for line in re.split(r"\r?\n", text):
        line = line.strip("\r")
        if not line or line.startswith("/") or line.startswith("!"):
            continue
        m = OBIS_LINE.match(line)
        if not m:
            problems.append(f"unreadable line: {line[:40]!r}")
            continue
        code, groups = m.group(1), m.group(2)
        seen.add(code)
        col = OBIS_COLUMNS.get(code)
        if col is None:
            continue                     # a register we do not log (equipment id, gas, ...)
        inner = groups[1:-1].split(")(")[-1]
        if col == "telegram_time":
            if re.fullmatch(r"\d{12}[SW]", inner):
                values[col] = inner
            else:
                problems.append(f"bad timestamp {inner!r}")
        else:
            v = _number(inner)
            if v is None:
                problems.append(f"bad value {code}({inner})")
            else:
                values[col] = v
    for code in REQUIRED:
        if code not in seen:
            problems.append(f"missing {code}")
    return values, problems


def check_frame(frame: Frame) -> tuple[dict, str, list[str]]:
    """Returns (values, error, notes); error '' means a good telegram."""
    values, problems = parse_values(frame.raw)
    if not frame.has_header:
        return values, "no-header", problems
    if frame.reason != "complete":
        return values, frame.reason, problems
    if not check_crc(frame.raw):
        return values, "crc", problems
    if problems:
        return values, "parse", problems
    return values, "", []


# ---- logging ---------------------------------------------------------------------------------
class RecvLog:
    """recv_<UTC>.csv + recv_<UTC>.raw, new pair at 00:00 UTC. seq restarts per file."""

    def __init__(self, log_dir: Path, source: str):
        self.dir, self.source = log_dir, source
        self.dir.mkdir(parents=True, exist_ok=True)
        self.day = None
        self.csv_f = self.raw_f = self.writer = None
        self.seq = 0
        self.prev_good = None
        self.paths = []

    def _open(self, now: datetime):
        self.close()
        stamp = f"{now:%Y%m%dT%H%M%SZ}"
        csv_path, raw_path = self.dir / f"recv_{stamp}.csv", self.dir / f"recv_{stamp}.raw"
        self.csv_f = open(csv_path, "w", newline="", encoding="utf-8")
        self.raw_f = open(raw_path, "wb")
        self.writer = csv.DictWriter(self.csv_f, LOG_COLUMNS, lineterminator="\n")
        self.writer.writeheader()
        self.csv_f.flush()
        self.day, self.seq = now.date(), 0
        self.paths.append(csv_path)

    def write(self, frame: Frame, values: dict, error: str) -> dict:
        now = frame.closed_at
        if self.day != now.date():
            self._open(now)
        self.seq += 1
        gap = 0
        if not error:
            if self.prev_good is not None:
                gap = max(0, round((now - self.prev_good).total_seconds()) - 1)
            self.prev_good = now
        row = {"time_utc": now.isoformat(timespec="milliseconds"), "source": self.source,
               "seq": self.seq, "seq_gap": gap, "crc_ok": int(frame.has_header and check_crc(frame.raw)),
               "raw_len": len(frame.raw), "vector_id": "", "error": error}
        row.update(values)
        self.writer.writerow(row)
        self.csv_f.flush()
        self.raw_f.write(f"#--- {row['time_utc']} {self.seq}\n".encode("ascii"))
        self.raw_f.write(frame.raw if frame.raw.endswith(b"\n") else frame.raw + b"\n")
        self.raw_f.flush()
        return row

    def close(self):
        for f in (self.csv_f, self.raw_f):
            if f:
                f.close()
        self.csv_f = self.raw_f = None


# ---- inputs ----------------------------------------------------------------------------------
class SerialIn:
    def __init__(self, device: str, baud: int):
        try:
            import serial
        except ImportError:
            sys.exit("pyserial is not installed: pip install pyserial==3.5")
        self.serial, self.device, self.baud, self.s = serial, device, baud, None
        self.name = f"serial {device} {baud} 8N1"

    def read(self) -> bytes | None:
        if self.s is None:
            try:
                self.s = self.serial.Serial(self.device, self.baud, bytesize=8, parity="N", stopbits=1, timeout=0.1)
                print(f"opened {self.name}", flush=True)
            except self.serial.SerialException as e:
                print(f"cannot open {self.device}: {e}; retrying in 5 s", flush=True)
                time.sleep(5)
                return b""
        try:
            return self.s.read(self.s.in_waiting or 1)
        except self.serial.SerialException as e:   # e.g. USB adapter unplugged
            print(f"serial error: {e}; reopening", flush=True)
            self.s.close()
            self.s = None
            return b""

    def close(self):
        if self.s:
            self.s.close()


class TcpIn:
    """Client of a network P1 source. Reconnects unless exit_on_eof."""

    def __init__(self, target: str, exit_on_eof: bool):
        host, _, port = target.rpartition(":")
        self.addr, self.exit_on_eof, self.sock, self.connected_once = (host or "127.0.0.1", int(port)), exit_on_eof, None, False
        self.name = f"tcp {self.addr[0]}:{self.addr[1]}"

    def read(self) -> bytes | None:
        if self.sock is None:
            if self.connected_once and self.exit_on_eof:
                return None
            try:
                self.sock = socket.create_connection(self.addr, timeout=3)
                self.sock.settimeout(0.1)
                self.connected_once = True
                print(f"connected to {self.name}", flush=True)
            except OSError as e:
                print(f"cannot connect to {self.name}: {e}; retrying in 2 s", flush=True)
                time.sleep(2)
                return b""
        try:
            data = self.sock.recv(4096)
        except socket.timeout:
            return b""
        except OSError as e:
            data = b""
            print(f"connection error: {e}", flush=True)
        else:
            if data:
                return data
            print("source closed the connection", flush=True)
        self.sock.close()
        self.sock = None
        return None if self.exit_on_eof else b""

    def close(self):
        if self.sock:
            self.sock.close()


class FileIn:
    def __init__(self, path: str):
        self.f = sys.stdin.buffer if path == "-" else open(path, "rb")
        self.name = f"file {path}"

    def read(self) -> bytes | None:
        return self.f.read(4096) or None

    def close(self):
        if self.f is not sys.stdin.buffer:
            self.f.close()


# ---- main ------------------------------------------------------------------------------------
def describe(row: dict) -> str:
    if row["error"]:
        return f"{row['seq']:6d}  ERROR {row['error']:<9} {row['raw_len']:4d} bytes"
    return (f"{row['seq']:6d}  ok    {row.get('telegram_time', ''):<13}  import {row.get('imp_kw', '')} kW"
            f"  export {row.get('exp_kw', '')} kW  gap {row['seq_gap']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--serial", metavar="DEVICE", help="serial device (default: $P1_SERIAL_DEVICE or /dev/ttyAMA0)")
    src.add_argument("--tcp", metavar="HOST:PORT", help="connect to a network P1 source")
    src.add_argument("--file", metavar="PATH", help="read bytes from a file, '-' = stdin")
    ap.add_argument("--baud", type=int, default=int(os.environ.get("P1_BAUD", 115200)))
    ap.add_argument("--log-dir", default=os.environ.get("P1_LOG_DIR", str(ROOT / "logs")))
    ap.add_argument("--source-name", default=os.environ.get("P1_SOURCE", "receiver-ref"),
                    help="value of the 'source' column (default receiver-ref)")
    ap.add_argument("--timeout", type=float, default=0.5,
                    help="seconds of silence that end a telegram (default 0.5; keep it below the send interval)")
    ap.add_argument("--exit-on-eof", action="store_true", help="TCP: stop when the source disconnects")
    ap.add_argument("--quiet", action="store_true", help="print only errors and an hourly summary")
    a = ap.parse_args(argv)

    if a.tcp:
        inp = TcpIn(a.tcp, a.exit_on_eof)
    elif a.file:
        inp = FileIn(a.file)
    else:
        inp = SerialIn(a.serial or os.environ.get("P1_SERIAL_DEVICE", "/dev/ttyAMA0"), a.baud)

    # systemd stops the service with SIGTERM: finish the current row and close the files cleanly
    def on_sigterm(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_sigterm)

    framer, log = Framer(a.timeout), RecvLog(Path(a.log_dir), a.source_name)
    counts: dict[str, int] = {}
    next_summary = time.monotonic() + 3600
    print(f"P1 receiver: {inp.name}, logs in {log.dir}", flush=True)

    def handle(frames):
        for fr in frames:
            values, error, notes = check_frame(fr)
            row = log.write(fr, values, error)
            counts[error or "ok"] = counts.get(error or "ok", 0) + 1
            if error or not a.quiet:
                print(describe(row) + (f"  ({'; '.join(notes[:3])})" if notes else ""), flush=True)

    try:
        while True:
            data = inp.read()
            now, mono = datetime.now(timezone.utc), time.monotonic()
            if data is None:                       # end of input
                handle(framer.flush(now))
                break
            handle(framer.feed(data, now, mono) if data else framer.tick(now, mono))
            if a.quiet and mono >= next_summary:
                print(f"last hour: {counts}", flush=True)
                next_summary = mono + 3600
    except KeyboardInterrupt:
        handle(framer.flush(datetime.now(timezone.utc)))
    finally:
        inp.close()
        log.close()
    total = sum(counts.values())
    print(f"stopped: {total} rows {counts}; logs: {', '.join(str(p) for p in log.paths) or 'none'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
