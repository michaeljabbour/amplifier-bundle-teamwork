# Connecting your workspace to Teamwork

For a teammate joining a shared project for the first time. About five minutes.

Everything here is drawn from the shipped code and the README. Where something is
known to be unfinished, it says so — so you don't spend an afternoon fighting it.

---

## 1. Add the bundle

```sh
amplifier bundle add "git+https://github.com/michaeljabbour/amplifier-bundle-teamwork@main#subdirectory=behaviors/teamwork.yaml" --app
```

`--app` adds Teamwork to **new** sessions while leaving your own bundle and model
provider exactly as they are. Amplifier installs the packaged modules itself — no
`pip install`, no clone, no separate Python command.

**Installing does nothing on its own.** It reads no project context and shares nothing.
Sharing begins only after you connect a session, and only for sessions you connect.

> The URL must point at the `.yaml` file exactly as written. A GitHub web page URL or a
> zip download produces `Unknown bundle format`.

## 2. Connect a session

1. Start a **new** Amplifier session as you normally would. If you have a checkout with
   a GitHub `origin` remote, start it there — Teamwork can then resolve which project
   you mean without you typing an ID.
2. Say: **"Connect this session to Teamwork."**
3. Amplifier opens a small private form in your browser. Fill in **one** of:

   | method | what to do |
   |---|---|
   | **Portal credential** *(use this)* | In the portal: **Account menu → Harnesses & agents → mint a credential**. It is shown **once**. Paste it into *Credential from the portal*, with the exact project ID. |
   | Microsoft sign-in | Offered only when the service advertises support. **Not live yet** — server-side work pending. If you don't see it, that's why. |
   | Private member code | Fallback. Your name/email plus your private member code. |

4. Return to Amplifier. **Sharing starts with your next prompt.** The connect request and
   everything earlier in the conversation are not published retroactively.

### On a remote or headless host

Supported as of [#38](https://github.com/michaeljabbour/amplifier-bundle-teamwork/pull/38).
The terminal prints the one-time form URL **and an SSH forwarding command**. Run that
command on your own computer, substituting `USER@REMOTE_HOST`, keep the tunnel open, and
open **the exact same loopback URL** in your local browser.

Both ends must use the printed port — the form validates its `Host` and `Origin` and will
refuse a mismatch. The command binds your local end to `127.0.0.1` even if your SSH config
sets `GatewayPorts`, and the server stays bound to loopback. Do not expose it on the
network, and do not forward the one-time URL to anyone: it carries a private code.

This is an **interactive** enrollment path. It is not unattended CI authorization.

### If the form doesn't appear

Amplifier prints a one-time address in the terminal — open that. It is also written to
`~/.config/amplifier-teamwork/native/pending-form-url.txt` (mode 0600) while the form is
open, and removed when it closes. Treat that address like a password.

The form closes after **15 minutes of inactivity**; typing counts as activity, so the
window measures idleness rather than total time.

**Never paste your member code or credential into chat** — not into a channel, not into
Amplifier's prompt.

## 3. Check it worked

Ask your session:

```
What Teamwork project am I connected to, and what arrived from it?
```

When project records reach a session, Amplifier prints one attributed line each — the kind
of record, who it came from, and its title — before the turn runs. Those lines are the
confirmation.

Seeing nothing is not necessarily a fault: if nobody has published to the project yet,
there is nothing to deliver.

---

## What is actually shared

Worth reading once, because "it shares your session" is not precise enough to consent to.

**Shared:**

- your visible prompts and the final responses — what you type and what you see
- session metadata and stable correlation IDs
- receipts confirming a bounded project-context excerpt was accepted before a turn
- a snapshot of your local work queue, when this machine runs one: its status, when it was
  last read, how many items are ready, the tracker's name, and — when it could not be read
  — that tracker's own explanation, which is the one field this project does not author
- title, status and a locator for any work item you publish **deliberately**

**Not shared:** tool output, internal reasoning, file contents, your local task descriptions
and acceptance criteria, or anything from a session you have not connected.

**Redaction is pattern-based.** Credential-shaped strings are stripped before anything is
stored or sent — that is a pattern match, **not a guarantee**. Do not connect a session
that contains secrets or prose you do not intend to share.

One project at a time. Child sessions — anything Amplifier delegates to a sub-agent —
never receive the connection or the sharing hook.

---

## Day to day

| you want to | do this |
|---|---|
| use it in a new session | already there; `--app` applies to every new session |
| stop sharing | start a session and don't connect — nothing is shared unless you do |
| switch project | ask to connect again; the form reopens |
| pick up bundle updates | `amplifier bundle update teamwork -y`, then start a **new** session |
| say you're blocked on a person | ask your session to declare it is waiting, and what for |

A running session keeps the tool set it started with, so after an update the new behaviour
appears only in sessions started afterwards.

---

## Known rough edges

- **Microsoft sign-in is not live.** Pending server-side work. Use the portal credential.
- **A killed session can leave a stale "waiting" badge** on the shared project until that
  session's next turn. There is no timeout. This is a deliberate trade-off, documented in
  [`docs/architecture/ARCHITECTURE.md`](architecture/ARCHITECTURE.md) — not a bug you found.

---

## If you get stuck

Say what you saw, exactly, and which of the three enrollment methods you used. Those two
facts resolve most of it. The error messages here are written to be quotable — quoting one
beats describing it.

Do not paste the credential, the member code, or the one-time form address.
