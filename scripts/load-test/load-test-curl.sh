#!/bin/bash
# Concurrent load test using plain bash + curl, with live progress logging.
# No extra packages required beyond curl, which ships with stock Ubuntu/WSL.

URL="$1"
CONCURRENCY="${2:-150}"
DURATION="${3:-300}"   # seconds

if [ -z "$URL" ]; then
  echo "Usage: $0 <url> [concurrency] [duration_seconds]"
  echo "Example: $0 http://your-alb-dns/api/status 150 300"
  exit 1
fi

WORKDIR=$(mktemp -d)
trap 'rm -rf "$WORKDIR"' EXIT

START_TIME=$(date '+%Y-%m-%d %H:%M:%S')
end_time=$((SECONDS + DURATION))

echo "=================================================="
echo " Load test starting: $START_TIME"
echo " Target:       $URL"
echo " Concurrency:  $CONCURRENCY workers"
echo " Duration:     ${DURATION}s"
echo "=================================================="

# Each worker appends one HTTP status code (or 'ERR' on curl failure) per
# line to its own log file. Separate files avoid any write interleaving
# between workers, so no locking is needed.
worker() {
  local id="$1"
  local logfile="$WORKDIR/worker_${id}.log"
  while [ "$SECONDS" -lt "$end_time" ]; do
    code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 "$URL" 2>/dev/null)
    if [ -z "$code" ]; then
      echo "ERR" >> "$logfile"
    else
      echo "$code" >> "$logfile"
    fi
  done
}

for i in $(seq 1 "$CONCURRENCY"); do
  worker "$i" &
done

# --- Monitor loop: prints a progress line every 5 seconds while the test runs ---
monitor() {
  local last_total=0
  while [ "$SECONDS" -lt "$end_time" ]; do
    sleep 5
    total=$(cat "$WORKDIR"/worker_*.log 2>/dev/null | wc -l)
    ok=$(cat "$WORKDIR"/worker_*.log 2>/dev/null | grep -c '^2')
    errs=$(cat "$WORKDIR"/worker_*.log 2>/dev/null | grep -Ec '^(ERR|5|4)')
    delta=$((total - last_total))
    rps=$((delta / 5))
    elapsed=$((DURATION - (end_time - SECONDS)))
    echo "[$(date '+%H:%M:%S')] elapsed=${elapsed}s  total_requests=${total}  ~${rps} req/s  2xx=${ok}  errors=${errs}"
    last_total=$total
  done
}
monitor &
MONITOR_PID=$!

wait $MONITOR_PID
wait   # wait for all remaining worker jobs to exit cleanly

# --- Final summary ---
END_TIME=$(date '+%Y-%m-%d %H:%M:%S')
TOTAL=$(cat "$WORKDIR"/worker_*.log 2>/dev/null | wc -l)
OK_2XX=$(cat "$WORKDIR"/worker_*.log 2>/dev/null | grep -c '^2')
ERR_4XX=$(cat "$WORKDIR"/worker_*.log 2>/dev/null | grep -c '^4')
ERR_5XX=$(cat "$WORKDIR"/worker_*.log 2>/dev/null | grep -c '^5')
ERR_CURL=$(cat "$WORKDIR"/worker_*.log 2>/dev/null | grep -c '^ERR')
AVG_RPS=$((TOTAL / DURATION))

echo "=================================================="
echo " Load test finished: $END_TIME"
echo " Total requests:   $TOTAL"
echo " 2xx responses:    $OK_2XX"
echo " 4xx responses:    $ERR_4XX"
echo " 5xx responses:    $ERR_5XX"
echo " Curl-level errors (timeout/connection): $ERR_CURL"
echo " Average req/s:    $AVG_RPS"
echo "=================================================="
