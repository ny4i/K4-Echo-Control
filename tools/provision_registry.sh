#!/usr/bin/env bash
# Create the two DynamoDB tables a shared skill needs.
#
#   ./tools/provision_registry.sh
#
# Only needed when one skill serves several operators. A private deployment
# addresses the single thing in K4_IOT_THING_NAME and never reads these.
# On-demand billing: a few dozen commands a month costs nothing measurable.
set -euo pipefail

PAIRINGS="${K4_PAIRINGS_TABLE:-k4echo-pairings}"
CODES="${K4_CODES_TABLE:-k4echo-codes}"
REGION="$(aws configure get region || echo "${AWS_REGION:-us-east-1}")"

create() {
    local table="$1" key="$2"
    if aws dynamodb describe-table --region "${REGION}" --table-name "${table}" >/dev/null 2>&1; then
        echo "    ${table} already exists, leaving it alone"
        return
    fi
    aws dynamodb create-table \
        --region "${REGION}" \
        --table-name "${table}" \
        --attribute-definitions "AttributeName=${key},AttributeType=S" \
        --key-schema "AttributeName=${key},KeyType=HASH" \
        --billing-mode PAY_PER_REQUEST >/dev/null
    echo "    created ${table} (partition key ${key})"
}

echo "region: ${REGION}"
echo "==> creating tables"
create "${PAIRINGS}" user_id
create "${CODES}" code

echo "==> waiting for them to become active"
aws dynamodb wait table-exists --region "${REGION}" --table-name "${PAIRINGS}"
aws dynamodb wait table-exists --region "${REGION}" --table-name "${CODES}"

cat <<TEXT

================================================================
Done. Now set these on the Lambda, alongside the ones it already has:

  K4_MULTI_TENANT=1
  K4_PAIRINGS_TABLE=${PAIRINGS}
  K4_CODES_TABLE=${CODES}

and give its role tools/lambda-iam-policy-shared.json (REGION and ACCOUNT
substituted). Note that policy is deliberately wider than the private one --
it can reach every bridge, so the pairing table is what separates operators.

Enrol each operator with:

  ./tools/enroll_operator.sh <thing-name>
================================================================
TEXT
