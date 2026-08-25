"""The one thing cron calls. Decides what to say today, and says it.

    manage.py community_manager                # decide and post
    manage.py community_manager --dry-run      # decide, render, post nothing
    manage.py community_manager --show         # what it could post, and the odds
    manage.py community_manager --kind news    # force a format
    manage.py community_manager --story        # force a story slot
    manage.py community_manager --asked        # questions the bank can't answer

Running it more than once a day is fine and intended: the first run of the day
takes the feed slot, later ones become stories, and a run with nothing worth
saying says nothing.
"""

from django.core.management.base import BaseCommand

from social.constants import CONTENT_WEIGHTS, SOCIAL_REQUIRE_APPROVAL
from social.content.planner import choose, performance_multipliers
from social.content.publisher import publish
from social.content.sources import gather_all
from social.content.sources.faq import unanswered_follower_questions

NETWORKS = {
    "instagram": ("instagram",),
    "facebook": ("facebook",),
    "both": ("instagram", "facebook"),
}


class Command(BaseCommand):
    help = "Decide what to post today across every content format, and post it."

    def add_arguments(self, parser):
        parser.add_argument("--network", choices=sorted(NETWORKS), default="instagram")
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Choose, write and render, but publish nothing.",
        )
        parser.add_argument(
            "--show", action="store_true",
            help="List available material and the weights, then exit.",
        )
        parser.add_argument(
            "--kind", help="Force a format: listing, news, data or faq.",
        )
        parser.add_argument(
            "--story", action="store_true", help="Force a story slot.",
        )
        parser.add_argument(
            "--feed", action="store_true",
            help="Force a feed slot even if something already went out today.",
        )
        parser.add_argument(
            "--asked", action="store_true",
            help="List follower questions the bank has no facts for, and exit.",
        )

    def handle(self, *args, **options):
        if options["asked"]:
            return self._report_asked()

        materials = gather_all()
        if options["kind"]:
            materials = [m for m in materials if m.kind == options["kind"]]
            if not materials:
                self.stdout.write(
                    self.style.WARNING(f"Nothing available of kind "
                                       f"'{options['kind']}'.")
                )
                return

        if options["show"]:
            return self._show(materials)

        prefer_story = True if options["story"] else (
            False if options["feed"] else None
        )
        material, medium = choose(materials, prefer_story=prefer_story)
        if not material:
            self.stdout.write(
                "Nothing worth posting right now — everything available is "
                "inside its cooldown."
            )
            return

        self.stdout.write(f"\n=== {material.kind}: {material.headline}")
        self.stdout.write(f"medium: {medium}")

        outcome = publish(
            material, medium,
            networks=NETWORKS[options["network"]],
            dry_run=options["dry_run"],
        )

        if outcome["caption"]:
            self.stdout.write("\n--- caption ---")
            self.stdout.write(outcome["caption"])
        for path in outcome["cards"]:
            self.stdout.write(f"  card: {path}")

        if outcome["posted"]:
            self.stdout.write(
                self.style.SUCCESS(f"\nposted to {', '.join(outcome['posted'])}")
            )
        else:
            self.stdout.write(
                self.style.WARNING(f"\nnothing posted: {outcome['skipped']}")
            )

    def _show(self, materials):
        multipliers = performance_multipliers()
        self.stdout.write(f"\n{len(materials)} material(s) available:\n")
        by_kind = {}
        for material in materials:
            by_kind.setdefault(material.kind, []).append(material)

        for kind, items in sorted(by_kind.items()):
            base = CONTENT_WEIGHTS.get(kind, 1.0)
            multiplier = multipliers.get(kind)
            note = (
                f" × {multiplier:.2f} from performance" if multiplier
                else " (not enough posts measured to adjust)"
            )
            self.stdout.write(f"{kind}: weight {base}{note}")
            for material in items[:6]:
                self.stdout.write(f"   · [{material.medium}] {material.headline[:88]}")
            if len(items) > 6:
                self.stdout.write(f"   … and {len(items) - 6} more")
            self.stdout.write("")

        if SOCIAL_REQUIRE_APPROVAL:
            self.stdout.write(
                self.style.WARNING(
                    "SOCIAL_REQUIRE_APPROVAL is on: posts will be held as "
                    "drafts instead of published."
                )
            )

    def _report_asked(self):
        questions = unanswered_follower_questions()
        if not questions:
            self.stdout.write(
                "No unanswered follower questions recorded yet. They accumulate "
                "once reply_comments_instagram has run since the question field "
                "was added."
            )
            return
        self.stdout.write(f"{len(questions)} question(s) the bank cannot answer:")
        for question in questions:
            self.stdout.write(f"  · {question}")
        self.stdout.write(
            "\nAdd facts for the recurring ones to social/content/faq_bank.py — "
            "the bot will not answer what it has no facts for."
        )
