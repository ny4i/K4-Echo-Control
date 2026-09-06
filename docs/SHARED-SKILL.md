# Running one skill for several operators

By default this project is private: one skill, one Lambda, one radio. Everyone
who wants it builds the whole stack, which means every one of them needs an AWS
account. That is a real barrier — most people who want to say "Alexa, turn off
the radio" do not want to create an IAM role first.

This document covers the alternative: **you** host the cloud half, and your
friends run nothing but a bridge on their own network. No AWS account, no
console, no Lambda. They accept an email invitation, install the bridge, and
speak one pairing sentence.

- [What changes](#what-changes)
- [How a request finds the right radio](#how-a-request-finds-the-right-radio)
- [Setting up the shared side](#setting-up-the-shared-side)
- [Enrolling an operator](#enrolling-an-operator)
- [What your friend does](#what-your-friend-does)
- [The security trade you are making](#the-security-trade-you-are-making)
- [Limits worth knowing](#limits-worth-knowing)

---

## What changes

Very little, and notably **the bridge does not change at all**. Each bridge
already subscribes to `k4echo/<its-own-thing>/cmd`. All the routing happens in
the Lambda, so an operator's Pi is byte-identical to a private installation.

What is added:

| Piece | Purpose |
|---|---|
| `k4echo/registry.py` | Resolves an Alexa speaker to their bridge |
| `PairIntent` | Lets someone bind their Alexa account by voice |
| `k4echo-pairings` table | `user_id` → `thing_name` |
| `k4echo-codes` table | `code` → `thing_name` |
| `K4_MULTI_TENANT=1` | Turns all of the above on |

With `K4_MULTI_TENANT` unset the Lambda behaves exactly as it always has, down
to ignoring the speaker entirely. That is deliberate: your own radio should not
be able to break while you are experimenting with sharing.

---

## How a request finds the right radio

Alexa puts `session.user.userId` in every request — an opaque string, scoped to
your skill, that identifies a speaker without revealing anything about their
Amazon account. Nothing read it before; now it is the tenant key.

```
"Alexa, tell radio control to turn off the radio"
        |
        v
   session.user.userId = amzn1.ask.account.AF3...
        |
        v
   k4echo-pairings  ->  thing_name = k4-w1abc-bridge
        |
        v
   publish to k4echo/k4-w1abc-bridge/cmd
```

If the lookup finds nothing, the Lambda says *"This skill isn't paired with a
radio yet"* and sends nothing at all. **There is no default and no fallback.**
That is the single most important property in this design — see below.

---

## Setting up the shared side

Everything here happens once, in your AWS account.

**1. Create the tables.**

```bash
./tools/provision_registry.sh
```

**2. Widen the execution role.** Use
[tools/lambda-iam-policy-shared.json](../tools/lambda-iam-policy-shared.json)
with `REGION` and `ACCOUNT` substituted, in place of the private policy.

**3. Turn it on.** Remember that `--environment` replaces the whole set, so
include what is already there:

```bash
aws lambda update-function-configuration --region us-east-1 --function-name k4-echo-control --environment 'Variables={K4_TRANSPORT=iot,K4_IOT_ENDPOINT=<your-ats-endpoint>,K4_SKILL_ID=<your-skill-id>,K4_MULTI_TENANT=1}'
```

`K4_IOT_THING_NAME` is no longer used when `K4_MULTI_TENANT` is set — every
request resolves its own target — but leaving it set does no harm.

**4. Redeploy the Lambda**, since `registry.py` is a new module:

```bash
./tools/build_lambda.sh
aws lambda update-function-code --region us-east-1 --function-name k4-echo-control --zip-file fileb://dist/k4-echo-lambda.zip
```

**5. Rebuild the interaction model.** The `PairIntent` is new, so paste the
updated `en-US.json` into the JSON Editor and **Build Model** again. Without
this, "pair four eight two nine one three" will not be understood.

**6. Turn on beta testing.** Developer console → **Distribution**, fill in the
publishing information (the privacy and terms URLs are required here), then
→ **Availability** → **Beta Test**. Add your friends by the email address on
their Amazon account and they get an invitation link.

Beta testing takes up to 500 testers and **skips certification entirely**,
which matters: a certification reviewer has no K4 to test against, and "the
reviewer could not exercise the skill" is a routine rejection.

---

## Enrolling an operator

One command per person:

```bash
./tools/enroll_operator.sh k4-w1abc-bridge
```

It provisions their thing, certificate and scoped policy, then registers a
random six-digit pairing code and prints it. Send them the certificate
directory over a channel you trust, delete your copy, and tell them the code.

---

## What your friend does

1. Accept the beta invitation email.
2. Install the bridge — `sudo ./bridge/install-linux.sh`, or the Windows
   installer. They need a Raspberry Pi or any always-on machine on the same
   network as the radio, running Python 3.9 or newer.
3. Drop the certificates you sent into `/etc/k4echo/certs` and put the `[iot]`
   block you sent into `/etc/k4echo/bridge.ini`.
4. `sudo systemctl enable --now k4-bridge`
5. Say once: **"Alexa, ask radio control to pair four eight two nine one three."**

From then on it is theirs: *"Alexa, tell radio control to turn off the radio."*

---

## The security trade you are making

Be clear-eyed about this before you invite anyone.

A private deployment's Lambda holds an IAM policy naming exactly one thing. It
*cannot* reach another radio, whatever the code does. A shared deployment's
Lambda must be allowed to publish to every operator's topic, so that guarantee
moves out of IAM and into a DynamoDB lookup that you wrote.

**A bug in `resolve_thing()` does not drop a menu. It keys a stranger's
transmitter, possibly while they are not home.**

Three rules follow, and they are why the code is shaped as it is:

- **Never default.** An unresolved speaker is refused. `registry.py` has no
  fallback path, and `tests/test_multi_tenant.py` asserts that an unpaired
  speaker causes *no transport to be built at all* — not merely a spoken error.
- **Log every resolution.** Each lookup records a speaker fingerprint and the
  thing it chose, so a misroute is discoverable afterwards instead of
  hypothetical. The fingerprint is a truncated SHA-256, so the logs stay
  correlatable without storing account identifiers.
- **Distinguish "not paired" from "broken".** A missing row means go and pair;
  a malformed row is a fault and raises. Collapsing the two would let a data
  bug look like a routine unpaired user.

You are also now the operator of other people's control plane. Your AWS
credentials, your uptime, and your billing are theirs too. That is a
responsibility, not just a convenience.

---

## Limits worth knowing

**Alexa user ids are not permanent.** Disabling and re-enabling the skill mints
a new one, which looks to the Lambda like a brand-new person. This is why
pairing codes stay valid rather than burning on first use: recovery is one
spoken sentence, not an email to you.

**The webhook transport cannot do this.** It points at one fixed URL, so there
is nothing to route. `build_transport()` raises rather than silently ignoring a
thing name. Shared deployments are IoT-only.

**Pairing codes never start with zero.** Alexa's `AMAZON.NUMBER` slot returns a
number, so `048291` would arrive as `48291` and never match. `enroll_operator.sh`
avoids issuing such codes rather than trying to pad one back, since guessing at
a code is exactly the wrong instinct in the one place that binds a speaker to a
transmitter.

**Certificate distribution is manual.** Fine for ten friends, unpleasant at
fifty. AWS IoT fleet provisioning is the answer at that scale, and is not worth
the complexity below it.

**Costs stay trivial.** DynamoDB on-demand at a few reads per command, IoT Core
messages, and Lambda invocations all sit inside or near the free tier. Ten
operators sending a few dozen commands a month is still pennies a year.
