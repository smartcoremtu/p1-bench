"""Generate the golden test vectors (work item N2).

Writes tests/vectors/<id>.txt (exact bytes, CRLF) and tests/vectors/index.json with the
expected outcome and the expected values in the log-format columns (docs/LOG_FORMAT.md).
Re-run only when the reference library or the vector list changes, and commit the output:
the files, not this script, are the contract.

    python tests/make_vectors.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from p1telegram import build_telegram, check_crc, dsmr_timestamp  # noqa: E402

OUT = ROOT / "tests" / "vectors"
UTC = timezone.utc


def one_phase(v, i, imp, exp):
    return [{"v": v, "i": i, "imp_kw": imp, "exp_kw": exp}]


def regs(i1, i2, e1, e2, tariff):
    return {"imp_t1_kwh": i1, "imp_t2_kwh": i2, "exp_t1_kwh": e1, "exp_t2_kwh": e2, "tariff": tariff}


# id, description, UTC instant, profile, values
VALID = [
    ("v01_winter_day_import", "Winter, day tariff, small import",
     datetime(2026, 12, 15, 10, 30, 0, tzinfo=UTC), "dsmr5-1ph",
     {**regs(1234.567, 2345.678, 123.456, 456.789, 2), "phases": one_phase(231.4, 2, 0.560, 0.0)}),
    ("v02_winter_night_ev", "Winter night tariff, EV charging 7.2 kW",
     datetime(2026, 12, 16, 3, 15, 0, tzinfo=UTC), "dsmr5-1ph",
     {**regs(1240.120, 2350.004, 123.456, 456.789, 1), "phases": one_phase(228.9, 31, 7.200, 0.0)}),
    ("v03_summer_import", "Summer time (S flag), evening import",
     datetime(2026, 7, 1, 19, 0, 0, tzinfo=UTC), "dsmr5-1ph",
     {**regs(1100.000, 2200.000, 100.000, 400.000, 2), "phases": one_phase(234.0, 5, 1.150, 0.0)}),
    ("v04_summer_export", "Summer midday, export only",
     datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC), "dsmr5-1ph",
     {**regs(1100.000, 2199.000, 100.000, 401.250, 2), "phases": one_phase(246.2, 13, 0.0, 3.200)}),
    ("v05_zero_power", "No import, no export (battery covering load)",
     datetime(2026, 7, 2, 1, 0, 0, tzinfo=UTC), "dsmr5-1ph",
     {**regs(1101.000, 2199.000, 100.000, 401.250, 1), "phases": one_phase(239.0, 0, 0.0, 0.0)}),
    ("v06_registers_near_max", "Registers just below 999999.999 (field width)",
     datetime(2026, 12, 15, 10, 30, 1, tzinfo=UTC), "dsmr5-1ph",
     {**regs(999999.998, 999999.999, 0.001, 0.0, 2), "phases": one_phase(230.0, 1, 0.120, 0.0)}),
    ("v07_registers_zero", "New meter, all registers zero",
     datetime(2026, 12, 15, 10, 30, 2, tzinfo=UTC), "dsmr5-1ph",
     {**regs(0.0, 0.0, 0.0, 0.0, 2), "phases": one_phase(230.0, 0, 0.001, 0.0)}),
    ("v08_peak_import", "13 kW import peak (EV + cooker)",
     datetime(2026, 12, 16, 17, 30, 0, tzinfo=UTC), "dsmr5-1ph",
     {**regs(1260.500, 2361.250, 123.456, 456.789, 2), "phases": one_phase(224.1, 58, 13.050, 0.0)}),
    ("v09_low_voltage", "Voltage at the low limit (207 V)",
     datetime(2026, 12, 16, 17, 31, 0, tzinfo=UTC), "dsmr5-1ph",
     {**regs(1260.700, 2361.250, 123.456, 456.789, 2), "phases": one_phase(207.0, 10, 2.070, 0.0)}),
    ("v10_high_voltage", "Voltage at the high limit (253 V) while exporting",
     datetime(2026, 7, 3, 12, 0, 0, tzinfo=UTC), "dsmr5-1ph",
     {**regs(1105.000, 2200.000, 100.000, 420.000, 2), "phases": one_phase(253.0, 22, 0.0, 5.500)}),
    ("v11_dst_end_last_summer", "25 Oct 2026, 01:59:59 IST (S): last second before clocks go back",
     datetime(2026, 10, 25, 0, 59, 59, tzinfo=UTC), "dsmr5-1ph",
     {**regs(1200.000, 2300.000, 110.000, 430.000, 1), "phases": one_phase(236.0, 1, 0.200, 0.0)}),
    ("v12_dst_end_first_winter", "25 Oct 2026, 01:00:00 GMT (W): same local hour again, now winter",
     datetime(2026, 10, 25, 1, 0, 0, tzinfo=UTC), "dsmr5-1ph",
     {**regs(1200.001, 2300.000, 110.000, 430.000, 1), "phases": one_phase(236.0, 1, 0.200, 0.0)}),
    ("v13_dst_start_last_winter", "28 Mar 2027, 00:59:59 GMT (W): last second before clocks go forward",
     datetime(2027, 3, 28, 0, 59, 59, tzinfo=UTC), "dsmr5-1ph",
     {**regs(1500.000, 2600.000, 130.000, 470.000, 1), "phases": one_phase(237.0, 1, 0.250, 0.0)}),
    ("v14_dst_start_first_summer", "28 Mar 2027, 02:00:00 IST (S): local 01:xx is skipped",
     datetime(2027, 3, 28, 1, 0, 0, tzinfo=UTC), "dsmr5-1ph",
     {**regs(1500.001, 2600.000, 130.000, 470.000, 1), "phases": one_phase(237.0, 1, 0.250, 0.0)}),
    ("v15_three_phase_import", "Three-phase, import on all phases",
     datetime(2026, 12, 15, 11, 0, 0, tzinfo=UTC), "dsmr5-3ph",
     {**regs(5000.000, 8000.000, 50.000, 60.000, 2),
      "phases": [{"v": 231.0, "i": 9, "imp_kw": 2.000, "exp_kw": 0.0},
                 {"v": 229.5, "i": 4, "imp_kw": 0.900, "exp_kw": 0.0},
                 {"v": 232.2, "i": 2, "imp_kw": 0.450, "exp_kw": 0.0}]}),
    ("v16_three_phase_export", "Three-phase, export on all phases",
     datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC), "dsmr5-3ph",
     {**regs(5000.000, 7900.000, 50.000, 75.000, 2),
      "phases": [{"v": 244.0, "i": 8, "imp_kw": 0.0, "exp_kw": 1.900},
                 {"v": 243.1, "i": 8, "imp_kw": 0.0, "exp_kw": 1.850},
                 {"v": 244.8, "i": 7, "imp_kw": 0.0, "exp_kw": 1.700}]}),
]


# Corruptions, applied to the bytes of a valid vector. Every one must be rejected.
def flip_digit(t: bytes) -> bytes:
    """One digit of the import register changed; CRC left as it was."""
    return t.replace(b"1-0:1.8.1(001234.567", b"1-0:1.8.1(001234.568", 1)


def wrong_crc(t: bytes) -> bytes:
    """Data intact, last bit of the CRC field flipped."""
    bang = t.rfind(b"!")
    crc = int(t[bang + 1:bang + 5], 16) ^ 0x0001
    return t[:bang + 1] + f"{crc:04X}".encode() + t[bang + 5:]


def truncated(t: bytes) -> bytes:
    """Transmission cut off half way: no '!' line at all."""
    return t[: len(t) // 2]


def missing_header(t: bytes) -> bytes:
    """Header line lost (receiver joined mid-telegram)."""
    return t[t.index(b"\r\n") + 2:]


def line_removed(t: bytes) -> bytes:
    """One data line dropped after the CRC was computed."""
    start = t.index(b"1-0:2.8.1(")
    end = t.index(b"\r\n", start) + 2
    return t[:start] + t[end:]


def lf_only(t: bytes) -> bytes:
    """Line endings changed to LF; CRC was computed over CRLF, so it no longer matches."""
    return t.replace(b"\r\n", b"\n")


CORRUPT = [
    ("c01_digit_changed", "v01_winter_day_import", flip_digit),
    ("c02_wrong_crc", "v01_winter_day_import", wrong_crc),
    ("c03_truncated", "v01_winter_day_import", truncated),
    ("c04_missing_header", "v01_winter_day_import", missing_header),
    ("c05_line_removed", "v01_winter_day_import", line_removed),
    ("c06_lf_line_endings", "v01_winter_day_import", lf_only),
]


def expected_row(when, values):
    ph = values["phases"]
    return {
        "telegram_time": dsmr_timestamp(when),
        "tariff": values["tariff"],
        "imp_t1_kwh": values["imp_t1_kwh"], "imp_t2_kwh": values["imp_t2_kwh"],
        "exp_t1_kwh": values["exp_t1_kwh"], "exp_t2_kwh": values["exp_t2_kwh"],
        "imp_kw": round(sum(p["imp_kw"] for p in ph), 3),
        "exp_kw": round(sum(p["exp_kw"] for p in ph), 3),
        "v_l1": ph[0]["v"], "i_l1": round(ph[0]["i"]),
        "v_l2": ph[1]["v"] if len(ph) > 1 else None, "i_l2": round(ph[1]["i"]) if len(ph) > 1 else None,
        "v_l3": ph[2]["v"] if len(ph) > 2 else None, "i_l3": round(ph[2]["i"]) if len(ph) > 2 else None,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.txt"):
        old.unlink()
    index = []
    raw = {}
    for vid, desc, when, profile, values in VALID:
        t = build_telegram(values, when, profile)
        assert check_crc(t), vid
        (OUT / f"{vid}.txt").write_bytes(t)
        raw[vid] = t
        index.append({"id": vid, "file": f"{vid}.txt", "expect": "valid", "profile": profile,
                      "description": desc, "sent_utc": when.isoformat(), "values": expected_row(when, values)})
    for cid, base, fn in CORRUPT:
        t = fn(raw[base])
        assert t != raw[base] and not check_crc(t), cid
        (OUT / f"{cid}.txt").write_bytes(t)
        index.append({"id": cid, "file": f"{cid}.txt", "expect": "reject", "based_on": base,
                      "description": fn.__doc__.strip() if fn.__doc__ else cid})
    (OUT / "index.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(VALID)} valid + {len(CORRUPT)} corrupt vectors to {OUT}")


if __name__ == "__main__":
    main()
