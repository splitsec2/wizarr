"""Password checks for the invite's account form.

The strong check is for servers that declare strong_password (their password is
shared with apps that may keep a weakly hashed copy); the rest only get a length
range.

The strong check follows NIST SP 800-63B: no composition rules, a length
range, and a check against guessability (zxcvbn score 4, about 1e10 guesses or
more) that knows the person's own words (their email name, their name) and the
operator's (PASSWORD_EXTRA_WORDS, comma separated). The 64-character cap keeps
zxcvbn's cost bounded on long inputs.
"""

import os
import re

from flask_babel import gettext as _
from zxcvbn import zxcvbn

MIN_LENGTH = 12
MAX_LENGTH = 64
MIN_SCORE = 4
# Servers that don't ask for the strong check: their clients' own limits.
BASIC_MIN_LENGTH = 8
BASIC_MAX_LENGTH = 128


def personal_words(email: str = "", *names: str | None) -> list[str]:
    """Words a guesser would try first for this person."""
    words: list[str] = []
    local = (email or "").split("@", 1)[0]
    if local:
        words.append(local)
        words += [w for w in re.split(r"[._+-]+", local) if w]
    for name in names:
        if name:
            words.append(name)
            words += name.split()
    extra = os.getenv("PASSWORD_EXTRA_WORDS", "")
    words += [w.strip() for w in extra.split(",") if w.strip()]
    return words


def problem_with(
    password: str, user_inputs: list[str], *, strong: bool = True
) -> str | None:
    """Why *password* isn't acceptable, in plain words, or None if it is."""
    if not strong:
        if not BASIC_MIN_LENGTH <= len(password) <= BASIC_MAX_LENGTH:
            return _("Use 8 to 128 characters.")
        return None
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        return _("Use 12 to 64 characters.")
    if zxcvbn(password, user_inputs=user_inputs)["score"] < MIN_SCORE:
        return _("That password is too easy to guess. Try a short sentence.")
    return None
