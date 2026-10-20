"""Play the golden vectors into a receiver, one telegram per interval, and log what was sent.

Serial (into John's Pi through a 3.3 V USB-serial adapter, TX -> GPIO15/RX, GND -> GND):
    python tools/inject_vectors.py --serial COM5            # Windows
    python tools/inject_vectors.py --serial /dev/ttyUSB0    # Linux
TCP (behaves like a network P1 dongle; Home Assistant DSMR 'network' mode connects to it):
    python tools/inject_vectors.py --tcp 2001

Options: --set valid|corrupt|all|<vector id>  (default all), --loop N (0 = forever, default 1),
         --interval seconds (default 1.0), --log sent.csv (default logs/sent_<UTC time>.csv)

The adapter sends normal (non-inverted) UART levels, so it goes straight to GPIO15, NOT
through the P1 front-end inverter. Use 3.3 V logic adapters only: Pi GPIOs are not 5 V tolerant.
Needs pyserial for --serial (pip install pyserial==3.5). Log columns: docs/LOG_FORMAT.md.
"""
import argparse
import csv
import json
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VEC = ROOT / "tests" / "vectors"
sys.path.insert(0, str(ROOT / "lib"))
from p1telegram import check_crc  # noqa: E402

LOG_COLUMNS = ["time_utc", "source", "seq", "seq_gap", "crc_ok", "raw_len", "telegram_time", "tariff",
               "imp_t1_kwh", "imp_t2_kwh", "exp_t1_kwh", "exp_t2_kwh", "imp_kw", "exp_kw",
               "v_l1", "i_l1", "v_l2", "i_l2", "v_l3", "i_l3", "vector_id", "error"]


def load_vectors(which):
    index = json.loads((VEC / "index.json").read_text(encoding="utf-8"))
    if which == "valid":
        index = [v for v in index if v["expect"] == "valid"]
    elif which == "corrupt":
        index = [v for v in index if v["expect"] == "reject"]
    elif which != "all":
        index = [v for v in index if v["id"] == which]
        if not index:
            sys.exit(f"no vector called {which}")
    return [(v, (VEC / v["file"]).read_bytes()) for v in index]


class SerialOut:
    def __init__(self, port):
        try:
            import serial
        except ImportError:
            sys.exit("pyserial is not installed: pip install pyserial==3.5")
        self.s = serial.Serial(port, 115200, bytesize=8, parity="N", stopbits=1, timeout=1)

    def send(self, data):
        self.s.write(data)
        self.s.flush()  # wait until the bytes have left, so telegrams never overlap

    def close(self):
        self.s.close()


class TcpOut:
    """Listens on a port and sends every telegram to all connected clients."""

    def __init__(self, port):
        self.clients, self.lock = [], threading.Lock()
        self.srv = socket.create_server(("0.0.0.0", port))
        threading.Thread(target=self._accept, daemon=True).start()
        print(f"listening on TCP port {port}; waiting for a client")
        while not self.clients:
            time.sleep(0.2)

    def _accept(self):
        while True:
            try:
                conn, addr = self.srv.accept()
            except OSError:  # server socket closed
                return
            print(f"client connected: {addr[0]}:{addr[1]}")
            with self.lock:
                self.clients.append(conn)

    def send(self, data):
        with self.lock:
            for c in list(self.clients):
                try:
                    c.sendall(data)
                except OSError:
                    self.clients.remove(c)

    def close(self):
        with self.lock:
            for c in self.clients:  # end the stream cleanly so clients see EOF, not a reset
                try:
                    c.shutdown(socket.SHUT_WR)
                    c.close()
                except OSError:
                    pass
        self.srv.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    out = ap.add_mutually_exclusive_group(required=True)
    out.add_argument("--serial", help="serial port, e.g. COM5 or /dev/ttyUSB0")
    out.add_argument("--tcp", type=int, help="TCP port to listen on, e.g. 2001")
    ap.add_argument("--set", default="all")
    ap.add_argument("--loop", type=int, default=1)
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--log")
    a = ap.parse_args()

    vectors = load_vectors(a.set)
    sink = SerialOut(a.serial) if a.serial else TcpOut(a.tcp)
    log_path = Path(a.log) if a.log else ROOT / "logs" / f"sent_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.csv"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    seq, rnd = 0, 0
    next_tick = time.monotonic()
    with open(log_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, LOG_COLUMNS)
        w.writeheader()
        try:
            while a.loop == 0 or rnd < a.loop:
                for v, raw in vectors:
                    time.sleep(max(0.0, next_tick - time.monotonic()))
                    next_tick += a.interval
                    sent_at = datetime.now(timezone.utc)
                    sink.send(raw)
                    seq += 1
                    row = {"time_utc": sent_at.isoformat(timespec="milliseconds"), "source": "injector",
                           "seq": seq, "seq_gap": 0, "crc_ok": int(check_crc(raw)), "raw_len": len(raw),
                           "vector_id": v["id"], "error": "" if v["expect"] == "valid" else "corrupt-by-design"}
                    row.update({k: ("" if val is None else val) for k, val in v.get("values", {}).items()})
                    w.writerow(row)
                    f.flush()
                    print(f"{seq:6d}  {v['id']:<28} {len(raw):4d} bytes  {'valid' if v['expect'] == 'valid' else 'CORRUPT'}")
                rnd += 1
        except KeyboardInterrupt:
            pass
        finally:
            sink.close()
    print(f"sent {seq} telegrams; log: {log_path}")


if __name__ == "__main__":
    main()
