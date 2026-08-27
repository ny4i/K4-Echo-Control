# K4-Echo-Control

Voice control for an [Elecraft K4](https://elecraft.com) from an Amazon Echo.

Say **"Alexa, tell radio control to turn off the radio"** and the K4 receives
`PS0;` on its network control port (TCP 9200).

The Echo cannot reach your LAN, so the command travels:

```
   "turn off the radio"
            |
        [ Echo ]
            |  Alexa Voice Service
            v
   [ Alexa custom skill ]
            |
            v
   [ AWS Lambda ]  ......  maps the intent to a command name.
            |               Never opens a socket to the radio itself.
            v
   ~~~~~~~~~~~~~~~~~~~~  the hop across your firewall
            v
   [ bridge on your LAN ]  ......  Raspberry Pi, Windows box, NAS, anything
            |                      that stays on and speaks Python.
            | TCP 9200
            v
      [ Elecraft K4 ]  <--  PS0;
```

## Two ways across the firewall

The bridge supports both; pick one at install time with a single config line.

| | `iot` **(recommended)** | `webhook` |
|---|---|---|
| Router changes | **none** | forward one port |
| Direction | bridge dials out to AWS IoT Core | AWS dials in to your house |
| Exposed to the internet | nothing | the bridge's port |
| Command latency | ~1s | ~0.5s |
| Status queries | from a cached shadow | live from the radio |
| AWS cost | pennies a year on the free tier | none |

`iot` is the default because it gets the job done **without opening any port at
all** — the bridge holds an outbound TLS connection to AWS IoT Core and the
Lambda publishes to it. If you would rather forward a port, `webhook` is fully
supported and every request is HMAC-signed and replay-protected.

What is *not* supported by design: forwarding port 9200 straight to the radio.
See [docs/SECURITY.md](docs/SECURITY.md) for why.

## What you can say

| Phrase | CAT sent |
|---|---|
| "Alexa, tell radio control to turn off the radio" | `PS0;` |
| "Alexa, tell radio control to turn on the radio" | `PS1;` |
| "Alexa, ask radio control if the radio is on" | `PS;` |

`PS0;` — the command this project was built for — puts the K4 in standby.

> **Turning the radio back on over the network usually will not work.** A K4 in
> standby takes its Ethernet interface down with it, so there is nothing left
> listening on port 9200. `PS1;` is included because it costs nothing and works
> on stations that keep the network alive, but plan on `PS0;` being a one-way
> trip until you press the front-panel button. The status query is written with
> this in mind: an unreachable radio is reported as "most likely in standby"
> rather than as an error.

## Quick start

```bash
git clone https://github.com/ny4i/K4-Echo-Control.git
cd K4-Echo-Control

# 1. Prove you can reach the radio at all, before any AWS work.
python3 tools/k4cli.py --host 192.168.1.50 status

# 2. Install the bridge on the machine that stays on.
sudo ./bridge/install-linux.sh            # or bridge\windows\install-windows.ps1

# 3. Build the Lambda package.
./tools/build_lambda.sh
```

Then follow [docs/SETUP.md](docs/SETUP.md), which walks through the AWS and
Alexa developer console steps end to end.

No radio yet? `python3 tools/fake_k4.py --port 9200` stands in for one and
prints every CAT string it receives, so you can test the whole chain first.

## Layout

```
k4echo/            code shared by both halves, so they cannot drift
  commands.py        the allow-list: power_off -> "PS0;"
  signing.py         HMAC request signing for the webhook transport
  radio.py           the TCP client that talks CAT to the K4
  alexa.py           Alexa request parsing and response building
  transports.py      how the Lambda reaches the bridge (Lambda side)
  config.py          bridge configuration
  bridge.py          the daemon that runs on your LAN

lambda/            the Lambda entry point
skill/             Alexa interaction model and manifest (ASK CLI layout)
bridge/            config example, systemd unit, installers
tools/             build, provisioning, and test utilities
docs/              setup, security, troubleshooting
tests/             78 tests, no AWS or radio required
```

## Tests

```bash
pip install pytest
python3 -m pytest tests -q
```

The suite stands up a simulated K4 and the real bridge HTTP server, then drives
a genuine Alexa `PowerOffIntent` event through the Lambda handler and asserts
that `PS0;` arrives at the radio. AWS is faked; nothing else is.

## License

GPL-3.0. See [LICENSE](LICENSE).
