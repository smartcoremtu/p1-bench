# P1 bench log format (v1, 2026-10-19)

Every sender (injector, student's simulator) and every receiver (John's reference receiver) writes
**the same CSV columns**, so one comparison script can line them up. Owner of this spec: Nash.
Change it only by bumping the version and telling both the student and John.

## Files

| File | Written by | Content |
|------|-----------|---------|
| `sent_<YYYYMMDDTHHMMSSZ>.csv` | injector / simulator | one row per telegram sent |
| `recv_<YYYYMMDDTHHMMSSZ>.csv` | receiver | one row per telegram (or broken fragment) received |
| `recv_<YYYYMMDDTHHMMSSZ>.raw` | receiver | every received telegram, exactly as bytes, separated by one line `#--- <time_utc> <seq>` |

UTF-8, comma-separated, header row first, `\n` line endings, one file per run. Rotate the receiver
files daily at 00:00 UTC (a 24 h soak test is then one file). Keep raw files: they are the evidence
when a value looks wrong.

## Columns

| Column | Type / unit | Meaning |
|--------|-------------|---------|
| `time_utc` | ISO 8601, ms, `+00:00` | Sender: when writing started. Receiver: when the final `!XXXX` arrived |
| `source` | text | `injector`, `simulator`, `receiver-john`, `receiver-usb`, … |
| `seq` | integer | Counter from 1, per file, incremented for every row |
| `seq_gap` | integer | Receiver: whole seconds missing since the previous good telegram, minus 1 (0 = none missing). Sender: 0 |
| `crc_ok` | 0 / 1 | CRC16 matches |
| `raw_len` | integer, bytes | Length from `/` to the CRLF after the CRC |
| `telegram_time` | text | Value of `0-0:1.0.0` exactly as sent, e.g. `261215103000W` |
| `tariff` | 1 / 2 | `0-0:96.14.0` |
| `imp_t1_kwh`, `imp_t2_kwh` | kWh | `1-0:1.8.1`, `1-0:1.8.2` |
| `exp_t1_kwh`, `exp_t2_kwh` | kWh | `1-0:2.8.1`, `1-0:2.8.2` |
| `imp_kw`, `exp_kw` | kW | `1-0:1.7.0`, `1-0:2.7.0` |
| `v_l1`, `i_l1` | V, A | `1-0:32.7.0`, `1-0:31.7.0` |
| `v_l2`, `i_l2`, `v_l3`, `i_l3` | V, A | L2/L3 (`52.7.0`/`51.7.0`, `72.7.0`/`71.7.0`); empty for single phase |
| `vector_id` | text | Sender only: golden vector id when playing vectors; empty otherwise |
| `error` | text | Empty if fine. Receiver: `crc`, `parse`, `truncated`, `no-header`, `timeout`. Injector: `corrupt-by-design` |

Rules:
- Numbers are written as parsed (no units, `.` decimal point). Empty cell = not present in the telegram.
- A row is written for **every** fragment the receiver frames, including broken ones (with `crc_ok=0`
  and `error` set), so missing data can be told apart from corrupt data.
- Times are always UTC. The Irish local time is only inside `telegram_time`.

## Matching sent and received rows

Primary key: `telegram_time` + register values. When replaying the golden vectors in a loop,
`telegram_time` repeats, so match in order within a ±2 s window of `time_utc`.
A telegram whose header line is lost has no `/` to split on and arrives glued to the previous
fragment: expect one `error` row covering both, not two.
