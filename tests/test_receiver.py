"""Tests for the reference receiver (framing, checks, log files) and the sent/received comparison.

    python tests/test_receiver.py           # all tests, including a TCP run with the injector
    python tests/test_receiver.py --no-tcp  # skip the TCP run

Exit code 0 = all passed. Standard library only.
"""
import csv
import json
import random
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VEC = ROOT / "tests" / "vectors"
sys.path.insert(0, str(ROOT / "receiver"))
import receiver as rx  # noqa: E402

INDEX = json.loads((VEC / "index.json").read_text(encoding="utf-8"))
RAW = {v["id"]: (VEC / v["file"]).read_bytes() for v in INDEX}
# what the receiver must say about each corrupt vector when it is followed by another telegram
EXPECTED_ERROR = {"c01_digit_changed": "crc", "c02_wrong_crc": "crc", "c03_truncated": "truncated",
                  "c04_missing_header": "no-header", "c05_line_removed": "crc", "c06_lf_line_endings": "crc"}
T0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))


def run_stream(data: bytes, chunks=None, tail_flush=True):
    """Feed bytes through a Framer (in the given chunk sizes) and check every frame."""
    fr, out, pos, i = rx.Framer(), [], 0, 0
    while pos < len(data):
        n = len(data) - pos if chunks is None else chunks[i % len(chunks)]
        out += fr.feed(data[pos:pos + n], T0, 0.0)
        pos, i = pos + n, i + 1
    if tail_flush:
        out += fr.flush(T0)
    return [rx.check_frame(f) for f in out]


def values_match(got, want):
    for col, w in want.items():
        if w is None:
            if col in got:
                return f"{col} present but should be empty"
        elif col not in got or (got[col] != w if isinstance(w, str) else abs(got[col] - w) > 1e-9):
            return f"{col}: got {got.get(col)!r}, want {w!r}"
    return ""


def test_each_vector():
    for v in INDEX:
        # follow every vector with v01 so a truncated one is closed by the next '/', as on a live line
        rows = run_stream(RAW[v["id"]] + RAW["v01_winter_day_import"])
        check(f"{v['id']}: two rows", len(rows) == 2, f"got {len(rows)}")
        values, error, notes = rows[0]
        if v["expect"] == "valid":
            check(f"{v['id']}: accepted", error == "", f"error {error!r} {notes}")
            bad = values_match(values, v["values"])
            check(f"{v['id']}: values equal index.json", not bad, bad)
        else:
            check(f"{v['id']}: rejected as {EXPECTED_ERROR[v['id']]}", error == EXPECTED_ERROR[v["id"]],
                  f"got {error!r}")
        check(f"{v['id']}: next telegram still good", rows[1][1] == "", f"got {rows[1][1]!r}")


def test_whole_sequence_any_chunking():
    """All vectors as a live line delivers them: one per second, silence in between."""
    want = ["" if v["expect"] == "valid" else EXPECTED_ERROR[v["id"]] for v in INDEX]
    # c03 is followed by c04, which has no '/': only the silence between them can end c03
    want = ["timeout" if v["id"] == "c03_truncated" else w for v, w in zip(INDEX, want)]
    rng = random.Random(1)
    for name, size in [("one block", None), ("byte by byte", lambda: 1), ("random chunks", lambda: rng.randint(1, 64))]:
        fr, frames = rx.Framer(timeout=0.5), []
        for k, v in enumerate(INDEX):
            data, mono = RAW[v["id"]], float(k)
            pos = 0
            while pos < len(data):
                n = len(data) if size is None else size()
                frames += fr.feed(data[pos:pos + n], T0, mono + pos / 11520)   # 115200 baud = 11.5 bytes/ms
                pos += n
            frames += fr.tick(T0, mono + 0.9)
        frames += fr.flush(T0)
        got = [rx.check_frame(f)[1] for f in frames]
        check(f"all {len(INDEX)} vectors at 1 per second, {name}: one row each, right verdicts", got == want,
              f"got {got}")
    merged = run_stream(RAW["c03_truncated"] + RAW["c04_missing_header"])
    check("documented limit: with no silence between them, c03 + c04 come out as one broken row",
          len(merged) == 1 and merged[0][1] != "")


def test_noise_and_idle():
    blank = run_stream(b"\r\n\x00\r\n" + RAW["v01_winter_day_import"] + b"\r\n\r\n")
    check("line breaks / NUL between telegrams are ignored", [e for _, e, _ in blank] == [""])
    junk = run_stream(b"\xff\xfegarbage" + RAW["v01_winter_day_import"])
    check("junk before a telegram -> one no-header row, then a good one", [e for _, e, _ in junk] == ["no-header", ""])

    fr = rx.Framer(timeout=0.5)
    fr.feed(RAW["c03_truncated"], T0, 100.0)
    check("quiet line mid-telegram: nothing before the timeout", fr.tick(T0, 100.4) == [])
    frames = fr.tick(T0, 100.6)
    check("quiet line mid-telegram: 'timeout' after it", len(frames) == 1 and rx.check_frame(frames[0])[1] == "timeout")

    fr = rx.Framer()
    no_crlf = RAW["v01_winter_day_import"].rstrip(b"\r\n")
    fr.feed(no_crlf, T0, 100.0)
    frames = fr.tick(T0, 103.0)
    check("CRC arrived but final line break missing: still a good telegram",
          len(frames) == 1 and rx.check_frame(frames[0])[1] == "")

    big = run_stream(b"/" + b"1" * (rx.MAX_FRAME + 10) + RAW["v01_winter_day_import"])
    check("runaway telegram over 4 kB is cut as 'truncated'", [e for _, e, _ in big][:1] == ["truncated"]
          and [e for _, e, _ in big][-1] == "")


def test_parse_error():
    # a telegram with a correct CRC but a register missing: only a parser can catch it
    sys.path.insert(0, str(ROOT / "lib"))
    from p1telegram import crc16
    body = RAW["v01_winter_day_import"].split(b"!")[0].replace(b"1-0:2.8.2(000456.789*kWh)\r\n", b"")
    t = body + b"!" + f"{crc16(body + b'!'):04X}\r\n".encode()
    rows = run_stream(t)
    check("CRC fine but a required register missing -> 'parse'", rows[0][1] == "parse", f"got {rows[0][1]} {rows[0][2]}")


def test_log_files():
    with tempfile.TemporaryDirectory() as d:
        log = rx.RecvLog(Path(d), "receiver-test")
        v01 = RAW["v01_winter_day_import"]
        times = [T0.replace(hour=23, minute=59, second=58), T0.replace(hour=23, minute=59, second=59),
                 T0.replace(hour=23, minute=59, second=59, microsecond=500000),
                 T0.replace(hour=0) + timedelta(days=1, seconds=3)]
        frames = [rx.Frame(v01, True, "complete", times[0]), rx.Frame(v01, True, "complete", times[1]),
                  rx.Frame(RAW["c02_wrong_crc"], True, "complete", times[2]),
                  rx.Frame(v01, True, "complete", times[3])]
        rows = []
        for f in frames:
            values, error, _ = rx.check_frame(f)
            rows.append(log.write(f, values, error))
        log.close()
        csvs = sorted(Path(d).glob("recv_*.csv"))
        raws = sorted(Path(d).glob("recv_*.raw"))
        check("new csv + raw pair at 00:00 UTC", len(csvs) == 2 and len(raws) == 2, f"{[p.name for p in csvs]}")
        with open(csvs[0], newline="", encoding="utf-8") as f:
            day1 = list(csv.DictReader(f))
        check("header = LOG_FORMAT columns", list(day1[0].keys()) == rx.LOG_COLUMNS)
        check("seq counts per file", [r["seq"] for r in day1] == ["1", "2", "3"] and rows[3]["seq"] == 1)
        check("error row: crc_ok 0, error crc", day1[2]["crc_ok"] == "0" and day1[2]["error"] == "crc")
        check("seq_gap: 0 for consecutive seconds", day1[1]["seq_gap"] == "0")
        check("seq_gap: seconds missing since the last good telegram", rows[3]["seq_gap"] == 3, f"got {rows[3]['seq_gap']}")
        check("time_utc in ms with +00:00", day1[0]["time_utc"] == "2026-10-21T23:59:58.000+00:00", day1[0]["time_utc"])
        raw = raws[0].read_bytes()
        check("raw file keeps the exact bytes", raw.count(b"#--- ") == 3 and v01 in raw and RAW["c02_wrong_crc"] in raw)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_tcp_end_to_end():
    """Injector (TCP server) -> receiver (client) -> compare_logs: the same chain as on the bench."""
    with tempfile.TemporaryDirectory() as d:
        port, sent_log = free_port(), Path(d) / "sent.csv"
        inj = subprocess.Popen([sys.executable, str(ROOT / "tools" / "inject_vectors.py"), "--tcp", str(port),
                                "--loop", "2", "--interval", "0.25", "--log", str(sent_log)],
                               stdout=subprocess.DEVNULL)
        time.sleep(0.5)
        rcv = subprocess.run([sys.executable, str(ROOT / "receiver" / "receiver.py"), "--tcp", f"127.0.0.1:{port}",
                              "--exit-on-eof", "--quiet", "--timeout", "0.12", "--log-dir", d, "--source-name", "receiver-test"],
                             capture_output=True, text=True, timeout=60)
        inj.wait(timeout=30)
        recv_logs = sorted(Path(d).glob("recv_*.csv"))
        check("TCP: receiver ran and wrote a log", rcv.returncode == 0 and len(recv_logs) == 1, rcv.stdout + rcv.stderr)
        if recv_logs:
            cmp = subprocess.run([sys.executable, str(ROOT / "tools" / "compare_logs.py"), str(sent_log),
                                  str(recv_logs[0])], capture_output=True, text=True)
            check("TCP: compare_logs says PASS (44 sent: 32 valid intact, 12 corrupt rejected)",
                  cmp.returncode == 0, cmp.stdout)


def test_compare_detects_problems():
    with tempfile.TemporaryDirectory() as d:
        sent = [{"seq": str(i + 1), "error": "", "telegram_time": f"t{i}", "imp_kw": "1.0", "crc_ok": "1",
                 "raw_len": "377", "time_utc": T0.isoformat()} for i in range(4)]
        recv = [dict(r) for r in sent]
        recv[1]["imp_kw"] = "2.0"                                  # value changed
        recv[2] = dict(recv[2], crc_ok="0", error="crc")           # corrupted in transit
        del recv[3]                                                # lost
        sys.path.insert(0, str(ROOT / "tools"))
        import compare_logs
        r = compare_logs.compare(sent, recv)
        check("compare_logs finds a changed value, a corrupted and a missing telegram",
              r["ok"] == 1 and len(r["mismatch"]) == 1 and len(r["transit"]) == 1 and len(r["missing"]) == 1, str(r))


if __name__ == "__main__":
    test_each_vector()
    test_whole_sequence_any_chunking()
    test_noise_and_idle()
    test_parse_error()
    test_log_files()
    test_compare_detects_problems()
    if "--no-tcp" not in sys.argv:
        test_tcp_end_to_end()
    print(f"\n{sum(results)}/{len(results)} passed")
    sys.exit(0 if all(results) else 1)
