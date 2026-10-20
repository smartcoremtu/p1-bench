"""Check the golden vectors against the reference CRC and, if installed, dsmr_parser.

    python tests/test_vectors.py

Exit code 0 = all checks passed. dsmr_parser is the parser Home Assistant's DSMR
integration uses, so a vector it accepts is one HA will accept.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lib"))
from p1telegram import check_crc  # noqa: E402

VEC = ROOT / "tests" / "vectors"

try:
    from dsmr_parser import obis_references as obis
    from dsmr_parser import telegram_specifications
    from dsmr_parser.parsers import TelegramParser
    PARSER = TelegramParser(telegram_specifications.V5)
except ImportError:
    PARSER = None

# log column -> dsmr_parser attribute
FIELDS = {
    "imp_t1_kwh": "ELECTRICITY_USED_TARIFF_1", "imp_t2_kwh": "ELECTRICITY_USED_TARIFF_2",
    "exp_t1_kwh": "ELECTRICITY_DELIVERED_TARIFF_1", "exp_t2_kwh": "ELECTRICITY_DELIVERED_TARIFF_2",
    "imp_kw": "CURRENT_ELECTRICITY_USAGE", "exp_kw": "CURRENT_ELECTRICITY_DELIVERY",
    "v_l1": "INSTANTANEOUS_VOLTAGE_L1", "i_l1": "INSTANTANEOUS_CURRENT_L1",
    "v_l2": "INSTANTANEOUS_VOLTAGE_L2", "i_l2": "INSTANTANEOUS_CURRENT_L2",
    "v_l3": "INSTANTANEOUS_VOLTAGE_L3", "i_l3": "INSTANTANEOUS_CURRENT_L3",
}


def parse_with_dsmr_parser(raw: bytes):
    """Return dict of values, or raise if dsmr_parser rejects the telegram."""
    telegram = PARSER.parse(raw.decode("ascii"))
    out = {}
    for col, attr in FIELDS.items():
        ref = getattr(obis, attr)
        if ref in telegram:
            out[col] = float(telegram[ref].value)
    return out


def main():
    index = json.loads((VEC / "index.json").read_text(encoding="utf-8"))
    failures = 0
    for v in index:
        raw = (VEC / v["file"]).read_bytes()
        crc_ok = check_crc(raw)
        notes = []
        ok = crc_ok == (v["expect"] == "valid")
        if not ok:
            notes.append(f"reference CRC says {crc_ok}")
        if PARSER is not None:
            try:
                got = parse_with_dsmr_parser(raw)
                if v["expect"] == "reject":
                    ok = False
                    notes.append("dsmr_parser ACCEPTED a corrupt vector")
                else:
                    for col, want in v["values"].items():
                        if col in FIELDS and want is not None and abs(got.get(col, float("nan")) - want) > 1e-6:
                            ok = False
                            notes.append(f"{col}: parser {got.get(col)} != expected {want}")
            except Exception as e:  # dsmr_parser raises several exception types
                if v["expect"] == "valid":
                    ok = False
                    notes.append(f"dsmr_parser rejected: {type(e).__name__}: {e}")
                else:
                    notes.append(f"rejected by dsmr_parser ({type(e).__name__})")
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {v['id']:<28} {v['expect']:<6} {'; '.join(notes)}")
    print(f"\n{len(index) - failures}/{len(index)} passed"
          + ("" if PARSER else "  (dsmr_parser not installed: CRC checks only)"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
