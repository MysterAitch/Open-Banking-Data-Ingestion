# Runbook: connecting and re-authorising a bank

**Read this if `obdi connections` says a consent is expiring, or you are adding
a bank for the first time.** It assumes you remember nothing.

## Why this exists

UK Open Banking consent lasts about **90 days** and **cannot be renewed by
software**. Refresh tokens keep access tokens alive indefinitely, but the
consent clock runs underneath regardless, and when it expires the connection
stops dead until a human re-authorises at the bank.

So this is a recurring manual chore, roughly quarterly, per bank. There is no
way to automate it away — the whole point of the rule is that a person
reaffirms it.

## Before you start

You need, all already set up if you have done this before:

- `.env` in the repo root, with `TRUELAYER_CLIENT_ID`,
  `TRUELAYER_CLIENT_SECRET_FILE`, `TRUELAYER_REDIRECT_URI` and
  `OBDI_CONNECTION_STORE`. Copy `.env.example` if it is missing.
- The virtual environment. If `.venv` is absent:
  `py -3.14 -m venv .venv && ./.venv/Scripts/python.exe -m pip install -e ".[dev]"`

Check the first with:

```
./.venv/Scripts/python.exe -m obdi.cli connections
```

## The sequence

Three commands. Do them in order, one bank at a time.

### 1. See what needs doing

```
./.venv/Scripts/python.exe -m obdi.cli connections
```

Shows every stored connection and days of consent remaining. Anything marked
`re-authorise soon` or `CONSENT EXPIRED` needs the steps below.

### 2. Get the authorisation link

```
./.venv/Scripts/python.exe scripts/truelayer_probe.py auth-link
```

Prints a URL. Open it, pick **one** bank, and complete the bank's own login and
approval. You will land on TrueLayer's hosted redirect page, which displays a
`code` in the address bar.

Copy the **entire URL** you land on, not just the code.

### 3. Exchange the code and save

```
./.venv/Scripts/python.exe scripts/truelayer_probe.py exchange "<the whole URL>" --save <name>
```

Use the **same `<name>` as before** when re-authorising, so the existing
connection is replaced rather than duplicated. `obdi connections` lists the
names you already use.

The code is single-use and expires within minutes. If you dawdle, go back to
step 2 — nothing is harmed.

### 4. Confirm

```
./.venv/Scripts/python.exe -m obdi.cli connections
```

The consent clock should read about 89 days again.

## Repeat per bank

Each bank is a separate consent with its own clock. Step 2 lets you pick one
bank at a time, so run steps 2 to 4 once per bank that needs it.

## When it goes wrong

**`Token exchange failed` with a redirect URI complaint** — `TRUELAYER_REDIRECT_URI`
must match one registered in the TrueLayer console byte for byte, trailing
slash included. Console changes take up to 15 minutes to propagate.

**`No refresh token in the response`** — the authorisation did not include
`offline_access`. The script requests it; if this appears, the scopes were
altered.

**`refresh token present: NO`** — same cause. Do not save the connection; every
sync would need re-authorising by hand.

**Nothing at all happens in the browser** — the link expires. Re-run step 2.

**You lost the client secret** — generate a new one in the TrueLayer console
under Settings, write it to the file named by `TRUELAYER_CLIENT_SECRET_FILE`,
and start again at step 2. Up to six secrets can exist at once, so adding one
does not break anything currently working.

## Adding a bank for the first time

Identical to the above, but choose a new `<name>` at step 3. Use something you
will recognise in a year — the bank's name is usually right.

Afterwards, if this account is also reachable another way (its own bank API, or
a file export), bind them to one canonical account so the two sources
cross-check instead of double-counting. See "Canonical accounts" in the README.

## The shorter version, once the callback receiver is deployed

Steps 2 and 3 collapse into one. The OAuth redirect happens **in your browser**,
not server to server — the provider never connects to anything of yours, it just
sends the browser somewhere. So the redirect target only has to be reachable by
the machine holding the session. A receiver bound to loopback and fronted by
whatever you already use for private access qualifies, so long as it presents a
certificate the browser trusts and nothing is published to the internet.

With that running:

1. `obdi connections` tells you what needs doing.
2. Open the authorisation link, approve at the bank.
3. The browser lands on the receiver, which exchanges the code and saves the
   connection itself. It shows a confirmation page saying so.

No copying URLs, no second command, nothing to mistype. The bank login stays —
strong customer authentication is the point of the rule and cannot be automated.

To set it up, register that hostname as a redirect URI with the provider and set
it as `TRUELAYER_REDIRECT_URI`, matching byte for byte:

```
https://<your-host>/callback
```

Note the shape this takes in practice: a **publicly resolvable** name whose DNS
answer is a **private address**. The name resolving publicly is what allows a
public CA to issue for it; the address being private is what keeps the service
unreachable from the internet. Both halves are needed, and they are not in
tension.

**A non-standard port is accepted.** `https://your-host:8087/callback` was
registered and used against TrueLayer in August 2026. Worth stating, because
plenty of OAuth providers reject anything but 443 and assuming this one does too
costs you a reverse proxy you do not need. If you publish several services on
one hostname by port, point the redirect straight at yours.

Two caveats worth knowing. Any publicly trusted certificate publishes its
hostname to Certificate Transparency logs, including one issued for a service
only reachable privately — the name leaks, never the traffic — so avoid putting
anything revealing in it. And this does nothing for
webhooks, which would need genuine public ingress; nothing here needs them,
since aggregator data is polled and their own polling runs on a four-to-six hour
cycle regardless.

## What you cannot do

- **Extend consent without re-authorising.** No API call does this.
- **Batch several banks in one authorisation.** One consent per bank.
- **Re-use a code.** Single use, minutes long.
