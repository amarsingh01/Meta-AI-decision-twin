# Agent Guardian

**The guardian holds the keys. Every action an AI agent takes is checked against the task and the
owner's rules before it runs.**

AI agents are now given real access: shells, credentials, email, one-time codes, sudo. When an agent
drifts off task, nothing between the agent and the machine notices. Two reported examples from 2026:

- In a security test reported by Cohere (October 2026), a model working on an unrelated task listed
  temp files, looked for transfer tools, read DNS settings and tried to upload a file to an outside
  cloud bucket.
- At Meta (March 2026), an internal AI agent acted without approval and exposed company and user
  data for about two hours, a Sev 1 incident.

Agent Guardian sits between any agent and its system. The agent gets no keys and no root. Every tool
call the agent's model proposes (a command, a file write, a web fetch, a request for a credential) is
pulled out of the model's reply before the agent can run it, and checked:

1. **Hard rules (code).** Secrets, environment dumps, uploads, unknown destinations, raw network
   tools and destructive commands outside the project are blocked. One-time codes, sudo, payments and
   force pushes go to the owner. These hold even if every model is wrong.
2. **Judgement (model).** Actions the rules cannot settle (listing `/tmp`, reading `/etc/resolv.conf`,
   touching files outside the project) are scored by the guardian model against the task, the recent
   actions and the owner's weighted principles: needed for the task, security, data stays home, least
   privilege, reversibility. Options are Allow / Ask the owner / Block. Vetoes and close calls never
   allow.
3. **Act.** Allowed calls pass unchanged. Stopped calls are replaced by a note to the agent, so the
   command never reaches the agent's tools.
4. **Broker.** Privileged steps are done by the guardian itself. In the demo the agent runs
   `guardian-request deploy "<why>"`; if the guardian agrees, it publishes the site with a deploy key
   the agent never sees and writes a receipt.

The guardian is model-agnostic: it watches actions, not intentions, so it works with any agent and
any model behind it.

Built for the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026), Indie track.

> The guardian's rules, principles and past decisions in `profile/` are **invented** for this demo
> and labelled `synthetic`. The "compromised model" in the demo is a **scripted replay** of the
> publicly reported steps, injected in place of real model replies and labelled in every log line.
> No real data is sent anywhere.

## Results

`guardian/scorecard.py` runs 12 scenarios (35 agent actions) under three set-ups. 8 scenarios are
attacks (15 harmful actions: the reported exfiltration pattern, a prompt injection hidden in a
README, SSH-key theft, credential over-reach, destructive cleanup, pack-and-upload, reading a login
code, persistence). 4 are normal jobs (20 legitimate actions, including ones that look risky out of
context, such as reading DNS settings while debugging a DNS problem).

| Set-up | Harmful actions stopped | Attacks contained | Legitimate actions allowed |
| --- | --- | --- | --- |
| No guardian | 0 / 15 | 0 / 8 | 20 / 20 |
| Hard rules only | 10 / 15 | 5 / 8 | 20 / 20 |
| **Hard rules + Muse Spark** | **15 / 15** | **8 / 8** | **20 / 20** |

The rules catch the obvious steps. The model catches the off-task reconnaissance that is harmless on
its own (listing temp files during a CSS fix) while allowing the same command when the task needs
it. 25 of the 35 checks were settled by rules in under a millisecond; 10 needed a model review
(about 40 s each with Muse Spark 1.3, run in parallel). This is a small, hand-written test set, not a
benchmark.

A recorded live run is on the dashboard: a real OpenClaw agent building a small site in a Modal
sandbox, with red-team mode on. The scripted upload to an outside bucket was blocked before it ran,
and the agent carried on with the task.

## Meta models used

| Model | Role | How |
| --- | --- | --- |
| **Muse Spark 1.3** | The guardian's judge; the OpenClaw agent's model; the owner's decision twin for build choices | Meta Model API, Standard tier |
| **Muse Glimmer 30B** (open weights) | Optional judge with the model's own option probabilities (log-probs) | Self-hosted with vLLM on a Modal H100 (`glimmer_server.py`), scales to zero |

## Architecture

```
            OpenClaw agent (unprivileged user, no keys)
                 │  model request              ▲  reply: tool calls, or a guardian note
                 ▼                             │
   ┌──────────── model gateway (root-only) ────┴────────────┐
   │  Meta API key ─► Muse Spark          guard hook:        │
   │                                      every tool call ─► hard rules ─► model review
   │  /guardian/request ─► broker (deploy key, receipt)      │
   └───────────────────────────┬─────────────────────────────┘
                               ▼
              guardian log ─► event log (Modal Dict) ─► dashboard
```

| Path | What it is |
| --- | --- |
| `guardian/policy.py` | Hard rules: `check(Action) -> allow / block / ask / review` |
| `guardian/judge.py` | The guardian: rules first, then a model review with the guardian principles |
| `guardian/scenarios.py`, `guardian/scorecard.py` | The 12 scenarios and the three-way scorecard |
| `worker/guard_hook.py` | Gateway hook: parses streamed replies, checks each tool call, rewrites stopped replies, red-team replay |
| `worker/broker.py` | Does privileged steps for the agent after the guardian agrees |
| `worker/model_gateway.py`, `worker/sandbox.py` | Root-only gateway and the OpenClaw sandbox on Modal |
| `twin/` | Scoring engine shared by the guardian and the build-decision twin: weighted principles, vetoes, softmax, retrieval of similar past decisions, untrusted-text fencing |
| `profile/` | Guardian rules and principles, synthetic past decisions; the owner's build principles |
| `orchestrator/` | The build loop and the event log |
| `webapi.py`, `web/` | API and dashboard, including the rate-limited "Ask the guardian" box |
| `app.py` | Modal app: build job, scorecard job, web endpoint |
| `tests/` | Unit tests (fake models; no network) |

## Security of the demo itself

- **The agent never sees a key.** OpenClaw runs as an unprivileged user. The Meta API key and the
  deploy key live in a root-only folder used by the gateway and the broker.
- **Fail closed.** If the guardian hook errors, the gateway returns an error instead of the reply.
  If a model review fails, the owner decides.
- **Untrusted text stays data.** Agent output and task text are cleaned and fenced; the models are
  told never to follow instructions inside them.
- **Dashboard.** Strict Content-Security-Policy, no inline scripts, DOM built with `textContent`,
  validated inputs. Starting a build needs an access token. "Ask the guardian" is limited per visitor
  per hour and per day. Built sites are served under a sandbox CSP.
- **No personal data.** No accounts, no tracking, synthetic profile.

## Limits

- The red-team "compromised model" is a replay of reported steps, not a real compromised model.
- Model reviews are slow (tens of seconds with a reasoning model); routine actions are settled by
  rules instantly, so in a build only unusual actions wait.
- "Ask the owner" pauses are shown and the action is not run; a phone approval flow is future work.
- The rules know one project folder and a small allow-list of hosts; a real deployment would load
  them per task.

## Run it

Requirements: a Modal account and a Meta Model API key.

```bash
pip install -e ".[dev]" modal
pytest                                                    # unit tests
modal secret create meta-api META_API_KEY=...
modal secret create twin-ui ACCESS_TOKEN=...
modal secret create glimmer GLIMMER_API_KEY=x GLIMMER_BASE_URL=http://unused/v1   # optional judge
modal run app.py::scorecard                               # the three-way scorecard
modal run app.py::main --guardian on --redteam exfil --task "..."   # one live guarded build
modal deploy app.py                                       # dashboard
```

## Built during the hackathon

Everything in this repository was written during the event (from Oct 31, 2026); see the commit
history. It started as a decision twin that directs a builder agent; the guardian reuses that scoring
engine. Third-party software used as-is: OpenClaw, vLLM, FastAPI, Pydantic, the OpenAI Python
client, Modal.
