"""Reference DSMR 5 P1 telegram builder and CRC check (SmartCORE P1 bench, work item N2).

This module is the contract: the student's simulator must produce byte-identical
telegrams for the golden vectors, and John's receiver must accept every valid one
and reject every corrupt one. Standard library only, Python 3.9+.

Baseline: DSMR 5.0.2 P1 Companion Standard (115200 baud, 8N1, one telegram per second).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

IRISH_TZ = ZoneInfo("Europe/Dublin")
DEFAULT_HEADER = "/SIM5\\SMARTCORE-P1-SIMULATOR"
DEFAULT_EQUIPMENT_ID = "SIMULATOR0001"  # fake on purpose: never a real meter serial or MPRN

# Profiles: which lines a telegram carries. Adding an Irish profile ("ie-...") means adding an
# entry here once ESB Networks publishes the format; the transports do not change.
PROFILES = {
    "dsmr5-1ph": {"phases": 1, "version": "50"},
    "dsmr5-3ph": {"phases": 3, "version": "50"},
}

# OBIS codes per phase: (voltage, current, import power, export power)
_PHASE_OBIS = {
    1: ("32.7.0", "31.7.0", "21.7.0", "22.7.0"),
    2: ("52.7.0", "51.7.0", "41.7.0", "42.7.0"),
    3: ("72.7.0", "71.7.0", "61.7.0", "62.7.0"),
}


def crc16(data: bytes) -> int:
    """CRC-16/ARC (poly 0xA001 reflected, init 0) as DSMR 5 specifies.

    Computed over every byte from the leading '/' up to and including the '!'.
    """
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def dsmr_timestamp(when: datetime) -> str:
    """YYMMDDhhmmss + S/W in Irish local time.

    The S/W flag comes from the UTC offset (+1 h = summer). Python's dst() is not used:
    tzdata models Europe/Dublin with a negative DST in winter, so dst() reads inverted.
    """
    if when.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    local = when.astimezone(IRISH_TZ)
    flag = "S" if local.utcoffset() == timedelta(hours=1) else "W"
    return f"{local:%y%m%d%H%M%S}{flag}"


def build_telegram(values: dict, when: datetime, profile: str = "dsmr5-1ph",
                   header: str = DEFAULT_HEADER, equipment_id: str = DEFAULT_EQUIPMENT_ID) -> bytes:
    """Return one complete telegram (CRLF line endings, trailing CRLF after the CRC).

    values keys:
      imp_t1_kwh, imp_t2_kwh, exp_t1_kwh, exp_t2_kwh  registers, kWh (>= 0)
      tariff                                          1 (low) or 2 (normal)
      phases: list of dicts, one per phase, each with
        v (volts), i (amps, rounded to whole amps on the wire), imp_kw, exp_kw
    Total import/export power (1.7.0 / 2.7.0) is the sum over the phases.
    """
    prof = PROFILES[profile]
    phases = values["phases"]
    if len(phases) != prof["phases"]:
        raise ValueError(f"profile {profile} needs {prof['phases']} phase(s), got {len(phases)}")
    if values["tariff"] not in (1, 2):
        raise ValueError("tariff must be 1 or 2")
    imp_kw = sum(p["imp_kw"] for p in phases)
    exp_kw = sum(p["exp_kw"] for p in phases)
    if imp_kw > 0 and exp_kw > 0:
        raise ValueError("import and export power cannot both be non-zero")

    lines = [
        header,
        "",
        f"1-3:0.2.8({prof['version']})",
        f"0-0:1.0.0({dsmr_timestamp(when)})",
        f"0-0:96.1.1({equipment_id.encode('ascii').hex().upper()})",
        f"1-0:1.8.1({values['imp_t1_kwh']:010.3f}*kWh)",
        f"1-0:1.8.2({values['imp_t2_kwh']:010.3f}*kWh)",
        f"1-0:2.8.1({values['exp_t1_kwh']:010.3f}*kWh)",
        f"1-0:2.8.2({values['exp_t2_kwh']:010.3f}*kWh)",
        f"0-0:96.14.0({values['tariff']:04d})",
        f"1-0:1.7.0({imp_kw:06.3f}*kW)",
        f"1-0:2.7.0({exp_kw:06.3f}*kW)",
    ]
    for n, p in enumerate(phases, start=1):
        v_obis, i_obis, imp_obis, exp_obis = _PHASE_OBIS[n]
        lines.append(f"1-0:{v_obis}({p['v']:05.1f}*V)")
        lines.append(f"1-0:{i_obis}({round(p['i']):03d}*A)")
    for n, p in enumerate(phases, start=1):
        _, _, imp_obis, exp_obis = _PHASE_OBIS[n]
        lines.append(f"1-0:{imp_obis}({p['imp_kw']:06.3f}*kW)")
        lines.append(f"1-0:{exp_obis}({p['exp_kw']:06.3f}*kW)")

    body = ("\r\n".join(lines) + "\r\n!").encode("ascii")
    return body + f"{crc16(body):04X}\r\n".encode("ascii")


def check_crc(telegram: bytes) -> bool:
    """True if a complete telegram ('/' ... '!XXXX') carries a correct CRC."""
    start = telegram.find(b"/")
    bang = telegram.rfind(b"!")
    if start < 0 or bang < start or len(telegram) < bang + 5:
        return False
    given = telegram[bang + 1:bang + 5]
    try:
        return int(given, 16) == crc16(telegram[start:bang + 1])
    except ValueError:
        return False


if __name__ == "__main__":
    demo = {
        "imp_t1_kwh": 1234.567, "imp_t2_kwh": 2345.678, "exp_t1_kwh": 123.456, "exp_t2_kwh": 456.789,
        "tariff": 2, "phases": [{"v": 231.4, "i": 2.4, "imp_kw": 0.56, "exp_kw": 0.0}],
    }
    t = build_telegram(demo, datetime.now(timezone.utc))
    print(t.decode("ascii"), end="")
    print("CRC ok:", check_crc(t))
