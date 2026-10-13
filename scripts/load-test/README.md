# Load test scripts

Used to drive the app tier past the 60% CPU target-tracking threshold. Run them from **AWS CloudShell** or another host with a fast, clean network path to the ALB; a home connection exhausts local ports before the server is stressed.

```bash
# Keep-alive version (recommended). Pass a BARE hostname, no http://
python3 load-test.py <external-alb-dns> /api/cpu-load 150 300
#                     host                path         concurrency  seconds

# curl-based version (one new TCP connection per request)
./load-test-curl.sh http://<external-alb-dns>/api/status 150 300
```

The curl version is kept for comparison. At sustained concurrency it collapses into TCP `TIME_WAIT` exhaustion on the client, which is why it was replaced (see Problem 6 in `docs/build-log.md`).
