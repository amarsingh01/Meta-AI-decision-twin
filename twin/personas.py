"""Three builder personas: the shared principles plus each owner's finer requirements."""

from __future__ import annotations

import json
from dataclasses import dataclass

from .profile import PROFILE_DIR
from .schema import Profile

PERSONAS_FILE = PROFILE_DIR / "personas.json"

RAINFOREST_TASK = (
    'Build "Amazon Rainforest Live", a website about the Amazon rainforest as it is today, for the general '
    "public. Cover: current weather and rainfall around Manaus, air quality (smoke from fires), recent "
    "wildlife sightings, and a few short news or background items. Free sources with no API key that you "
    "may use: Open-Meteo forecast API (api.open-meteo.com), Open-Meteo air quality API "
    "(air-quality-api.open-meteo.com), GBIF occurrence search (api.gbif.org) and the Wikipedia REST API "
    "(en.wikipedia.org). Put the site in /work/site with automated tests. When the tests pass, publish it "
    'by running: guardian-request deploy "publish the built site".'
)
RAINFOREST_HOSTS = frozenset(
    {"api.open-meteo.com", "air-quality-api.open-meteo.com", "api.gbif.org", "en.wikipedia.org"}
)


@dataclass(frozen=True)
class Persona:
    key: str
    name: str
    short: str
    color: str
    tagline: str
    requirements: tuple[str, ...]
    profile: Profile

    def brief(self, task: str) -> str:
        """The task as the agent receives it: the job plus this owner's requirements."""
        reqs = "\n".join(f"- {r}" for r in self.requirements)
        return f"{task}\n\nTHE OWNER'S REQUIREMENTS FOR THIS BUILD (follow all of them):\n{reqs}"

    def summary(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "short": self.short,
            "color": self.color,
            "tagline": self.tagline,
            "requirements": list(self.requirements),
            "principles": [p.model_dump() for p in self.profile.principles],
            "hard_rules": list(self.profile.hard_rules),
        }


def load_personas() -> dict[str, Persona]:
    raw = json.loads(PERSONAS_FILE.read_text(encoding="utf-8"))
    return {
        key: Persona(
            key=key,
            profile=Profile.model_validate(v["profile"]),
            requirements=tuple(v["persona"]["requirements"]),
            **{k: v["persona"][k] for k in ("name", "short", "color", "tagline")},
        )
        for key, v in raw.items()
    }
