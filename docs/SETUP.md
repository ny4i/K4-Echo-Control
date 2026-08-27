# Setup

End to end, this takes about an hour the first time. Work through it in order —
each step is verifiable on its own, so if something breaks you know exactly
which hop is at fault.

- [Step 0 — reach the radio](#step-0--reach-the-radio)
- [Step 1 — install the bridge](#step-1--install-the-bridge)
- [Step 2 — pick a transport](#step-2--pick-a-transport)
  - [Option A: AWS IoT Core](#option-a-aws-iot-core-no-open-ports)
  - [Option B: signed webhook](#option-b-signed-webhook-one-forwarded-port)
- [Step 3 — create the Lambda](#step-3--create-the-lambda)
- [Step 4 — create the Alexa skill](#step-4--create-the-alexa-skill)
- [Step 5 — test it](#step-5--test-it)
- [What this costs](#what-this-costs)

## Prerequisites

- An Elecraft K4 with its network interface enabled and reachable on your LAN.
- A machine on the same LAN that stays powered on: a Raspberry Pi, a Windows
  PC, a NAS. It needs Python 3.9 or newer.
- An AWS account.
- An [Amazon developer account](https://developer.amazon.com/alexa) using the
  **same Amazon login as your Echo**. This matters: a skill in development mode
  is only usable by the account that owns it.

---

## Step 0 — reach the radio

On the K4: **MENU → Network** (or the front panel's `NET` settings). Note its IP
address and confirm remote control is enabled.

Give the radio a **DHCP reservation** on your router so its address never moves.
Everything downstream is pinned to it.

From the machine that will run the bridge:

```bash
python3 tools/k4cli.py --host 192.168.1.50 status
```

```
PS1;  ->  The K four is on.
```

If that fails, stop here — nothing else can work until it succeeds. `nc
192.168.1.50 9200` is a useful second opinion.

> No radio to hand? Run `python3 tools/fake_k4.py --port 9200` on the bridge
> machine and point everything at that instead. It behaves like a K4 for these
> three commands and prints each CAT string it receives.

---

## Step 1 — install the bridge

**Raspberry Pi / Linux**

```bash
git clone https://github.com/ny4i/K4-Echo-Control.git
cd K4-Echo-Control
sudo ./bridge/install-linux.sh
```

This creates an unprivileged `k4bridge` user, a virtualenv in `/opt/k4echo`, a
config file at `/etc/k4echo/bridge.ini`, and a systemd unit.

**Windows** — see [bridge/windows/README.md](../bridge/windows/README.md).

Now edit `/etc/k4echo/bridge.ini` and set the radio's address:

```ini
[radio]
host = 192.168.1.50
port = 9200
```

Verify the bridge itself can reach the radio:

```bash
sudo -u k4bridge /opt/k4echo/venv/bin/python -m k4echo.bridge --config /etc/k4echo/bridge.ini --selftest
```

```json
{
  "power": "on",
  "power_reply": "PS1;",
  "radio_reachable": true,
  "radio_host": "192.168.1.50:9200",
  "updated_at": 1735000000
}
```

You can also fire a real command from here, still with no AWS involved:

```bash
sudo -u k4bridge /opt/k4echo/venv/bin/python -m k4echo.bridge --config /etc/k4echo/bridge.ini --send power_off
```

---

## Step 2 — pick a transport

### Option A: AWS IoT Core (no open ports)

The bridge opens an outbound TLS connection to AWS and keeps it open. Nothing
listens at home; there is nothing to forward and nothing to scan. **This is the
recommended option.**

Run the provisioning script on any machine with the AWS CLI configured:

```bash
./tools/provision_iot.sh k4-shack-bridge ./certs
```

It creates the IoT thing, a certificate and private key, and a policy scoped to
exactly this radio's three topics — then prints the config block to paste in.

Copy `./certs` to the bridge machine over a trusted channel and lock it down:

```bash
scp -r ./certs pi@192.168.1.9:/tmp/certs
ssh pi@192.168.1.9 'sudo mkdir -p /etc/k4echo/certs && sudo mv /tmp/certs/* /etc/k4echo/certs/ && sudo chown -R root:k4bridge /etc/k4echo/certs && sudo chmod 750 /etc/k4echo/certs && sudo chmod 640 /etc/k4echo/certs/*'
rm -rf ./certs        # the private key should not linger on your laptop
```

Then in `/etc/k4echo/bridge.ini`:

```ini
[bridge]
transport = iot

[iot]
endpoint = a1b2c3d4e5f6g7-ats.iot.us-east-1.amazonaws.com
thing_name = k4-shack-bridge
cert = /etc/k4echo/certs/device.pem.crt
key = /etc/k4echo/certs/private.pem.key
root_ca = /etc/k4echo/certs/AmazonRootCA1.pem
```

Start it:

```bash
sudo systemctl enable --now k4-bridge
journalctl -u k4-bridge -f
```

```
connecting to AWS IoT at a1b2c3d4e5f6g7-ats.iot.us-east-1.amazonaws.com
connected; subscribing to k4echo/k4-shack-bridge/cmd
iot bridge ready -> radio 192.168.1.50:9200
```

Confirm from the AWS side using the IoT MQTT test client (IoT Core console →
Test → Publish). Publish to `k4echo/k4-shack-bridge/cmd`:

```json
{"command": "power_query"}
```

Subscribe to `k4echo/k4-shack-bridge/result` and you should see the radio's
reply. **Your firewall was not touched.** Skip ahead to Step 3.

### Option B: signed webhook (one forwarded port)

Choose this if you would rather not use IoT Core. The Lambda posts directly to
the bridge, which means one forwarded port.

Generate a secret — this is the only thing standing between the internet and
your radio, so let the tool make it:

```bash
python3 tools/make_secret.py
```

In `/etc/k4echo/bridge.ini`:

```ini
[bridge]
transport = webhook

[webhook]
bind = 0.0.0.0
port = 8443
path = /command
secret = <the generated value>
```

Prefer keeping the secret out of the file? Put `K4_BRIDGE_SECRET=...` in
`/etc/k4echo/bridge.env`, `chmod 600` it, and uncomment the `EnvironmentFile`
line in the systemd unit.

Start it and test **from inside the LAN first**:

```bash
sudo systemctl enable --now k4-bridge

K4_BRIDGE_SECRET='<the generated value>' python3 tools/post_command.py --url http://192.168.1.9:8443/command power_query
```

Now forward the port on your router:

| Setting | Value |
|---|---|
| External port | `8443` (pick something non-obvious if you like) |
| Internal IP | the bridge machine, e.g. `192.168.1.9` |
| Internal port | `8443` |
| Protocol | TCP |

Forward to **the bridge**, never to the radio. And if your router supports
source-IP restrictions, note that Lambda egress comes from a large, changing
AWS range — restricting it usefully requires a VPC with a NAT gateway and an
Elastic IP, which costs far more than IoT Core does. This is one of several
reasons Option A is the better deal.

You also need a stable public address. A dynamic-DNS hostname works; put that
hostname in `K4_BRIDGE_URL` rather than a bare IP.

Verify from outside your network (phone on cellular, say):

```bash
K4_BRIDGE_SECRET='<the generated value>' python3 tools/post_command.py --url http://yourhost.example.com:8443/command power_query
```

---

## Step 3 — create the Lambda

> **Region matters.** An Alexa custom skill can only call a Lambda in
> **us-east-1** (N. Virginia) for `en-US`, `eu-west-1` for European locales, or
> `us-west-2` for Far East locales. Build in the wrong region and the skill will
> not be able to see the function.

Build the package:

```bash
./tools/build_lambda.sh          # -> dist/k4-echo-lambda.zip
```

Create an execution role. For the IoT transport, use
[tools/lambda-iam-policy.json](../tools/lambda-iam-policy.json) with `REGION`,
`ACCOUNT`, and `THING_NAME` substituted. For the webhook transport the Lambda
needs no AWS permissions beyond writing logs — the `AWSLambdaBasicExecutionRole`
managed policy is enough.

```bash
aws lambda create-function --region us-east-1 --function-name k4-echo-control --runtime python3.13 --handler lambda_function.lambda_handler --role arn:aws:iam::<ACCOUNT>:role/k4-echo-control-role --zip-file fileb://dist/k4-echo-lambda.zip --timeout 8
```

An 8 second timeout matches the window Alexa gives a skill to answer.

Set the environment variables. **For the IoT transport:**

```bash
aws lambda update-function-configuration --region us-east-1 --function-name k4-echo-control --environment 'Variables={K4_TRANSPORT=iot,K4_IOT_THING_NAME=k4-shack-bridge,K4_IOT_ENDPOINT=a1b2c3d4e5f6g7-ats.iot.us-east-1.amazonaws.com}'
```

**For the webhook transport:**

```bash
aws lambda update-function-configuration --region us-east-1 --function-name k4-echo-control --environment 'Variables={K4_TRANSPORT=webhook,K4_BRIDGE_URL=http://yourhost.example.com:8443/command,K4_BRIDGE_SECRET=<the generated value>}'
```

Lambda environment variables are encrypted at rest, but anyone with
`lambda:GetFunctionConfiguration` can read them back. To keep the secret in
Secrets Manager instead, store it there and set `K4_BRIDGE_SECRET_ARN` to the
secret's ARN — the Lambda prefers it over `K4_BRIDGE_SECRET` and caches the
value between invocations. Grant the role `secretsmanager:GetSecretValue` on
that ARN.

`K4_SKILL_ID` is set in the next step, once the skill exists.

### All Lambda environment variables

| Variable | Transport | Default | Meaning |
|---|---|---|---|
| `K4_SKILL_ID` | both | *(unset)* | Alexa skill ID. Requests from any other skill are refused. **Set this.** |
| `K4_TRANSPORT` | both | `iot` | `iot` or `webhook` |
| `K4_LOG_LEVEL` | both | `INFO` | Python log level |
| `K4_IOT_THING_NAME` | iot | — | IoT thing name |
| `K4_IOT_ENDPOINT` | iot | *(SDK default)* | ATS data endpoint |
| `K4_IOT_TOPIC` | iot | `k4echo/<thing>/cmd` | Command topic |
| `K4_SHADOW_MAX_AGE` | iot | `300` | Refuse a status reading older than this many seconds |
| `K4_BRIDGE_URL` | webhook | — | Full URL of the bridge endpoint |
| `K4_BRIDGE_SECRET` | webhook | — | Shared HMAC secret |
| `K4_BRIDGE_SECRET_ARN` | webhook | *(unset)* | Secrets Manager ARN; takes precedence |
| `K4_BRIDGE_TIMEOUT` | webhook | `6` | Seconds to wait for the bridge |

---

## Step 4 — create the Alexa skill

In the [Alexa developer console](https://developer.amazon.com/alexa/console/ask),
signed in with **the same Amazon account as your Echo**:

1. **Create Skill** → name `K4 Control` → primary locale English (US).
2. Model: **Custom**. Hosting: **Provision your own**.
3. Template: **Start from Scratch**.
4. Left nav → **JSON Editor**. Paste the contents of
   [`skill/skill-package/interactionModels/custom/en-US.json`](../skill/skill-package/interactionModels/custom/en-US.json),
   replacing what is there.
5. **Save Model**, then **Build Model**. Wait for the build to finish.
6. Left nav → **Endpoint** → **AWS Lambda ARN**. Paste your function's ARN into
   *Default Region*. **Save Endpoints**.
7. Copy the **Your Skill ID** value shown on that page
   (`amzn1.ask.skill.xxxxxxxx-...`).

Give the Lambda that skill ID, both as a trigger restriction and as an in-code
check:

```bash
aws lambda add-permission --region us-east-1 --function-name k4-echo-control --statement-id alexa-skill-trigger --action lambda:InvokeFunction --principal alexa-appkit.amazon.com --event-source-token amzn1.ask.skill.xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

aws lambda update-function-configuration --region us-east-1 --function-name k4-echo-control --environment 'Variables={...existing...,K4_SKILL_ID=amzn1.ask.skill.xxxx...}'
```

> `update-function-configuration --environment` **replaces** the whole variable
> set. Include the ones you already set, or read them back first with
> `aws lambda get-function-configuration`.

### Prefer the ASK CLI?

The [`skill/`](../skill) directory is already in ASK CLI v2 layout. Put your
Lambda ARN into `skill-package/skill.json` and:

```bash
cd skill && ask deploy --target skill-metadata
```

### Changing the invocation name

`radio control` is the default because it is unambiguous and easy for Alexa to
hear. To change it, edit `invocationName` in the interaction model and rebuild.
Amazon requires it to be two or more words, all lowercase, with numbers spelled
out — and single letters written with a period and a space, so a K4-flavoured
name would be `k. four control`.

---

## Step 5 — test it

**In the console.** Developer console → **Test** tab → enable testing for
*Development*. Type or say:

```
tell radio control to turn off the radio
```

You should get back *"Turning the K four off."* and see `PS0;` in the bridge's
log:

```bash
journalctl -u k4-bridge -f
```

**On the Echo.** Same Amazon account, so the skill is already enabled:

> "Alexa, tell radio control to turn off the radio."

Also works:

> "Alexa, ask radio control if the radio is on."
> "Alexa, open radio control." … "Turn off the radio."

### Making it a one-liner

Custom skills always need their invocation name, which is a mouthful. An
**Alexa Routine** (Alexa app → More → Routines) can shorten it: trigger on a
phrase you choose, action **Custom**, and enter `tell radio control to turn off
the radio`. Then "Alexa, shut down the shack" does the whole thing. Routine
support for custom skill actions varies by region and app version — if you do
not see it, the plain invocation still works everywhere.

---

## What this costs

Assume a few dozen commands a month.

| | |
|---|---|
| Lambda | free tier covers it many times over; ~$0.00 |
| AWS IoT Core | free tier: 250k messages/month for 12 months. After that ~$1 per million messages plus $0.08/million connection-minutes — call it **a few cents a year** |
| Alexa skill | free (development mode, never published) |
| Webhook transport | $0 in AWS, but you are forwarding a port |

The one genuinely expensive option is the one this project avoids: pinning
Lambda's egress to a fixed IP needs a VPC NAT gateway, around **$32/month**.

---

Something not working? See [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
