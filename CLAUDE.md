# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Voice control for an Elecraft K4 transceiver from an Amazon Echo. An Alexa custom
skill invokes an AWS Lambda, which hands a *symbolic command name* to a bridge
daemon running on the same LAN as the radio; only the bridge opens a TCP socket to
the K4 on port 9200 and speaks CAT (`PS0;` standby, `PS1;` on, `PS;` query).

Pure standard library except `awsiotsdk` on the bridge (only for `transport=iot`).
No packaging (`setup.py`/`pyproject.toml`), no linter config, no CI.

## Commands

```bash
python3 -m pytest tests -q                    # full suite (78 tests, no AWS/radio needed)
python3 -m pytest tests/test_lambda.py -q     # one file
python3 -m pytest tests/test_lambda.py::test_name -q

./tools/build_lambda.sh                       # -> dist/k4-echo-lambda.zip

python3 tools/fake_k4.py --port 9200          # simulated radio, prints CAT it receives
python3 tools/k4cli.py --host <ip> status     # talk to the radio directly, no AWS
K4_BRIDGE_SECRET=... python3 tools/post_command.py --url <bridge-url> power_off

python -m k4echo.bridge --config bridge.ini --selftest       # query radio, print shadow doc
python -m k4echo.bridge --config bridge.ini --send power_off # run one command locally
python -m k4echo.bridge --list-commands                      # the allow-list
```

`tests/conftest.py` puts the repo root, `lambda/`, and `tests/` on `sys.path`, so
tests import `lambda_function`, `k4echo.*`, and `fake_k4` with no install step.

## Architecture

`k4echo/` is shared by both halves so they cannot drift. The split matters when
editing:

| Module | Lambda | Bridge |
|---|---|---|
| `commands.py`, `signing.py`, `alexa.py`, `transports.py` | ✅ | ✅ |
| `radio.py`, `config.py`, `bridge.py` | ❌ | ✅ |

`tools/build_lambda.sh` ships only the four Lambda-side modules
(`LAMBDA_MODULES` in that script). **Adding a Lambda-side import of `radio.py`,
`config.py`, or `bridge.py` breaks the deployed zip** — the script's import check
will catch it, but the fix is to keep that dependency out, not to widen the list.

### The security model — do not weaken it accidentally

- The Lambda never sends a raw CAT string. It sends a command *name*
  (`power_off`), and `CommandExecutor.resolve()` in `bridge.py` looks it up in
  `commands.CATALOG`. A leaked signing secret therefore buys an attacker only the
  three verbs in the catalog, not arbitrary CAT. Raw CAT is gated behind the
  off-by-default `allow_raw_cat` config flag.
- Webhook requests are HMAC-SHA256 signed over `v1:{ts}:{nonce}:{body}` with a
  clock-skew window and an in-memory `ReplayGuard` nonce cache. Any change to
  `signing_string()` is a wire-format break: Lambda and bridge must be redeployed
  together.
- `K4_SKILL_ID` gates the Lambda; without it any skill that learns the ARN can
  drive the radio.

### Two transports

`K4_TRANSPORT` selects the Lambda side (`transports.build_transport()`);
`[bridge] transport` selects the bridge side. They must match.

- **`iot`** (default): bridge holds an outbound MQTT connection to AWS IoT Core,
  subscribed to `k4echo/<thing>/cmd`, replying on `.../result`. No inbound port.
  Power commands are **fire-and-forget** — the Lambda cannot see the radio's
  reply, so `_speech_for()` appends "command sent". Status queries are answered
  from the IoT **device shadow**, which the bridge refreshes every
  `shadow_interval` seconds and after every command; the Lambda refuses a reading
  older than `K4_SHADOW_MAX_AGE`.
- **`webhook`**: Lambda POSTs to a forwarded port; synchronous, so the radio's
  real reply comes back in the response.

### The standby asymmetry

A K4 in standby drops its Ethernet interface, so an unreachable radio is the
*expected* answer to a status query, not an error. `CommandExecutor.execute()`
returns `ok: True, radio_reachable: False` when a query cannot connect, and the
Lambda speaks "most likely in standby". Don't turn that path into a failure.
`PS1;` over the network usually won't work for the same reason.

### Adding a command

1. Add a `K4Command` in `k4echo/commands.py` and register it in `CATALOG`.
2. Add the intent + sample utterances to
   `skill/skill-package/interactionModels/custom/en-US.json`.
3. Map intent → command in `INTENT_TO_COMMAND` in `lambda/lambda_function.py`.
4. Both halves must be redeployed — the bridge resolves names against its own
   copy of the catalog, so an unknown name is rejected there.

Speech strings are spoken by Alexa: write "K four", not "K4".

## Configuration

Bridge config resolves **environment variable → INI file → dataclass default**
(`config.py::_get`). Everything in `bridge.ini` has a `K4_*` env override, so
secrets and cert paths can live in a systemd `EnvironmentFile` instead of the
file. Lambda config is env-only; the full table is in `docs/SETUP.md`.

`.gitignore` already excludes `bridge.ini`, `bridge.env`, `certs/`, and `*.pem*`.

## Docs

`docs/SETUP.md` is the end-to-end install (AWS + Alexa console steps, env var
table, costs), `docs/SECURITY.md` the threat model, `docs/TROUBLESHOOTING.md`
symptom-by-symptom. Changes to transports, config, or the command catalog usually
need a matching edit in `docs/SETUP.md` and the README.
