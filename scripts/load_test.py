"""What happens when many employees ask at once, as at a shift change.

    python scripts/load_test.py                       # concurrency 1, 4, 8, 16, 32, 64
    python scripts/load_test.py --levels 1,16 --per-level 64

Starts the real service in this process (sign-in, retrieval with the MiniLM
embedder, prompt, validation, citations) on a free port, with one change:
the LLM is a stand-in that waits as long as Groq usually does and returns a
valid answer. So it measures our own code under load, without spending any
free-tier quota, which would allow only about 8 questions a minute anyway.

The wait is drawn from a log-normal distribution with a median of 0.75 s and
a 95th percentile of about 1.1 s, the figures measured over 5 evaluation
runs. Each level sends `--per-level` questions, `level` at a time, and
reports the full HTTP time an employee would see.
"""

import argparse
import json
import math
import random
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import uvicorn

from app.config import EmbedderKind, LLMProvider, Settings
from app.container import Container
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = {
    "yamuna": ("YAM-1002", "What time does the security escort start for women on night drops?"),
    "gomti": ("GOM-2002", "How long will the cab wait for me at pickup?"),
    "medhavi": ("MED-3002", "What's the spending limit on each cab ride?"),
}
# log-normal with median 0.75 s; sigma chosen so that p95 = 0.75 * e^(1.645 sigma) ≈ 1.1 s
MEDIAN_S, SIGMA = 0.75, math.log(1.1 / 0.75) / 1.645


class GroqLikeLLM:
    """Waits like the hosted model, then answers from the first excerpt."""

    name = "groq-like"

    def __init__(self, seed: int = 7):
        self._random = random.Random(seed)
        self._lock = threading.Lock()

    def complete_json(self, system: str, user: str, schema: dict, timeout: float) -> str:
        with self._lock:
            wait = self._random.lognormvariate(math.log(MEDIAN_S), SIGMA)
        time.sleep(wait)
        return json.dumps(
            {"verdict": "answered", "answer": "As the policy states in the cited section.", "citations": ["S1"]}
        )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def post(url: str, body: dict, token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, math.ceil(pct / 100 * len(ordered)) - 1)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--levels", default="1,4,8,16,32,64", help="concurrent requests to try, comma-separated")
    parser.add_argument("--per-level", type=int, default=96, help="questions sent at each level")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="load-test-") as tmp:
        settings = Settings(
            _env_file=None,
            data_dir=Path(tmp),
            policy_seed_dir=ROOT / "data" / "policies",
            employee_directory=ROOT / "data" / "employees.json",
            embedder=EmbedderKind.MINILM,
            llm_provider=LLMProvider.NONE,
            jwt_secret="load-test-secret-that-is-at-least-32-bytes",
            # Room for every level's sign-ins; the test is about questions.
            max_sessions_per_employee=50,
        )
        port = free_port()
        app = create_app(settings, Container(settings, llm=GroqLikeLLM()))
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        while not server.started:
            time.sleep(0.05)
        url = f"http://127.0.0.1:{port}"

        tokens = {}
        for company, (employee, _) in QUESTIONS.items():
            status, body = post(f"{url}/v1/auth/login", {"company_id": company, "employee_id": employee})
            if status != 200:
                raise SystemExit(f"could not sign in to {company}: {status} {body}")
            tokens[company] = body["access_token"]

        def ask(i: int) -> tuple[float, str, float]:
            company = list(QUESTIONS)[i % len(QUESTIONS)]
            started = time.perf_counter()
            code, body = post(f"{url}/v1/ask", {"question": QUESTIONS[company][1]}, tokens[company])
            elapsed = (time.perf_counter() - started) * 1000
            return elapsed, body.get("status", str(code)), body.get("meta", {}).get("latency_ms") or 0.0

        print("concurrent  questions  p50 ms  p95 ms  max ms  in-service p95  answered  try_again  throughput q/s")
        for level in (int(x) for x in args.levels.split(",")):
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=level) as pool:
                results = list(pool.map(ask, range(args.per_level)))
            wall = time.perf_counter() - started
            times = [r[0] for r in results]
            service = [r[2] for r in results]
            statuses = [r[1] for r in results]
            print(
                f"{level:10}  {len(results):9}  {percentile(times, 50):6.0f}  {percentile(times, 95):6.0f}  "
                f"{max(times):6.0f}  {percentile(service, 95):14.0f}  {statuses.count('answered'):8}  "
                f"{statuses.count('try_again'):9}  {len(results) / wall:14.1f}"
            )
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
