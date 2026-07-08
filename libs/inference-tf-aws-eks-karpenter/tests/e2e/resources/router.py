"""Scale-from-zero activator/router for the KEDA online-serving E2E.

The chicken-and-egg of online scale-from-zero: you cannot scale a serving Deployment
0->1 on a metric it emits itself, because at 0 replicas there is no pod and no metric
series. This router breaks it — it is an ALWAYS-ON pod (1 replica, CPU, system NG) whose
metric therefore always exists, so KEDA has something to watch while the serving pod is
at zero. It is the Knative-activator pattern, in ~stdlib Python (no pip on the air-gapped
VPC; run on the ecr-public python base, script mounted from a ConfigMap).

Behavior:
  POST /v1/chat/completions
      -> increment an in-flight gauge (this is what KEDA scales the serving pod on)
      -> forward to the vLLM Service, RETRYING until it is up (the serving pod is being
         cold-started by KEDA->Karpenter meanwhile; we hold the client connection the
         whole time, so the caller just needs a long read timeout — no queue, no 202)
      -> return the real completion; decrement the gauge in a finally
  GET /metrics
      -> Prometheus text: `router_inflight_requests` gauge + `router_requests_total`

Env:
  BACKEND_URL   the vLLM Service base, e.g. http://vllm-keda-e2e.default.svc:8000
  LISTEN_PORT   port to serve on (default 8080)
  BACKEND_TIMEOUT_S   per-attempt forward timeout (default 1200 — cold start + warmup)
"""

import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BACKEND_URL = os.environ["BACKEND_URL"].rstrip("/")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8080"))
BACKEND_TIMEOUT_S = int(os.environ.get("BACKEND_TIMEOUT_S", "1200"))
# How long to keep retrying the backend while KEDA+Karpenter bring it up.
COLD_START_BUDGET_S = int(os.environ.get("COLD_START_BUDGET_S", "1200"))
RETRY_INTERVAL_S = 3

_lock = threading.Lock()
_inflight = 0
_total = 0


def _bump(delta: int) -> None:
    global _inflight, _total
    with _lock:
        _inflight += delta
        if delta > 0:
            _total += 1


def _forward(path: str, body: bytes) -> tuple[int, bytes]:
    """Forward to the backend, retrying connection failures until the cold-start budget
    elapses (the serving pod is being provisioned meanwhile). Returns (status, body)."""
    deadline = time.monotonic() + COLD_START_BUDGET_S
    last_err = None
    while time.monotonic() < deadline:
        req = urllib.request.Request(
            f"{BACKEND_URL}{path}", data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=BACKEND_TIMEOUT_S) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            # Backend reachable but returned an error status — pass it straight through.
            return e.code, e.read()
        except (urllib.error.URLError, ConnectionError, OSError) as e:
            # Backend not up yet (no endpoints / refused) — keep holding + retry.
            last_err = e
            time.sleep(RETRY_INTERVAL_S)
    raise TimeoutError(f"backend never became ready within {COLD_START_BUDGET_S}s: {last_err}")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:  # noqa: A003 - quiet the default access log
        pass

    def _send(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/metrics":
            with _lock:
                inflight, total = _inflight, _total
            text = (
                "# HELP router_inflight_requests Requests currently held by the router.\n"
                "# TYPE router_inflight_requests gauge\n"
                f"router_inflight_requests {inflight}\n"
                "# HELP router_requests_total Requests received by the router.\n"
                "# TYPE router_requests_total counter\n"
                f"router_requests_total {total}\n"
            )
            self._send(200, text.encode(), "text/plain; version=0.0.4")
        elif self.path == "/healthz":
            self._send(200, b'{"status":"ok"}')
        else:
            self._send(404, b'{"error":"not found"}')

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        _bump(+1)
        try:
            status, resp = _forward(self.path, body)
            self._send(status, resp)
        except TimeoutError as e:
            self._send(504, json.dumps({"error": str(e)}).encode())
        finally:
            _bump(-1)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)
    print(f"[router] listening on :{LISTEN_PORT}, backend={BACKEND_URL}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
