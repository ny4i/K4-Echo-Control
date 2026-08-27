#!/usr/bin/env python3
"""Generate a shared signing secret for the Lambda <-> bridge webhook.

    python3 tools/make_secret.py

Put the same value in the Lambda's K4_BRIDGE_SECRET (or Secrets Manager) and
in the bridge's [webhook] secret.
"""

import secrets

if __name__ == "__main__":
    print(secrets.token_urlsafe(48))
