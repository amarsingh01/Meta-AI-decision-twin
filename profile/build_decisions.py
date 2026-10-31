"""Generate profile/decisions.jsonl: the synthetic past decisions of the demo persona.

Every decision here is invented for the hackathon. They describe a made-up decision
style that is consistent with profile/principles.json; none come from real history.

Run: python profile/build_decisions.py
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

# (domain, question, options {id: label}, chosen, why, principles that drove it)
_D = [
    # --- Data sourcing and rights ---
    (
        "data",
        "Where should live stock prices come from for a public demo?",
        {
            "A": "Scrape the exchange website",
            "B": "Official free API with terms that allow display",
            "C": "Paid real-time feed",
        },
        "B",
        "Scraping breaks the exchange's terms; the paid feed is out of budget.",
        ["legal", "cost"],
    ),
    (
        "data",
        "No free real-time source allows public display. What now?",
        {"A": "Show delayed data and label it clearly", "B": "Pretend delayed data is live", "C": "Drop the feature"},
        "A",
        "Being honest about freshness keeps the feature and the user's trust.",
        ["clarity", "legal"],
    ),
    (
        "data",
        "Where should financial news come from?",
        {
            "A": "Copy full articles from news sites",
            "B": "Open news index with headlines and links",
            "C": "Paid news API",
        },
        "B",
        "Headlines plus links respect publishers' rights and cost nothing.",
        ["legal", "cost"],
    ),
    (
        "data",
        "A free API allows 25 calls a day. The page refreshes every 5 seconds.",
        {
            "A": "Call the API on every refresh",
            "B": "Cache server-side and refresh on a schedule",
            "C": "Buy a higher tier",
        },
        "B",
        "A cache keeps us inside the free limit and makes the page faster.",
        ["cost", "quality"],
    ),
    (
        "data",
        "Use a community library that unofficially wraps a finance site?",
        {"A": "Yes, it is easy", "B": "Only if the site's terms allow it; otherwise use an official API"},
        "B",
        "Convenience does not override the data owner's terms.",
        ["legal"],
    ),
    (
        "data",
        "How should market data be stored?",
        {"A": "Keep raw ticks forever", "B": "Keep only what the page needs, with a short retention"},
        "B",
        "Less stored data means less to secure and lower cost.",
        ["privacy", "cost", "security"],
    ),
    # --- Security ---
    (
        "security",
        "Where should the API key for the data provider live?",
        {"A": "In the front-end JavaScript", "B": "In a server-side secret store", "C": "Committed in a config file"},
        "B",
        "Keys in the browser or the repo are leaked keys.",
        ["security"],
    ),
    (
        "security",
        "A dependency has a known critical vulnerability. Release is today.",
        {"A": "Ship and patch later", "B": "Patch or replace it before release"},
        "B",
        "A known critical hole is not shipped, deadline or not.",
        ["security", "testing"],
    ),
    (
        "security",
        "News headlines will be fed to the language model. How to handle them?",
        {"A": "Paste them straight into the prompt", "B": "Mark them as untrusted data, strip markup and cap length"},
        "B",
        "External text can carry instructions; it must be treated as data.",
        ["security"],
    ),
    (
        "security",
        "Should the backend accept any ticker string from the user?",
        {"A": "Yes, pass it through", "B": "Validate against an allow-list pattern"},
        "B",
        "Every input is validated at the boundary.",
        ["security", "quality"],
    ),
    (
        "security",
        "How should the public endpoint be protected from abuse?",
        {"A": "No limits", "B": "Rate limiting and request size limits", "C": "Require user accounts"},
        "B",
        "Limits stop cost and abuse without collecting personal data.",
        ["security", "privacy", "cost"],
    ),
    (
        "security",
        "Which response headers should the site send?",
        {"A": "Framework defaults", "B": "Strict CSP, HSTS, no-sniff and frame denial"},
        "B",
        "Safe headers are cheap and close whole classes of attacks.",
        ["security"],
    ),
    (
        "security",
        "The agent needs to install packages. How?",
        {"A": "Install the latest of everything", "B": "Pinned versions from a lock file"},
        "B",
        "Pinned dependencies make builds reproducible and harder to poison.",
        ["security", "testing"],
    ),
    # --- Privacy ---
    (
        "privacy",
        "Add analytics to see how people use the site?",
        {
            "A": "Third-party tracking script",
            "B": "No tracking; count anonymous server-side hits only",
            "C": "Nothing at all",
        },
        "B",
        "Basic counts help without tracking anyone.",
        ["privacy"],
    ),
    (
        "privacy",
        "Should users sign in to save a watchlist?",
        {"A": "Email sign-in", "B": "Store the watchlist in the browser only"},
        "B",
        "No account means no personal data to protect.",
        ["privacy", "security"],
    ),
    (
        "privacy",
        "What should the server log?",
        {"A": "Full requests including IPs", "B": "Errors and timings, no IPs or identifiers"},
        "B",
        "Log what you need to fix things, nothing more.",
        ["privacy"],
    ),
    # --- Cost ---
    (
        "cost",
        "Where should the demo site be hosted?",
        {"A": "Always-on paid server", "B": "Serverless that scales to zero", "C": "Run it on a laptop"},
        "B",
        "Pay only when someone uses it.",
        ["cost", "quality"],
    ),
    (
        "cost",
        "Which model should summarise news for each request?",
        {"A": "The largest model every time", "B": "A small model, cached per headline"},
        "B",
        "A summary does not need the biggest model, and caching avoids paying twice.",
        ["cost"],
    ),
    (
        "cost",
        "A paid charting library looks nicer than the free one.",
        {"A": "Buy it", "B": "Use the free open-source one and style it well"},
        "B",
        "The free library does the job; the money is better kept.",
        ["cost"],
    ),
    # --- Quality and design ---
    (
        "quality",
        "How should data providers be integrated?",
        {"A": "Calls scattered through the code", "B": "One adapter interface per provider"},
        "B",
        "An adapter makes providers swappable and testable.",
        ["quality", "testing"],
    ),
    (
        "quality",
        "How should buy/sell signals be computed?",
        {"A": "Inside the web handler", "B": "Pure functions in their own module"},
        "B",
        "Pure functions are easy to test and reuse.",
        ["quality", "testing"],
    ),
    (
        "quality",
        "Pick a backend framework for a small JSON API.",
        {"A": "A heavy full-stack framework", "B": "A light typed framework", "C": "Hand-rolled HTTP server"},
        "B",
        "Light and typed: little code, clear contracts, well known.",
        ["quality", "security"],
    ),
    (
        "quality",
        "The page feels slow. What first?",
        {"A": "Rewrite in another language", "B": "Measure, then fix the slowest step"},
        "B",
        "Optimise what the numbers show, not what we guess.",
        ["quality", "cost"],
    ),
    (
        "quality",
        "Should configuration be hard-coded?",
        {"A": "Yes, it is faster", "B": "Typed settings loaded from the environment"},
        "B",
        "Config separate from code keeps secrets out and environments clean.",
        ["quality", "security"],
    ),
    # --- Testing ---
    (
        "testing",
        "Tests are failing and the demo is in an hour.",
        {"A": "Skip the tests", "B": "Fix the failures or cut the broken feature"},
        "B",
        "Nothing ships red.",
        ["testing"],
    ),
    (
        "testing",
        "How should code that calls external APIs be tested?",
        {"A": "Only against the live API", "B": "Recorded fixtures plus one live smoke test"},
        "B",
        "Fixtures make tests fast and repeatable; one live check catches drift.",
        ["testing", "cost"],
    ),
    (
        "testing",
        "How should the signal logic be checked?",
        {"A": "Eyeball a few charts", "B": "Unit tests on known price series"},
        "B",
        "Known inputs with expected outputs catch regressions.",
        ["testing"],
    ),
    # --- Clarity / UX ---
    (
        "clarity",
        "How should a Buy signal be shown?",
        {"A": "A big green BUY", "B": "BUY with confidence, reasons and data age"},
        "B",
        "People should see why and how sure, not just the verdict.",
        ["clarity"],
    ),
    (
        "clarity",
        "Where should the not-financial-advice notice go?",
        {"A": "Small print in the footer", "B": "Visible next to every signal"},
        "B",
        "The warning belongs where the decision is made.",
        ["clarity", "legal"],
    ),
    (
        "clarity",
        "The data source is down. What should the page do?",
        {"A": "Show the last values silently", "B": "Show the last values marked stale with the time"},
        "B",
        "Never let stale data look fresh.",
        ["clarity"],
    ),
    (
        "clarity",
        "How much jargon on the main screen?",
        {"A": "Indicator names only", "B": "Plain words, jargon in tooltips"},
        "B",
        "A first-time user should understand it without a glossary.",
        ["clarity"],
    ),
    # --- Trading style (intraday and swing) ---
    (
        "trading",
        "Intraday: price breaks out on low volume.",
        {"A": "Enter immediately", "B": "Wait for volume to confirm", "C": "Short it"},
        "B",
        "A breakout without volume often fails; wait for confirmation.",
        ["quality"],
    ),
    (
        "trading",
        "Intraday position is down 1.5%, the stop-loss level.",
        {"A": "Hold and hope", "B": "Exit at the stop", "C": "Average down"},
        "B",
        "The stop is the plan; capital protection comes first.",
        ["security"],
    ),
    (
        "trading",
        "Swing: strong earnings news, but the stock already gapped up 8%.",
        {"A": "Chase the gap", "B": "Wait for a pullback to support", "C": "Ignore the news"},
        "B",
        "Buying after a big gap risks entering at the top.",
        ["quality"],
    ),
    (
        "trading",
        "Swing: signals from price and news disagree.",
        {"A": "Follow the news", "B": "Follow the price", "C": "Stay out until they agree"},
        "C",
        "When evidence conflicts, no trade is a valid trade.",
        ["clarity"],
    ),
    (
        "trading",
        "How much of the capital per trade?",
        {"A": "Whatever feels right", "B": "Fixed risk of about 1% per trade"},
        "B",
        "Fixed risk keeps one bad trade from hurting much.",
        ["security"],
    ),
    (
        "trading",
        "Major results announcement in 30 minutes.",
        {"A": "Open a position before it", "B": "Stay flat until after the announcement"},
        "B",
        "Event risk is a coin flip, not an edge.",
        ["security"],
    ),
    (
        "trading",
        "A tip on a messaging group says a small stock will double.",
        {"A": "Buy some", "B": "Ignore unverified tips"},
        "B",
        "Unverified tips are noise and sometimes manipulation.",
        ["legal", "security"],
    ),
    # --- Process ---
    (
        "process",
        "Requirements are unclear and two readings are equally likely.",
        {"A": "Pick one and build", "B": "Ask the owner a short question first"},
        "B",
        "A one-line question is cheaper than rebuilding.",
        ["quality"],
    ),
    (
        "process",
        "The agent proposes deleting a folder it did not create.",
        {"A": "Allow it", "B": "Refuse and ask the human"},
        "B",
        "Irreversible actions outside the task need a human yes.",
        ["security"],
    ),
    (
        "process",
        "A feature works but its code is messy. Deadline is tomorrow.",
        {"A": "Ship as is", "B": "Ship after a small focused clean-up and tests", "C": "Rewrite it"},
        "B",
        "A small clean-up with tests is the best trade-off.",
        ["quality", "testing"],
    ),
]

# Every fourth decision is held out for evaluation; the twin never sees these as examples.
_HOLDOUT_EVERY = 4


def build() -> list[dict]:
    # Options are written with the preferred one mostly in slot B; shuffle them with a fixed
    # seed so the correct letter varies and a model cannot score well by always picking "B".
    rng = random.Random(20261031)  # noqa: S311 - deterministic shuffle, not security
    out = []
    for i, (domain, question, options, chosen, why, principles) in enumerate(_D, start=1):
        assert chosen in options, f"decision {i}: chosen {chosen!r} not in options"
        labels = list(options.values())
        chosen_label = options[chosen]
        rng.shuffle(labels)
        letters = "ABCD"[: len(labels)]
        out.append(
            {
                "id": f"d{i:03d}",
                "domain": domain,
                "question": question,
                "options": [{"id": letters[j], "label": lab} for j, lab in enumerate(labels)],
                "chosen": letters[labels.index(chosen_label)],
                "why": why,
                "principles": principles,
                "split": "holdout" if i % _HOLDOUT_EVERY == 0 else "train",
                "synthetic": True,
            }
        )
    return out


if __name__ == "__main__":
    path = Path(__file__).with_name("decisions.jsonl")
    rows = build()
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    holdout = sum(r["split"] == "holdout" for r in rows)
    spread = dict(sorted(Counter(r["chosen"] for r in rows).items()))
    print(f"wrote {len(rows)} decisions ({holdout} held out) to {path}; chosen letters {spread}")
