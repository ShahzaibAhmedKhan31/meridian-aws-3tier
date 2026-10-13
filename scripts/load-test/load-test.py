#!/usr/bin/env python3
"""
Load test using persistent (keep-alive) HTTP connections, one per worker
thread, reused across every request that thread makes. This avoids the
TCP connection churn / TIME_WAIT exhaustion that a new-connection-per-request
approach runs into under sustained concurrency.

Uses only the Python standard library (http.client) — no pip install needed.

Usage:
    python3 load-test-v2.py <host> <path> [concurrency] [duration_seconds]

Example:
    python3 load-test-v2.py your-alb-dns.elb.amazonaws.com /api/status 150 300
"""

import sys
import time
import threading
import http.client
from collections import Counter

def main():
    if len(sys.argv) < 3:
        print("Usage: python3 load-test-v2.py <host> <path> [concurrency] [duration_seconds]")
        print("Example: python3 load-test-v2.py your-alb-dns.elb.amazonaws.com /api/status 150 300")
        sys.exit(1)

    host = sys.argv[1]
    path = sys.argv[2]
    concurrency = int(sys.argv[3]) if len(sys.argv) > 3 else 150
    duration = int(sys.argv[4]) if len(sys.argv) > 4 else 300

    print("=" * 60)
    print(f" Load test starting (keep-alive): {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" Target:       http://{host}{path}")
    print(f" Concurrency:  {concurrency} persistent connections")
    print(f" Duration:     {duration}s")
    print("=" * 60)

    stop_time = time.time() + duration
    counts = Counter()
    lock = threading.Lock()
    total_counter = {"total": 0}

    def worker():
        conn = None
        while time.time() < stop_time:
            try:
                if conn is None:
                    conn = http.client.HTTPConnection(host, timeout=5)
                conn.request("GET", path, headers={"Connection": "keep-alive"})
                resp = conn.getresponse()
                resp.read()  # must drain the body to reuse the connection
                code = resp.status
            except Exception:
                code = "ERR"
                # connection is likely broken — drop it and open a fresh one next loop
                try:
                    if conn:
                        conn.close()
                except Exception:
                    pass
                conn = None
            with lock:
                counts[code] += 1
                total_counter["total"] += 1
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    def monitor():
        last_total = 0
        while time.time() < stop_time:
            time.sleep(5)
            with lock:
                total = total_counter["total"]
                ok = sum(v for k, v in counts.items() if isinstance(k, int) and 200 <= k < 300)
                errs = sum(v for k, v in counts.items() if k == "ERR" or (isinstance(k, int) and k >= 400))
            delta = total - last_total
            rps = delta // 5
            elapsed = duration - int(stop_time - time.time())
            print(f"[{time.strftime('%H:%M:%S')}] elapsed={elapsed}s  "
                  f"total_requests={total}  ~{rps} req/s  2xx={ok}  errors={errs}")
            last_total = total

    mon_thread = threading.Thread(target=monitor, daemon=True)
    mon_thread.start()

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    mon_thread.join(timeout=1)

    with lock:
        total = total_counter["total"]
        ok_2xx = sum(v for k, v in counts.items() if isinstance(k, int) and 200 <= k < 300)
        err_4xx = sum(v for k, v in counts.items() if isinstance(k, int) and 400 <= k < 500)
        err_5xx = sum(v for k, v in counts.items() if isinstance(k, int) and 500 <= k < 600)
        err_conn = counts.get("ERR", 0)

    avg_rps = total // duration if duration else 0

    print("=" * 60)
    print(f" Load test finished: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f" Total requests:   {total}")
    print(f" 2xx responses:    {ok_2xx}")
    print(f" 4xx responses:    {err_4xx}")
    print(f" 5xx responses:    {err_5xx}")
    print(f" Connection errors: {err_conn}")
    print(f" Average req/s:    {avg_rps}")
    print("=" * 60)

if __name__ == "__main__":
    main()
