"""What a source hands the planner.

One shape for everything the account can talk about — a listing, a news story,
a number out of our own database, a question — so the planner can compare and
rank things that have nothing else in common, and the publisher can render them
without knowing where they came from.

`facts` is the important field. It is the complete set of things the copy is
allowed to state; copy.py enforces that rather than trusting the model to obey
it. A source that cannot supply facts cannot produce a post, which is the
property that lets this thing run unattended.
"""

from dataclasses import dataclass, field


@dataclass
class Material:
    kind: str                      # SocialPost.KIND_* — the editorial format
    key: str                       # identity for cooldown/dedupe
    headline: str                  # the line that has to stop the scroll
    facts: list                    # the ONLY claims the copy may make
    medium: str = "carousel"       # carousel | single | story | reel
    eyebrow: str = ""              # small label on the card
    body_eyebrow: str = ""         # label on the body cards
    footnote: str = ""             # attribution burnt onto the card
    link: str = ""                 # appended to the caption
    prewritten_body: str = ""      # skip the model entirely (we wrote it)
    prewritten_caption: str = ""   # caption text, when it should differ
    cooldown_days: int = None      # None = never repeat this key
    weight: float = 1.0
    needs_review: bool = False     # specific enough to be worth a human read
    brief: str = ""                # extra steer for the copywriter
    meta: dict = field(default_factory=dict)

    def __str__(self):
        return f"{self.kind}:{self.key}"
