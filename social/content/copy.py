"""Writes the words, from the facts, and checks it did.

The listing captions already work this way — the price and location line is
assembled in Python and the model only supplies the flavour — and that is the
only reason this can post unattended. Here the rule is enforced rather than
requested: a draft that states a number the facts did not contain is thrown
away, not published.
"""

import logging
import random
import re

from ai.providers import ai_client
from social.utils import build_hashtags

logger = logging.getLogger(__name__)

COPY_SCHEMA = {
    "type": "object",
    "properties": {
        "body": {"type": "string"},
        "caption_body": {"type": "string"},
    },
    "required": ["body", "caption_body"],
    "additionalProperties": False,
}

CTAS = [
    "Got a question we haven't answered? Ask below 👇",
    "Ask us anything in the comments — we read all of them 💬",
    "More on our site 🏠",
    "Save this for when you start looking ✨",
    "Thinking about it? Drop us a message 📩",
]

# Said in the caption on the material where a wrong reading costs someone real
# money. The card stays readable; the caption carries the qualification.
DISCLAIMER = (
    "General information about how these purchases work — not advice on a "
    "specific property. Rules and rates vary by municipality."
)

BANNED_PHRASES = [
    "nestled", "hidden gem", "hustle and bustle", "boasts", "dive in",
    "unlock", "the truth is", "game changer", "look no further",
]


def _numbers(text):
    """Digit runs, so '¥1,200' and '1200' compare as the same fact."""
    return set(re.findall(r"\d+", text.replace(",", "")))


def _invents_numbers(text, facts):
    """True if the copy states a figure the facts did not.

    A plausible invented "around 10%" or "¥500,000" is the exact failure that
    makes an account like this untrustworthy, and it is the one kind of error
    that is cheap to detect. So detect it.
    """
    allowed = _numbers(" ".join(facts))
    return bool(_numbers(text) - allowed)


def _prompt(material):
    facts = "\n".join(f"- {fact}" for fact in material.facts)
    brief = f"\n{material.brief}\n" if material.brief else ""
    return (
        "You write for an Instagram account that helps an international "
        "audience buy cheap vacant houses (akiya) in Japan.\n\n"
        f"Subject: {material.headline}\n\n"
        "The facts you may use, and the ONLY facts you may use:\n"
        f"{facts}\n"
        f"{brief}\n"
        "Return two things.\n\n"
        "1. `body`: text for a swipe card. Two or three short paragraphs "
        "separated by a single newline, each one or two sentences. Plain and "
        "spoken, as if talking to one person. No emojis, no hashtags, no "
        "headings, no bullet characters.\n\n"
        "2. `caption_body`: two or three sentences saying the same thing more "
        "warmly for the post caption. At most one emoji. No hashtags.\n\n"
        "Rules, all absolute:\n"
        "- State nothing that is not in the facts above. No figures, "
        "percentages, prices, dates or rules of your own.\n"
        "- Do not hedge it into mush; the facts are reliable, say them plainly.\n"
        "- Do not open by restating the subject.\n"
        f"- Never use: {', '.join(BANNED_PHRASES[:6])}.\n"
        "- Never imply we give legal, tax or immigration advice."
    )


def write_copy(material, attempts=2):
    """Return (card_body, caption_body) for a material.

    Raises if the model cannot produce copy that stays inside the facts. The
    caller treats that as "skip this one", which is the correct behaviour for
    something running unattended: saying nothing beats saying something wrong.
    """
    if material.prewritten_body:
        # Stats materials write their own body: the numbers *are* the copy, and
        # there is no reason to let a model near them. The caption gets its own
        # wording where repeating the card verbatim under it would read like a
        # template — the same reason the listing captions stopped saying the
        # price twice.
        return (
            material.prewritten_body,
            material.prewritten_caption or material.prewritten_body,
        )

    client = ai_client()
    last_reason = ""
    for attempt in range(attempts):
        result = client.generate_json(
            _prompt(material), COPY_SCHEMA, schema_name="post_copy"
        )
        body = (result.get("body") or "").strip()
        caption_body = (result.get("caption_body") or "").strip()

        if not body or not caption_body:
            last_reason = "empty field"
        elif [
            name for name, text in (("body", body), ("caption", caption_body))
            if _invents_numbers(text, material.facts)
        ]:
            last_reason = "invented a figure the facts did not contain"
        else:
            return body, caption_body

        logger.warning(
            "Copy for %s rejected on attempt %s: %s", material, attempt + 1,
            last_reason,
        )
    raise RuntimeError(f"No usable copy for {material}: {last_reason}")


def build_caption(material, caption_body):
    """Assemble the caption here, not in the model.

    Instagram truncates at roughly 125 characters, so the headline goes first —
    it is the part that decides whether the rest is ever expanded.
    """
    parts = [material.headline, caption_body]
    if material.link:
        # Attribution before the call to action: a news post that buries whose
        # story it is looks like it is passing the reporting off as ours.
        parts.append(f"{material.footnote}\n{material.link}".strip())
    parts.append(random.choice(CTAS))
    if material.needs_review:
        parts.append(DISCLAIMER)
    parts.append(build_hashtags(material.meta.get("location", "")))
    return "\n\n".join(part for part in parts if part)
