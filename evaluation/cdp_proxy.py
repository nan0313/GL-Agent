"""Tiny stdin/stdout proxy between the Node GIS runner and Chrome CDP.

The portable runtime already includes websockets. Using it avoids adding an npm
dependency solely for the Chrome DevTools websocket transport.
"""

from __future__ import annotations

import json
import sys

from websockets.sync.client import connect


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: cdp_proxy.py WS_URL", file=sys.stderr)
        return 2
    with connect(sys.argv[1], open_timeout=10, close_timeout=2, max_size=None) as websocket:
        print(json.dumps({"proxy_status": "ready"}), flush=True)
        for line in sys.stdin:
            if not line.strip():
                continue
            request = json.loads(line)
            request_id = request.get("id")
            websocket.send(json.dumps(request, ensure_ascii=False, separators=(",", ":")))
            while True:
                message = websocket.recv()
                if isinstance(message, bytes):
                    message = message.decode("utf-8")
                response = json.loads(message)
                if response.get("id") == request_id:
                    print(json.dumps(response, ensure_ascii=False, separators=(",", ":")), flush=True)
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
