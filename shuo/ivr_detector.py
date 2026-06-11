"""
ivr_detector.py — Automatic IVR/automated-phone-system detection.

Two complementary signals are combined:

  1. Phone number pattern  — instant pre-call verdict for known service-number
     formats (AU 13xxxx/1300/1800, US toll-free 8xx, UK 0800/03xx, …).
     Fires before the first word is spoken so the greeting can be suppressed.

  2. Transcript content   — running confidence from speech patterns during the
     call (e.g. "welcome to", "press 1 for", "thank you for calling").
     Catches IVR systems that use non-standard phone numbers.

Usage
-----
    detector = IVRDetector()
    detector.setup(phone="131171", force=False, on_detected=callback)
    # → is_ivr is True immediately (AU 13-number)

    detector = IVRDetector()
    detector.setup(phone="+16175551234")
    detector.analyze("Welcome to Acme Corp. Press 1 for sales.")
    # → is_ivr becomes True; on_detected fires
"""

from __future__ import annotations

import re
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Phone number patterns that indicate a private / withheld number
# ---------------------------------------------------------------------------

_PRIVATE_PATTERNS: list[re.Pattern] = [
    # String representations used by carriers and Twilio (case-insensitive)
    re.compile(r"^(private|unknown|withheld|anonymous|blocked|restricted|unavailable)$", re.I),
    re.compile(r"^private\s*(number|call)$",    re.I),
    re.compile(r"^no\s*caller\s*id$",           re.I),
    re.compile(r"^international$",              re.I),  # UK carrier withheld
    # Twilio anonymous caller ID placeholder
    re.compile(r"^\+?266696687$"),
    # All-zeros numeric placeholder (any length ≥ 3)
    re.compile(r"^0{3,}$"),
    # Country-prefixed all-zeros  (+1, +44, +61, +64)
    re.compile(r"^\+?1\s*0{7,10}$"),           # US/CA
    re.compile(r"^\+?44\s*0{9,10}$"),          # UK
    re.compile(r"^\+?61\s*0{8,9}$"),           # AU
    re.compile(r"^\+?64\s*0{7,9}$"),           # NZ
]


def classify_private_number(phone: str) -> bool:
    """
    Return True if the phone string indicates a private / withheld caller.
    Strips spaces and hyphens before matching.
    """
    cleaned = re.sub(r"[\s\-]", "", phone)
    return any(p.fullmatch(cleaned) for p in _PRIVATE_PATTERNS)


# ---------------------------------------------------------------------------
# Phone number patterns that strongly indicate an IVR / service line
# ---------------------------------------------------------------------------

_IVR_PATTERNS: list[re.Pattern] = [
    # AU service numbers (with or without +61 country code)
    re.compile(r"^\+?61\s*13\d{4}$"),          # AU 13xxxx
    re.compile(r"^\+?61\s*1300\d{6}$"),         # AU 1300 xxxxxx
    re.compile(r"^\+?61\s*1800\d{6}$"),         # AU 1800 xxxxxx
    # AU bare (no country code)
    re.compile(r"^13\d{4}$"),
    re.compile(r"^1300\d{6}$"),
    re.compile(r"^1800\d{6}$"),
    # US toll-free
    re.compile(r"^\+?1?\s*8(00|44|55|66|77|88)\d{7}$"),
    # UK non-geographic
    re.compile(r"^\+?44\s*0?(800|808|3\d{2})\d{6,7}$"),
    re.compile(r"^0(800|808|3\d{2})\d{6,7}$"),
]

# ---------------------------------------------------------------------------
# Transcript scoring
# ---------------------------------------------------------------------------

# (pattern, confidence_increment)
_STRONG: list[tuple[re.Pattern, float]] = [
    (re.compile(r"\bwelcome\s+to\b",                         re.I), 0.65),
    (re.compile(r"\bthank\s+you\s+for\s+calling\b",          re.I), 0.65),
    (re.compile(r"\bplease\s+listen\s+carefully\b",          re.I), 0.60),
    (re.compile(r"\bour\s+menu\s+options\b",                 re.I), 0.70),
    (re.compile(r"\bpress\s+\d\b",                           re.I), 0.60),
    (re.compile(r"\bdial\s+\d\b",                            re.I), 0.60),
    (re.compile(r"\bfor\s+.{3,40},?\s+press\b",              re.I), 0.70),
    (re.compile(r"\bsay\s+or\s+press\b",                     re.I), 0.70),
    (re.compile(r"\bplease\s+press\b",                       re.I), 0.60),
    (re.compile(r"\bto\s+speak\s+(to|with)\b",               re.I), 0.50),
]

_WEAK: list[tuple[re.Pattern, float]] = [
    (re.compile(r"\byour\s+call\s+(is|may\s+be)\b",          re.I), 0.20),
    (re.compile(r"\bplease\s+hold\b",                        re.I), 0.20),
    (re.compile(r"\bcurrently\s+experiencing\b",             re.I), 0.20),
    (re.compile(r"\bhigh\s+call\s+volumes?\b",               re.I), 0.20),
    (re.compile(r"\ball\s+(of\s+our\s+)?(operators?|agents?"
                r"|representatives?)\s+are\b",               re.I), 0.20),
    (re.compile(r"\bestimated\s+wait\s+time\b",              re.I), 0.25),
    (re.compile(r"\byou\s+are\s+(now\s+)?(being\s+)?"
                r"connected\b",                              re.I), 0.20),
]

_DETECT_THRESHOLD = 0.85   # cumulative confidence needed to confirm IVR


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def classify_number(phone: str) -> bool:
    """
    Return True if the phone number pattern strongly suggests a service/IVR line.
    Strips spaces and hyphens before matching.
    """
    cleaned = re.sub(r"[\s\-]", "", phone)
    return any(p.fullmatch(cleaned) for p in _IVR_PATTERNS)


def _score_transcript(text: str) -> float:
    """Return a confidence increment for a single transcript turn."""
    score = 0.0
    for pattern, weight in _STRONG:
        if pattern.search(text):
            score += weight
    for pattern, weight in _WEAK:
        if pattern.search(text):
            score += weight
    return min(score, 1.0)


# ---------------------------------------------------------------------------
# IVRDetector
# ---------------------------------------------------------------------------

class IVRDetector:
    """
    Lazy-initialized IVR detector.

    Create an instance before the call starts, then call setup() once the
    phone number is known (e.g. inside a get_goal() callback). After that,
    call analyze() on each incoming transcript — it returns True the first
    time IVR is confirmed.

    is_ivr is always safe to read; it starts False and becomes True once
    either number classification or transcript evidence crosses the threshold.
    """

    def __init__(self) -> None:
        self._detected:     bool            = False
        self._confidence:   float           = 0.0
        self._on_detected:  Optional[Callable[[], None]] = None

    def setup(
        self,
        phone:       str                          = "",
        force:       bool                         = False,
        on_detected: Optional[Callable[[], None]] = None,
    ) -> None:
        """
        Activate the detector.  Safe to call multiple times (idempotent once
        detected=True).

        phone       : destination/caller number — checked against known service
                      number patterns for an instant verdict.
        force       : treat as IVR immediately regardless of number pattern
                      (maps to the --ivr CLI flag).
        on_detected : zero-argument callback fired once when IVR is first
                      confirmed; useful for updating external state.
        """
        if self._detected:
            return
        self._on_detected = on_detected
        if force or classify_number(phone):
            self._detected   = True
            self._confidence = 1.0
            if self._on_detected:
                self._on_detected()

    @property
    def is_ivr(self) -> bool:
        return self._detected

    def analyze(self, transcript: str) -> bool:
        """
        Analyze one transcript turn.

        Returns True the first time IVR is confirmed (False → True transition),
        False on every subsequent call or if already detected.
        """
        if self._detected:
            return False
        transcript = transcript.lstrip(",").strip()
        score = _score_transcript(transcript)
        self._confidence = min(1.0, self._confidence + score)
        if self._confidence >= _DETECT_THRESHOLD:
            self._detected = True
            if self._on_detected:
                self._on_detected()
            return True
        return False
