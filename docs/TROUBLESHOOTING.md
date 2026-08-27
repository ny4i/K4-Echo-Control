# Troubleshooting

Work from the radio outward. Each hop can be tested on its own, and testing them
in order tells you exactly where the chain breaks.

```
 [Echo] -> [skill] -> [Lambda] -> [transport] -> [bridge] -> [K4]
    5         4          3            2             1         0
```

## 0. The bridge cannot reach the radio

```bash
python3 tools/k4cli.py --host 192.168.1.50 status
```

**`cannot reach the radio ... Connection refused`**
The radio is in standby, or its network interface is off. A K4 in standby drops
its Ethernet link — if you sent `PS0;` earlier, this is expected and you will
need the front-panel button. Otherwise check **MENU → Network** on the radio.

**`cannot reach the radio ... timed out`**
Wrong IP, or a VLAN/firewall between the bridge and the radio. Check with
`ping 192.168.1.50` and `nc -vz 192.168.1.50 9200`.

**`radio did not answer 'PS' within 3s`**
Something is listening on 9200 but it is not talking CAT. Confirm you have the
right port, and that another program (a logger, WSJT-X, a rig-control daemon)
has not already claimed the radio's single control connection.

**The address changed.** Give the K4 a DHCP reservation. This is the most common
cause of a setup that worked last week and does not today.

## 1. The bridge itself

```bash
sudo systemctl status k4-bridge
journalctl -u k4-bridge -n 50 --no-pager
```

**`configuration error: iot transport is missing: endpoint, thing_name, ...`**
`bridge.ini` was never filled in, or the service is reading a different copy
than you edited. The log line right after startup says which file it loaded.

**`the iot transport needs the AWS IoT SDK: pip install awsiotsdk`**
The virtualenv is missing its dependency:
`sudo /opt/k4echo/venv/bin/pip install -r bridge/requirements.txt`

**`configuration error: the webhook secret is too short`**
Use `python3 tools/make_secret.py`. Minimum 32 characters.

**Nothing in the log at all.** The unit runs the venv's Python with
`WorkingDirectory=/opt/k4echo`; if you moved the code, `python -m k4echo.bridge`
will not find the package.

## 2. The transport

### IoT

**Bridge logs `connecting to AWS IoT ...` and then nothing.**
Certificate, key, or policy problem. The bridge cannot always tell the
difference — check IoT Core → Security → Certificates and confirm the cert is
**Active**, has the policy attached, and has the thing attached.

**Connects, but commands never arrive.** Topic mismatch. The bridge logs the
topic it subscribed to at startup; the Lambda publishes to `K4_IOT_TOPIC` or
`k4echo/<K4_IOT_THING_NAME>/cmd`. They must match exactly. Publish by hand from
the IoT MQTT test client to see which side is wrong.

**Status says "the home bridge has not reported in for a while."** The bridge is
not running, or has lost its connection. Its shadow updates every
`shadow_interval` seconds (default 300) and the Lambda rejects readings older
than `K4_SHADOW_MAX_AGE` (default 300). If you raise one, raise the other.

**`could not read the radio's status ... ResourceNotFound`** The shadow does not
exist yet — the bridge has never successfully reported. Start it and retry.

### Webhook

Test each side separately:

```bash
# from inside the LAN
K4_BRIDGE_SECRET='...' python3 tools/post_command.py \
    --url http://192.168.1.9:8443/command power_query

# from outside (phone hotspot)
K4_BRIDGE_SECRET='...' python3 tools/post_command.py \
    --url http://yourhost.example.com:8443/command power_query
```

**Works inside, fails outside.** Port forward, or your ISP. Check the router
rule points at the bridge's *current* LAN IP, and that your public address has
not changed. Some ISPs use CGNAT, in which case inbound forwarding is impossible
and you should use the IoT transport.

**`HTTP 401: unauthorized`.** The secrets differ. Compare the bridge's
`[webhook] secret` against the Lambda's `K4_BRIDGE_SECRET` character for
character — a trailing newline from a copy-paste is the usual culprit. It is
also worth checking the clocks: requests more than `max_skew` (300s) out of step
are refused. `timedatectl` on the Pi.

**`HTTP 401` on the second identical request only.** Working as intended — that
is the replay guard. Each request carries a fresh nonce; `post_command.py`
generates one per run, so re-running the tool is fine, but replaying a captured
request is not.

**`HTTP 404`.** The URL path does not match `[webhook] path` (default
`/command`).

**`HTTP 400: raw CAT strings are disabled on this bridge`.** Something sent
`{"cat": "..."}` rather than a command name. That is the allow-list doing its
job; see [SECURITY.md](SECURITY.md).

## 3. The Lambda

CloudWatch Logs → `/aws/lambda/k4-echo-control`.

**`environment variable K4_BRIDGE_URL is not set`**
`update-function-configuration --environment` replaces the entire variable set.
Read the current values back first:

```bash
aws lambda get-function-configuration \
    --function-name k4-echo-control --query Environment.Variables
```

**`K4_SKILL_ID is not set -- any skill can invoke this function`**
A warning, not a failure. Set it.

**`This request did not come from the K four control skill.`**
`K4_SKILL_ID` does not match the skill actually calling. Copy the ID from the
developer console's Endpoint page.

**Alexa says "there was a problem with the requested skill's response."**
The Lambda errored, timed out, or is not returning valid JSON. The log will say
which. If it is a timeout, the function's timeout should be 8 seconds and
`K4_BRIDGE_TIMEOUT` should be below that.

**No log group at all.** The skill is not reaching the Lambda — usually the
wrong ARN in the Endpoint page, a missing `add-permission`, or a Lambda in the
wrong region. `en-US` skills require **us-east-1**.

## 4. The skill

**"I don't know that one" / Alexa opens something else.** The model was not
built. Developer console → Build Model, and wait for it to finish.

**Alexa hears the wrong intent.** Add the phrasing you actually used to that
intent's `samples` in the interaction model and rebuild. The shipped samples
cover common phrasings, not every one.

**The Test tab works but the Echo does not.** Almost always different Amazon
accounts. The developer account that owns the skill must be the same account the
Echo is registered to. Check Alexa app → More → Skills & Games → Your Skills →
Dev.

## 5. Behaviour that looks like a bug but is not

**"Turn on the radio" does nothing.** Expected. A K4 in standby has no network
interface, so nothing receives `PS1;`. See the note in the [README](../README.md).

**Status says "most likely in standby" instead of an error.** Deliberate — for
this radio, unreachable and off are the same observation.

**Power-off replies "command sent" rather than confirming.** You are on the IoT
transport, which is fire-and-forget. Confirm in the bridge log, or use the
webhook transport if you want the radio's own acknowledgement in the response.

## Still stuck?

Run the test suite — if it passes, the code is fine and the problem is
configuration:

```bash
python3 -m pytest tests -q
```

Turn up the logging on both ends: `K4_LOG_LEVEL=DEBUG` on the Lambda, and
`log_level = DEBUG` in `bridge.ini`.
