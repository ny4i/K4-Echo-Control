#!/usr/bin/env python3
"""Run a simulated K4 so the whole chain can be tested without the real radio.

    python3 tools/fake_k4.py --port 9200

Point the bridge's [radio] host/port at it and say "Alexa, tell radio control
to turn off the radio" -- the simulator prints every CAT string it receives.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests"))

from fake_k4 import FakeK4  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Simulated Elecraft K4 CAT-over-TCP server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9200)
    parser.add_argument("--start-off", action="store_true", help="begin in standby")
    args = parser.parse_args()

    radio = FakeK4(host=args.host, port=args.port, power_on=not args.start_off).start()
    print("fake K4 listening on {}:{} (power {})".format(
        radio.host, radio.port, "on" if radio.power_on else "standby"))

    seen = 0
    try:
        while True:
            time.sleep(0.25)
            while seen < len(radio.history):
                token, powered = radio.history[seen]
                print("  <- {}   (power now {})".format(token, "on" if powered else "standby"))
                seen += 1
    except KeyboardInterrupt:
        print("\nstopping")
        radio.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())
