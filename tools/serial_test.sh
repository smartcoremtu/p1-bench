#!/usr/bin/env bash
# Serial bench test on the receiver Pi: plays the golden vectors into the UART and checks that the
# receiver logged every valid one intact and rejected every corrupt one.
#
#   bash tools/serial_test.sh internal  # Pi talks to itself inside the UART chip: no wiring at all
#   bash tools/serial_test.sh self      # Pi talks to itself through the pins: jumper header pin 8 (TX) to pin 10 (RX)
#   bash tools/serial_test.sh listen    # only receive, for a sender on another machine (laptop + USB
#                                       # adapter, or the student's emulator); stop with Ctrl+C
# Options via environment: LOOPS (default 2), DEV (default from /opt/p1bench/receiver.env).
# The p1-receiver service is stopped for the test (two readers on one port steal bytes from each
# other) and started again afterwards if it was running.
set -euo pipefail
cd "$(dirname "$0")/.."
MODE="${1:-self}"
[[ -f /opt/p1bench/receiver.env ]] && source /opt/p1bench/receiver.env
DEV="${DEV:-${P1_SERIAL_DEVICE:-/dev/ttyAMA0}}"
LOOPS="${LOOPS:-2}"
PY=/opt/p1bench/.venv/bin/python
[[ -x $PY ]] || PY=python3
OUT="/opt/p1bench/logs/test_$(date -u +%Y%m%dT%H%M%SZ)_$MODE"
mkdir -p "$OUT"

was_running=0
if systemctl is-active --quiet p1-receiver; then
  was_running=1
  sudo systemctl stop p1-receiver
fi
restore() { [[ $was_running == 1 ]] && sudo systemctl start p1-receiver || true; }
trap restore EXIT

echo "== receiver on $DEV, logs in $OUT"
"$PY" receiver/receiver.py --serial "$DEV" --log-dir "$OUT" --source-name receiver-ref --quiet &
RX=$!
sleep 1

if [[ $MODE == self || $MODE == internal ]]; then
  LB=""; [[ $MODE == internal ]] && LB="--loopback"
  "$PY" tools/inject_vectors.py --serial "$DEV" $LB --loop "$LOOPS" --log "$OUT/sent.csv" >/dev/null
  sleep 1
  kill -TERM $RX; wait $RX || true
  echo
  if ! ls "$OUT"/recv_*.csv >/dev/null 2>&1; then
    echo "FAIL: nothing received.$([[ $MODE == self ]] && echo " Is the jumper on header pins 8 and 10?")"; exit 1
  fi
  "$PY" tools/compare_logs.py "$OUT/sent.csv" "$OUT"/recv_*.csv
else
  echo "== listening; start the sender now, Ctrl+C when it has finished"
  trap 'kill -TERM $RX 2>/dev/null' INT
  wait $RX || true
  wait $RX 2>/dev/null || true
  echo "copy the sender's sent_*.csv here and run:"
  echo "   $PY tools/compare_logs.py <sent.csv> $(ls "$OUT"/recv_*.csv 2>/dev/null | head -1)"
fi
