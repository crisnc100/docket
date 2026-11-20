"""A stand-in for the Anthropic Messages API, for the beta load run only.

It answers every classify call with "lease" and every choose call with the first
candidate, after a short pause, and reports token usage like the real API. The worker
reaches it through the Anthropic SDK's ANTHROPIC_BASE_URL, which only
scripts/load/compose.load.yaml sets. Nothing it answers is billed.
"""

import ast
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAUSE_SECONDS = 0.4


def answer(body: dict) -> dict:
    prompt = body["messages"][0]["content"]
    if prompt.startswith("CANDIDATE IDS:\n"):
        ids = ast.literal_eval(prompt.split("\n", 2)[1])
        output = {"record_id": ids[0] if ids else None}
    else:
        output = {"category": "lease"}
    return {
        "id": "msg_stub",
        "type": "message",
        "role": "assistant",
        "model": body["model"],
        "content": [{"type": "text", "text": json.dumps(output)}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": len(prompt) // 4 + 50, "output_tokens": 9},
    }


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length))
        time.sleep(PAUSE_SECONDS)
        reply = json.dumps(answer(body)).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(reply)))
        self.end_headers()
        self.wfile.write(reply)

    def log_message(self, format, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
