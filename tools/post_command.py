#!/usr/bin/env python3
"""Send a signed command to the bridge's webhook, the way the Lambda does.

Use this to prove the webhook path works before wiring up Alexa -- run it from
inside the LAN against the bridge's LAN address, then from outside against the
public address to confirm the port forward.

    K4_BRIDGE_SECRET=... python3 tools/post_command.py \
        --url http://192.168.1.9:8443/command power_off
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from k4echo import signing  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a signed command to the K4 bridge")
    parser.add_argument("command", choices=["power_off", "power_on", "power_query"])
    parser.add_argument("--url", default=os.environ.get("K4_BRIDGE_URL"), required=False)
    parser.add_argument("--secret", default=os.environ.get("K4_BRIDGE_SECRET"))
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    if not args.url:
        parser.error("pass --url or set K4_BRIDGE_URL")
    if not args.secret:
        parser.error("pass --secret or set K4_BRIDGE_SECRET")

    body = json.dumps({"command": args.command}, separators=(",", ":")).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    headers.update(signing.sign_request(args.secret, body))

    request = urllib.request.Request(args.url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            print(json.dumps(json.loads(response.read().decode()), indent=2))
    except urllib.error.HTTPError as exc:
        print("HTTP {}: {}".format(exc.code, exc.read().decode(errors="replace")), file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print("cannot reach the bridge: {}".format(exc.reason), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
