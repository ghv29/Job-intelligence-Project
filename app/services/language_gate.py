"""
Tiered German-language requirement detection for job descriptions.

Calibration (July 2026): the user is B1-certified but has worked in
German-only environments (Tesla, Amazon, TITV). Boilerplate like
"sehr gute Deutschkenntnisse" is therefore a flag, not a blocker.
Only explicit C1/C2/verhandlungssicher/muttersprachlich requirements
count as a real barrier — and even those are penalised, never dropped.

Tiers:
  - "required_c1":     explicit C1/C2/verhandlungssicher/native German demanded
  - "fluent_boilerplate": "sehr gute/fliessende Deutschkenntnisse" phrasing
  - "english_friendly": posting signals English working language / German optional
  - "unspecified":     no language requirement detected
"""

from __future__ import annotations

import re

TIER_REQUIRED_C1 = "required_c1"
TIER_BOILERPLATE = "fluent_boilerplate"
TIER_ENGLISH_FRIENDLY = "english_friendly"
TIER_UNSPECIFIED = "unspecified"

# Explicit, deliberate German requirements. Matched within a short window of
# a "deutsch"/"german" mention so "verhandlungssicheres Englisch" doesn't trigger.
_HARD_TERMS = r"(?:verhandlungssicher\w*|muttersprach\w*|c[12](?![0-9])|native)"
_GERMAN_WORD = r"(?:deutsch\w*|german)"
_WINDOW = r"[^.!?\n]{0,80}?"

_HARD_PATTERNS = [
    re.compile(_GERMAN_WORD + _WINDOW + _HARD_TERMS, re.IGNORECASE),
    re.compile(_HARD_TERMS + _WINDOW + _GERMAN_WORD, re.IGNORECASE),
]

# HR boilerplate — flag only, no exclusion.
_SOFT_TERMS = r"(?:sehr gut\w*|flie(?:ss|ß)end\w*|fluent)"

_SOFT_PATTERNS = [
    re.compile(_SOFT_TERMS + _WINDOW + _GERMAN_WORD, re.IGNORECASE),
    re.compile(_GERMAN_WORD + _WINDOW + _SOFT_TERMS, re.IGNORECASE),
]

_ENGLISH_FRIENDLY_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"working language\s+(?:is\s+)?english",
        r"englisch\w*\s+(?:als|ist)\s+(?:unsere\s+)?arbeitssprache",
        r"arbeitssprache\s+(?:ist\s+)?englisch",
        r"english[- ]speaking (?:team|environment|company)",
        r"deutsch\w*\s+(?:sind?\s+)?(?:nicht erforderlich|kein muss|von vorteil|ein plus|w[üu]nschenswert)",
        r"german\s+(?:is\s+)?(?:not required|a plus|nice[- ]to[- ]have|beneficial|advantageous)",
    ]
]


def _first_match(patterns: list[re.Pattern], text: str) -> str | None:
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            return m.group(0).strip()
    return None


def detect_german_requirement(text: str | None) -> dict:
    """
    Classify the German-language requirement of a job posting.

    Works on raw text (not accent-stripped) so "fließend"/"ß" survive.
    Returns {"tier": <tier>, "evidence": <matched snippet or None>}.
    """
    if not text:
        return {"tier": TIER_UNSPECIFIED, "evidence": None}

    # Hard requirement wins over everything else.
    evidence = _first_match(_HARD_PATTERNS, text)
    if evidence:
        return {"tier": TIER_REQUIRED_C1, "evidence": evidence}

    soft_evidence = _first_match(_SOFT_PATTERNS, text)
    english_evidence = _first_match(_ENGLISH_FRIENDLY_PATTERNS, text)

    # An explicit English-working-language signal outweighs soft boilerplate.
    if english_evidence:
        return {"tier": TIER_ENGLISH_FRIENDLY, "evidence": english_evidence}
    if soft_evidence:
        return {"tier": TIER_BOILERPLATE, "evidence": soft_evidence}
    return {"tier": TIER_UNSPECIFIED, "evidence": None}
