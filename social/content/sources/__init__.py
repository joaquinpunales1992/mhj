"""Everything the account could talk about today.

Adding a format means adding a module here with a gather() returning Materials.
The planner and the publisher need no changes for it — which is the whole point
of the split.
"""

import logging

from . import faq, listings, news, places, stats

logger = logging.getLogger(__name__)

SOURCES = [listings, news, stats, faq, places]


def gather_all():
    """Collect from every source. One failing source must not silence the rest."""
    materials = []
    for source in SOURCES:
        try:
            materials.extend(source.gather())
        except Exception as exc:
            logger.error("Source %s failed: %s", source.__name__, exc)
    return materials
