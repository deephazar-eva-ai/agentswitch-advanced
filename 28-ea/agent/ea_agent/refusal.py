"""When --prompt asks for a forbidden action, the agent stops before any platform write.

v1 rule (suite/TEST_SUITE_GUIDE.md §4): send, book, delete, accept, replace or edit → refuse.
Normal briefing prompts must not use those words, or they are refused correctly.
"""
from __future__ import annotations

import re

# Same words the suite's well-behaved fake agent uses (tests/test_suite.py REFUSE_WORDS).
REFUSE_WORDS = re.compile(r"\b(send|book|delete|accept|replace|edit)\b", re.I)

REFUSED_MESSAGE = (
    "Refused (v1): the request asks to send, book, delete, accept, replace or edit. "
    "Nothing was written."
)


def should_refuse(prompt: str | None) -> bool:
    return bool(prompt and REFUSE_WORDS.search(str(prompt)))
