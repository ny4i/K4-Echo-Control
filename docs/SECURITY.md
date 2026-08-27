# Security notes

The worst realistic outcome here is someone taking your K4 off the air at will,
or worse, driving it into a state you did not intend. That is a small blast
radius compared to most internet-exposed things, but it is your radio, and the
mitigations are cheap. This is what the design does about it.

## The thing not to do

The obvious shortcut is to forward TCP 9200 on the router straight to the K4.
It works in five minutes, and it is the one configuration this project will not
help you build.

The K4's control port has **no authentication of any kind**. Anything that can
open a socket to it can send any CAT command: change frequency, key the
transmitter, alter menu settings, power the radio down mid-QSO. Port 9200 is a
port scanner's afternoon — internet-wide scans of the full port range complete
in hours, and there are public search engines you can query for the results.
"Nobody knows my address" stops being true within about a day of opening it.

The bridge exists so that the thing exposed to the internet is a program you
control, which authenticates its callers and will only do three specific things.

## What the layers actually protect

| Layer | Stops |
|---|---|
| `K4_SKILL_ID` check + Lambda `--event-source-token` | Someone else's Alexa skill invoking your function, and anyone with the ARN invoking it directly |
| IoT Core mutual TLS | Any client without your bridge's private key |
| IoT policy scoped to one thing's topics | A stolen certificate reaching anything else in your AWS account |
| HMAC signature (webhook) | Forged commands from anyone who finds the open port |
| Timestamp window + nonce cache (webhook) | Replaying a captured request |
| Command allow-list on the bridge | A leaked secret becoming arbitrary control of the radio |

That last row is the one worth dwelling on. The Lambda sends the *name*
`power_off`, never the string `PS0;`. The bridge resolves names against its own
copy of the catalog and refuses anything else. So the worst an attacker with a
valid signature can do is turn your radio on and off — not retune it, not key
it, not walk its menus. Raw CAT pass-through exists (`allow_raw_cat`) but is off
by default, and turning it on is what converts a nuisance into a real problem.

## If you use the webhook transport

**Forward to the bridge, never to the radio.** The whole point is that the
bridge is what faces the internet.

**Use the generated secret.** `tools/make_secret.py` produces 48 bytes of
`secrets.token_urlsafe`. The config loader refuses anything shorter than 32
characters, because a guessable secret makes every other layer decorative.

**Plain HTTP is a deliberate default, not an oversight.** Requests are signed,
so an eavesdropper cannot forge or replay one. What they *can* do is see that
you sent `power_off` — which is not a secret worth a certificate-renewal
treadmill on a Raspberry Pi. If you want TLS anyway, set `tls_cert` and
`tls_key`; a Let's Encrypt certificate for your dynamic-DNS hostname works.

**Pick a non-obvious external port.** Not security, but it keeps you out of the
default scan lists and cuts the log noise considerably.

**Rate limiting is not built in.** The bridge is single-purpose and the command
set is harmless-by-construction, but if your router can rate-limit the forwarded
port, do it.

## Handling the IoT private key

`provision_iot.sh` writes a certificate and private key to a local directory.
That key is the bridge's entire identity.

- Move it to the bridge over a trusted channel (`scp`), never email or chat.
- On the bridge: owned by root, group-readable by the service account, `0640`.
- Delete the local copy afterwards. The script's closing message says so.
- Committing `certs/` to git is the classic mistake. `.gitignore` covers it,
  but check before you push.

To revoke a compromised key, deactivate the certificate in the IoT console (or
`aws iot update-certificate --new-status INACTIVE`) and re-run the provisioning
script. Nothing else needs to change.

## What the bridge is allowed to do

The systemd unit runs it as a dedicated unprivileged `k4bridge` user with a
read-only filesystem view, no new privileges, no device access, and network
access restricted to IPv4/IPv6 sockets. It needs exactly two outbound
connections — one to the radio, one to AWS — and can make no use of anything
else.

On Windows the scheduled task runs as `NETWORK SERVICE` at `Limited` run level,
which is the nearest equivalent.

## Things this design does not defend against

Worth being explicit, so you can judge the residual risk yourself:

- **Anyone who can talk to your Echo can control your radio.** Voice is the
  authentication. A houseguest, or someone shouting through an open window, can
  say the phrase. If that matters, Alexa supports a per-skill voice PIN under
  the skill's permissions, or you can drop the `PowerOnIntent` and keep this
  strictly one-way.
- **Anyone on your LAN can already control the radio directly**, with or without
  this project. Port 9200 is open to your whole local network by design.
- **AWS can see the command traffic.** It is `{"command":"power_off"}`. If your
  threat model includes your cloud provider, this is not the project for you.
- **A compromised bridge machine** owns the radio completely. It is on the LAN.

## Reporting

Found something wrong with this? Open an issue at
<https://github.com/ny4i/K4-Echo-Control/issues>.
