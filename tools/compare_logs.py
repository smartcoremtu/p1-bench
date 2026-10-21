"""Compare what a sender sent with what a receiver logged (the pass/fail check for a bench run).

    python tools/compare_logs.py logs/sent_20261021T120000Z.csv logs/recv_20261021T120001Z.csv

Rows are matched in order (the two machines' clocks are not trusted to agree):
  valid telegram sent    -> the next received row must be good (crc_ok=1, no error) with equal values
  corrupt telegram sent  -> the next received row must be an error row (it was rejected)
A good sent row whose received row is broken counts as 'corrupted in transit'; a sent row with no
received row at all counts as 'missing'; received rows matching nothing count as 'extra'.
Exit code 0 = every valid telegram arrived intact and every corrupt one was rejected.
Log columns: docs/LOG_FORMAT.md.
"""
import argparse
import csv
import statistics
import sys
from datetime import datetime

VALUE_COLUMNS = ["telegram_time", "tariff", "imp_t1_kwh", "imp_t2_kwh", "exp_t1_kwh", "exp_t2_kwh",
                 "imp_kw", "exp_kw", "v_l1", "i_l1", "v_l2", "i_l2", "v_l3", "i_l3"]
LOOKAHEAD = 3   # received rows to search past an unexpected one


def read(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def good(row):
    return row["crc_ok"] == "1" and not row["error"]


def same_values(a, b):
    """Returns a list of differing columns ([] = equal)."""
    diff = []
    for c in VALUE_COLUMNS:
        x, y = a.get(c, ""), b.get(c, "")
        if x == y:
            continue
        try:
            if abs(float(x) - float(y)) < 1e-6:
                continue
        except ValueError:
            pass
        diff.append(f"{c} sent {x!r} got {y!r}")
    return diff


def compare(sent, recv):
    res = {"ok": 0, "rejected": 0, "mismatch": [], "transit": [], "missing": [], "accepted_corrupt": [],
           "corrupt_unseen": [], "extra": [], "latency": []}
    p = 0
    for s in sent:
        label = f"sent #{s['seq']} {s.get('vector_id') or s.get('telegram_time')}"
        expect_good = not s["error"]
        if expect_good:
            hit = None
            for k in range(p, min(p + LOOKAHEAD + 1, len(recv))):
                if good(recv[k]) and not same_values(s, recv[k]):
                    hit = k
                    break
            if hit is not None:
                res["extra"] += [f"recv #{r['seq']} {r['error'] or 'good'}" for r in recv[p:hit]]
                res["ok"] += 1
                res["latency"].append(_secs(recv[hit]["time_utc"]) - _secs(s["time_utc"]))
                p = hit + 1
            elif p < len(recv) and good(recv[p]):
                res["mismatch"].append(f"{label} vs recv #{recv[p]['seq']}: {'; '.join(same_values(s, recv[p]))}")
                p += 1
            elif p < len(recv) and recv[p]["error"]:
                res["transit"].append(f"{label} -> recv #{recv[p]['seq']} error {recv[p]['error']}")
                p += 1
            else:
                res["missing"].append(label)
        else:
            if p < len(recv) and recv[p]["error"]:
                res["rejected"] += 1
                p += 1
            elif p < len(recv) and good(recv[p]) and s.get("raw_len") == recv[p]["raw_len"]:
                # a good row the same length as the corrupt telegram: the receiver accepted it
                res["accepted_corrupt"].append(f"{label} -> recv #{recv[p]['seq']}")
                p += 1
            else:
                res["corrupt_unseen"].append(label)
    res["extra"] += [f"recv #{r['seq']} {r['error'] or 'good'}" for r in recv[p:]]
    return res


def _secs(t):
    return datetime.fromisoformat(t).timestamp()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sent")
    ap.add_argument("recv")
    a = ap.parse_args()
    sent, recv = read(a.sent), read(a.recv)
    r = compare(sent, recv)
    n_good = sum(1 for s in sent if not s["error"])
    n_bad = len(sent) - n_good
    print(f"sent {len(sent)} ({n_good} valid, {n_bad} corrupt by design); received {len(recv)} rows")
    print(f"  valid arrived intact     {r['ok']}/{n_good}")
    print(f"  corrupt rejected         {r['rejected']}/{n_bad}")
    for key, title in [("mismatch", "VALUES DIFFER"), ("transit", "corrupted in transit"), ("missing", "MISSING"),
                       ("accepted_corrupt", "CORRUPT ACCEPTED"), ("corrupt_unseen", "corrupt not seen at all"),
                       ("extra", "extra received rows")]:
        if r[key]:
            print(f"  {title}: {len(r[key])}")
            for line in r[key][:10]:
                print(f"      {line}")
    if r["latency"]:
        lat = r["latency"]
        print(f"  receive time - send time: median {statistics.median(lat):+.3f} s "
              f"(min {min(lat):+.3f}, max {max(lat):+.3f}; includes any clock difference between the machines)")
    passed = r["ok"] == n_good and r["rejected"] == n_bad and not r["extra"]
    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
