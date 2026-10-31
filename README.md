# Decision Twin

**A model that decides like its owner, directing a separate AI builder one decision at a time.**

You give the twin a task ("build me a website that…"). The twin does not write code. It hands
the work to an [OpenClaw](https://docs.openclaw.ai) agent running in an isolated sandbox. Every time
the agent reaches a real decision (data source, stack, security approach, layout, whether the work
is finished) it stops and asks. The twin scores every option against its owner's **weighted
principles**, turns the scores into probabilities, vetoes anything that breaks a hard rule, and
sends back the answer with its confidence and reasoning. The whole exchange is shown live in a
web UI.

Built for the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026). Theme: **Build for Your World**.

> The principles and past decisions in `profile/` are **invented** for this demo and labelled
> `synthetic`. The stock website the agent builds in the demo is a demo: it never places trades and
> is not financial advice.

## How a decision is made

For each question from the builder, with options `A…D`:

1. **Score.** The twin model rates every option 0–10 on every principle (security, legal and data
   rights, privacy, cost, code quality, testing, clarity), with the persona's most similar past
   decisions as examples.
2. **Weigh.** `value = Σ weightᵢ × scoreᵢ / 10`.
3. **Veto.** An option that breaks a hard rule, or scores under a principle's veto line, gets 0%.
4. **Principle probabilities.** `softmax(value / T)` over the options that survive.
5. **The model's own probability.** With the open-weight brain (Muse Glimmer on our own vLLM
   server) we read the token log-probabilities at the position where the model writes the option
   letter. Meta's hosted API refuses log-probabilities for reasoning models, so this needs open weights.
6. **Blend and answer.** `final = 0.7 × principles + 0.3 × model`. The top option goes back to the
   builder. If the top option is under 45% or within 8 points of the runner-up, the decision is
   flagged as a close call.

`profile/principles.json` holds the weights, veto lines, hard rules and these settings.

## Meta models used

| Model | Role | How |
| --- | --- | --- |
| **Muse Glimmer 30B** (open weights, Apache 2.0) | The twin's brain: scores options, gives its own option probabilities | Self-hosted with vLLM on a Modal H100 (`glimmer_server.py`), scales to zero |
| **Muse Spark 1.3** | The OpenClaw builder's model; alternative twin brain | Meta Model API, Standard tier |

## Architecture

```
 owner's task ─► Decision twin ◄──── question + options ──── OpenClaw builder
                 (Muse Glimmer)  ──── decision + reasons ───►  (Muse Spark 1.3)
                       │                                        in a Modal sandbox
                       ▼                                              │
                 event log (Modal Dict) ◄──── turns, files, tests ────┘
                       │
                       ▼
                  web UI (timeline, score grids, probabilities, agreement score)
```

| Path | What it is |
| --- | --- |
| `twin/` | The decision engine: schemas, scoring maths, retrieval of similar past decisions, untrusted-text fencing, model back-ends |
| `profile/` | Weighted principles, hard rules, 41 synthetic past decisions (10 held out for evaluation) |
| `worker/` | OpenClaw sandbox, the model gateway, and the twin–builder protocol |
| `orchestrator/` | The build loop and the event log |
| `webapi.py`, `web/` | API and UI |
| `app.py` | Modal app: build job and web endpoint |
| `glimmer_server.py` | vLLM server for Muse Glimmer |
| `twin/evaluation.py` | Agreement score on held-out decisions |
| `tests/` | Unit tests (fake models; no network) |

## Security and privacy

- **The builder never sees a key.** OpenClaw and every command it runs execute as an unprivileged
  user. The Meta API key lives in a root-only folder, read by a small gateway that forwards only
  chat completions, only for an allow-listed model, under a per-run token budget. The sandbox has
  no secrets in its environment.
- **Untrusted text stays data.** Everything the builder writes is cleaned and placed between data
  fences; the twin is told never to follow instructions inside them. Builder replies are parsed
  only through strict, validated blocks.
- **The result is inspected in place.** The finished site is started inside the sandbox and
  photographed there with a headless browser; nothing the builder made is exposed to the internet.
  Its source is saved and can be opened in the UI under a sandbox Content-Security-Policy.
- **Tests are run independently.** When the builder says it is finished, the loop runs the tests
  itself, and the twin decides whether to accept.
- **Web UI.** Strict Content-Security-Policy, no inline scripts, DOM built with `textContent`,
  allow-listed static files, validated inputs, and starting a run needs an access token. One run
  at a time.
- **No personal data.** No accounts, no tracking. The persona is synthetic.

## Run it

Requirements: a Modal account, a Meta Model API key.

```bash
pip install -e ".[dev]" modal
pytest                                                  # unit tests
modal secret create meta-api META_API_KEY=...
modal secret create glimmer GLIMMER_API_KEY=... GLIMMER_BASE_URL=https://<your-glimmer-endpoint>/v1
modal secret create twin-ui ACCESS_TOKEN=...
modal run glimmer_server.py::download                   # cache the open weights once
modal serve glimmer_server.py                           # Glimmer endpoint
modal run app.py::evaluate --brain glimmer              # agreement score
modal serve app.py                                      # web UI; start a build from the page
```

## Built during the hackathon

Everything in this repository was written during the event (from Oct 31, 2026); see the commit
history. Third-party software used as-is: OpenClaw, vLLM, FastAPI, Pydantic, the OpenAI Python
client, Modal.
