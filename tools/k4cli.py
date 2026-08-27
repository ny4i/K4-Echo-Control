#!/usr/bin/env python3
"""Talk to the K4 directly from the local network -- no AWS involved.

Use this first: if this cannot reach the radio, nothing else will.

    python3 tools/k4cli.py --host 192.168.1.50 status
    python3 tools/k4cli.py --host 192.168.1.50 off
    python3 tools/k4cli.py --host 192.168.1.50 raw 'FA;'
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from k4echo import commands  # noqa: E402
from k4echo.radio import DEFAULT_PORT, K4Client, RadioError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a CAT command to a K4 over TCP")
    parser.add_argument("--host", default=os.environ.get("K4_RADIO_HOST", "192.168.1.50"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("K4_RADIO_PORT", DEFAULT_PORT)))
    parser.add_argument("--timeout", type=float, default=5.0)

    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("off", help="send PS0; (standby)")
    sub.add_parser("on", help="send PS1;")
    sub.add_parser("status", help="send PS; and print the reply")
    raw = sub.add_parser("raw", help="send an arbitrary CAT string")
    raw.add_argument("cat")
    raw.add_argument("--expect", help="wait for a reply with this prefix")

    args = parser.parse_args()
    client = K4Client(args.host, args.port, connect_timeout=args.timeout, reply_timeout=args.timeout)

    try:
        if args.action == "off":
            client.send(commands.POWER_OFF.cat)
            print("sent PS0; to {}:{}".format(args.host, args.port))
        elif args.action == "on":
            client.send(commands.POWER_ON.cat)
            print("sent PS1; to {}:{}".format(args.host, args.port))
        elif args.action == "status":
            reply = client.ping()
            print("{}  ->  {}".format(reply, commands.describe_power_reply(reply)))
        else:
            cat = args.cat if args.cat.endswith(";") else args.cat + ";"
            reply = client.send(cat, expect_prefix=args.expect)
            print("sent {}{}".format(cat, "  ->  " + reply if reply else ""))
    except RadioError as exc:
        print("error: {}".format(exc), file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
