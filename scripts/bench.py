"""Measures end-to-end latency of /v1/ask against a running server.

    python scripts/bench.py                      # against http://localhost:8000
    python scripts/bench.py --rounds 2 --pace 15 # more samples, slower pace

Times the full HTTP round trip (token check, retrieval, LLM call, response),
which is what an employee waits for. Questions come from the evaluation set.
The pace between requests keeps the run inside free-tier rate limits; it is
not part of the measured time.
"""

import argparse
import json
import math
import time
import urllib.error
import urllib.request
from pathlib import Path

CASES = Path(__file__).resolve().parent.parent / "evaluation" / "cases.jsonl"
# One sample employee per company (see data/employees.json).
BENCH_ACCOUNTS = {"yamuna": "YAM-1002", "gomti": "GOM-2002", "medhavi": "MED-3002"}


def post(url: str, body: dict, token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8000")
    parser.add_argument("--rounds", type=int, default=1, help="times to go through the question list")
    parser.add_argument("--pace", type=float, default=12.0, help="seconds between requests (free-tier limits)")
    args = parser.parse_args()

    cases = [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]
    cases = [c for c in cases if c["expected"]["status"] != "other_company"]  # those never call the LLM
    tokens = {}
    for company in sorted({c["company"] for c in cases}):
        login = {"company_id": company, "employee_id": BENCH_ACCOUNTS[company]}
        status, body = post(f"{args.url}/v1/auth/login", login)
        if status != 200:
            raise SystemExit(f"could not sign in to {company}: {status} {body}")
        tokens[company] = body["access_token"]

    timings, statuses = [], {}
    for _ in range(args.rounds):
        for case in cases:
            started = time.perf_counter()
            code, body = post(f"{args.url}/v1/ask", {"question": case["question"]}, tokens[case["company"]])
            elapsed_ms = (time.perf_counter() - started) * 1000
            status = body.get("status", str(code))
            statuses[status] = statuses.get(status, 0) + 1
            timings.append(elapsed_ms)
            print(f"{elapsed_ms:7.0f} ms  {status:13} {case['id']}")
            time.sleep(args.pace)

    print(f"\nrequests: {sum(statuses.values())}  outcomes: {statuses}")
    if timings:
        print(
            f"latency (all requests): p50={percentile(timings, 50):.0f} ms  "
            f"p95={percentile(timings, 95):.0f} ms  max={max(timings):.0f} ms  n={len(timings)}"
        )


if __name__ == "__main__":
    main()
