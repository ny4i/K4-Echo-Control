#!/usr/bin/env bash
# Provision the AWS IoT Core side of the bridge -- the transport that needs no
# open port on the home router.
#
#   ./tools/provision_iot.sh [thing-name] [output-dir]
#
# Creates a thing, a certificate and key pair, and a policy scoped to just this
# radio's topics; downloads the Amazon root CA; and prints the [iot] section to
# paste into bridge.ini. Requires the AWS CLI, already configured.
set -euo pipefail

THING="${1:-k4-shack-bridge}"
OUTDIR="${2:-./certs}"
REGION="$(aws configure get region || echo "${AWS_REGION:-us-east-1}")"
ACCOUNT="$(aws sts get-caller-identity --query Account --output text)"
POLICY="${THING}-policy"

CMD_TOPIC="k4echo/${THING}/cmd"
RESULT_TOPIC="k4echo/${THING}/result"

echo "region     : ${REGION}"
echo "account    : ${ACCOUNT}"
echo "thing      : ${THING}"
echo "output dir : ${OUTDIR}"
echo

mkdir -p "${OUTDIR}"
chmod 700 "${OUTDIR}"

echo "==> creating thing"
aws iot create-thing --thing-name "${THING}" >/dev/null

echo "==> writing policy (scoped to this thing's topics only)"
POLICY_DOC="$(cat <<JSON
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": "iot:Connect",
      "Resource": "arn:aws:iot:${REGION}:${ACCOUNT}:client/${THING}*"
    },
    {
      "Effect": "Allow",
      "Action": "iot:Subscribe",
      "Resource": [
        "arn:aws:iot:${REGION}:${ACCOUNT}:topicfilter/${CMD_TOPIC}",
        "arn:aws:iot:${REGION}:${ACCOUNT}:topicfilter/\$aws/things/${THING}/shadow/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "iot:Receive",
      "Resource": [
        "arn:aws:iot:${REGION}:${ACCOUNT}:topic/${CMD_TOPIC}",
        "arn:aws:iot:${REGION}:${ACCOUNT}:topic/\$aws/things/${THING}/shadow/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": "iot:Publish",
      "Resource": [
        "arn:aws:iot:${REGION}:${ACCOUNT}:topic/${RESULT_TOPIC}",
        "arn:aws:iot:${REGION}:${ACCOUNT}:topic/\$aws/things/${THING}/shadow/update"
      ]
    }
  ]
}
JSON
)"

if aws iot get-policy --policy-name "${POLICY}" >/dev/null 2>&1; then
    echo "    policy ${POLICY} already exists, leaving it alone"
else
    aws iot create-policy --policy-name "${POLICY}" --policy-document "${POLICY_DOC}" >/dev/null
fi

echo "==> creating certificate and keys"
CERT_ARN="$(aws iot create-keys-and-certificate \
    --set-as-active \
    --certificate-pem-outfile "${OUTDIR}/device.pem.crt" \
    --private-key-outfile "${OUTDIR}/private.pem.key" \
    --public-key-outfile "${OUTDIR}/public.pem.key" \
    --query certificateArn --output text)"

echo "==> attaching policy and thing to the certificate"
aws iot attach-policy --policy-name "${POLICY}" --target "${CERT_ARN}"
aws iot attach-thing-principal --thing-name "${THING}" --principal "${CERT_ARN}"

echo "==> downloading the Amazon root CA"
curl -fsSL https://www.amazontrust.com/repository/AmazonRootCA1.pem \
    -o "${OUTDIR}/AmazonRootCA1.pem"

chmod 600 "${OUTDIR}"/*.pem "${OUTDIR}"/*.key "${OUTDIR}"/*.crt 2>/dev/null || true

ENDPOINT="$(aws iot describe-endpoint --endpoint-type iot:Data-ATS --query endpointAddress --output text)"

CERT_PATH="$(cd "${OUTDIR}" && pwd)"

cat <<EOF

================================================================
Done. Paste this into bridge.ini on the Pi / Windows box:

[bridge]
transport = iot

[iot]
endpoint = ${ENDPOINT}
thing_name = ${THING}
cert = ${CERT_PATH}/device.pem.crt
key = ${CERT_PATH}/private.pem.key
root_ca = ${CERT_PATH}/AmazonRootCA1.pem

And set these on the Lambda:

  K4_TRANSPORT=iot
  K4_IOT_THING_NAME=${THING}
  K4_IOT_ENDPOINT=${ENDPOINT}

Copy ${OUTDIR} to the bridge machine over a trusted channel (scp), then
delete it from here -- the private key is the bridge's only credential.
================================================================
EOF
