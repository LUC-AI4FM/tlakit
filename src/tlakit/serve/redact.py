"""Strip host details out of anything sent to a client.

No error class currently leaks a filesystem path -- that was measured across
missing modules, semantic errors, syntax errors, bad configs, and evaluation
errors. But it holds because the parsers happen to capture message text that
excludes paths, not because anything enforces it. A new TLC message, or a new
diagnostic pattern, could change that silently.

This is defence in depth, not a fix for a known hole. Obscuring where the
install lives buys very little on its own -- anyone who can read a path from
this service already has more access than the path would give them. What it does
buy: the account name, the install layout, and the jar version stop being free
information for someone deciding what to try next.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

PLACEHOLDER = "<path>"

#: Absolute POSIX paths, and Windows drive paths for good measure.
_ABSOLUTE = re.compile(r"(?:[A-Za-z]:\\|/)(?:[\w.\-+@]+[/\\])*[\w.\-+@]*")

#: Paths shorter than this are more likely to be prose than a real path.
_MIN_SEGMENTS = 2


def _sensitive_terms() -> set[str]:
    terms = set()
    home = os.environ.get("HOME")
    if home and home not in ("/", "/var/empty"):
        terms.add(home)
        name = Path(home).name
        if len(name) > 2:
            terms.add(name)
    user = os.environ.get("USER") or os.environ.get("LOGNAME")
    if user and len(user) > 2:
        terms.add(user)
    for var in ("TLAKIT_TLA2TOOLS", "TLAKIT_COMMUNITY_MODULES"):
        value = os.environ.get(var)
        if value:
            terms.add(value)
            terms.add(str(Path(value).parent))
    return {t for t in terms if t}


def redact(text: str) -> str:
    """Replace absolute paths and host identifiers with a placeholder.

    Paths are handled first. Doing bare terms first mangles them: a username
    like "eric" matches inside "ericspencer", leaving a half-redacted string
    that still discloses the rest.
    """
    if not text:
        return text

    def swap(match: re.Match[str]) -> str:
        candidate = match.group(0)
        # "/\\ x = 0" and TLA+ operators are not paths; require real depth.
        if candidate.count("/") + candidate.count("\\") < _MIN_SEGMENTS:
            return candidate
        return PLACEHOLDER

    text = _ABSOLUTE.sub(swap, text)

    # Whatever survived is a bare identifier, so match on word boundaries only.
    for term in sorted(_sensitive_terms(), key=len, reverse=True):
        if "/" in term or "\\" in term:
            continue  # already covered by the path pass
        text = re.sub(rf"\b{re.escape(term)}\b", PLACEHOLDER, text)
    return text


def redact_deep(value):
    """Redact every string inside a JSON-shaped structure."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: redact_deep(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_deep(v) for v in value]
    return value
