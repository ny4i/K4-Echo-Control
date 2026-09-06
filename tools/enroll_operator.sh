#!/usr/bin/env bash
# Enrol one operator into a shared skill.
#
#   ./tools/enroll_operator.sh <thing-name> [output-dir]
#
# Provisions their bridge's IoT thing, certificate and scoped policy, then
# registers a six-digit pairing code they will speak once to bind their Alexa
# account to that bridge. Hand them the certificate bundle over a channel you
# trust and tell them the code; they need no AWS account of their own.
set -euo pipefail

THING="${1:?usage: enroll_operator.sh <thing-name> [output-dir]}"
OUTDIR="${2:-./certs-${THING}}"
CODES="${K4_CODES_TABLE:-k4echo-codes}"
REGION="$(aws configure get region || echo "${AWS_REGION:-us-east-1}")"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Six digits, drawn at random rather than derived from the name -- a guessable
# code is a way into somebody else's radio. The first digit is never zero:
# Alexa's AMAZON.NUMBER slot hands back a *number*, so a code of 048291 would
# arrive as "48291" and never match.
CODE="$(python3 -c 'import secrets; print(secrets.choice("123456789") + "".join(secrets.choice("0123456789") for _ in range(5)))')"

echo "==> provisioning the bridge"
"${HERE}/provision_iot.sh" "${THING}" "${OUTDIR}"

echo "==> registering pairing code"
aws dynamodb put-item \
    --region "${REGION}" \
    --table-name "${CODES}" \
    --item "{\"code\":{\"S\":\"${CODE}\"},\"thing_name\":{\"S\":\"${THING}\"},\"issued_at\":{\"N\":\"$(date +%s)\"}}" \
    >/dev/null

cat <<TEXT

================================================================
Operator enrolled.

  bridge (thing) : ${THING}
  certificates   : ${OUTDIR}
  pairing code   : ${CODE}

Send them ${OUTDIR} over a trusted channel, then delete your copy -- the
private key is their bridge's only credential. Tell them to say, once:

  "Alexa, ask radio control to pair ${CODE}"

The code stays valid on purpose. Alexa mints a new user id whenever a skill is
disabled and re-enabled, and re-pairing then costs them one sentence instead of
an email to you.
================================================================
TEXT
