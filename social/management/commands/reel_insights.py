"""What actually happened to the reels — and which kind of reel works.

    manage.py reel_insights                 refresh, then report the last 90 days
    manage.py reel_insights --days 30
    manage.py reel_insights --no-fetch      report on what is already stored
    manage.py reel_insights --limit 50      refresh at most 50 posts

The bot picks a caption angle and a soundtrack at random for every post. That is
only defensible if something reads the result back, which nothing did: the
published id was thrown away, so no post could be looked up afterwards and no
format could be compared to another. This is that reader.

Averages exclude posts with no insights rather than counting them as zero — an
unfetched post would otherwise make a good angle look bad. Posts published
before media_id existed can never be fetched; they are reported as
unattributable instead of being quietly left out of the total.

Safe to run as often as you like: it only reads from Meta and writes the
snapshot back onto each row.
"""

import os
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from social.insights import group_by, refresh_insights
from social.models import SocialPost

PRICE_BANDS = [
    (1000, "under $1k"),
    (2000, "$1k–2k"),
    (3000, "$2k–3k"),
    (5000, "$3k–5k"),
    (float("inf"), "$5k+"),
]


class Command(BaseCommand):
    help = "Fetch Instagram/Facebook insights and report which format works."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90,
                            help="How far back to look (default 90).")
        parser.add_argument("--no-fetch", action="store_true",
                            help="Report on stored numbers without calling Meta.")
        parser.add_argument("--limit", type=int, default=None,
                            help="Refresh at most this many posts.")
        parser.add_argument("--channel", default="instagram",
                            choices=["instagram", "facebook", "all"],
                            help="Which channel to report on (default instagram).")

    def handle(self, *args, **options):
        since = timezone.now() - timedelta(days=options["days"])
        posts = SocialPost.objects.filter(datetime__gte=since)
        if options["channel"] != "all":
            posts = posts.filter(social_media=options["channel"])

        total = posts.count()
        if not total:
            self.stdout.write(self.style.WARNING(
                f"\nNothing posted in the last {options['days']} days.\n"
            ))
            return

        with_id = posts.exclude(media_id="")
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nPosts — last {options['days']} days ({options['channel']})"
        ))
        self._row("posted", total)
        self._row("can be measured", with_id.count(), total)
        unattributable = total - with_id.count()
        if unattributable:
            was = "was" if unattributable == 1 else "were"
            self.stdout.write(
                f"    {unattributable} {was} published without a stored media id "
                "and can never\n    be measured — anything posted before that id "
                "was kept is lost to us."
            )

        if not options["no_fetch"]:
            # Imported here, not at module scope: get_fresh_token lives beside the
            # video encoder, and --no-fetch must keep working when moviepy or
            # ffmpeg is unhappy on the box.
            from social.utils import get_fresh_token

            to_refresh = with_id.order_by("-datetime")
            if options["limit"]:
                to_refresh = to_refresh[: options["limit"]]
            token = get_fresh_token()
            if not token:
                self.stdout.write(self.style.ERROR(
                    "    No Page token available — skipping the fetch. "
                    "Run refresh_social_token first."
                ))
            else:
                fetched, skipped = refresh_insights(to_refresh, token)
                self._row("insights fetched now", fetched)
                if skipped:
                    self._row("no numbers returned", skipped)

        measured = [p for p in with_id if p.insights_fetched_at is not None]
        if not measured:
            hint = (
                "  Run it without --no-fetch to pull the numbers.\n"
                if options["no_fetch"]
                else "  The fetch returned nothing: the token or the Graph API\n"
                     "  version is the place to look.\n"
            )
            self.stdout.write(self.style.WARNING(
                f"\n  Nothing has insights yet.\n{hint}"
            ))
            return

        reels = [p for p in measured if p.content_type == "reel"]

        self.stdout.write(self.style.MIGRATE_HEADING("\nAverages per post"))
        self._averages(measured)
        if reels and len(reels) != len(measured):
            self.stdout.write("  Reels only:")
            self._averages(reels)

        self._table("By caption angle", group_by(measured, "caption_angle"))

        for post in measured:
            # Transient labels for grouping; never saved.
            post.sound_label = os.path.basename(post.sound_track or "") or "(none)"
        self._table("By soundtrack", group_by(
            [p for p in measured if p.content_type == "reel"], "sound_label"))

        self._label_price_bands(measured)
        self._table("By price band", group_by(measured, "price_band"))

        self.stdout.write(self.style.MIGRATE_HEADING("\nBest posts"))
        for post in sorted(measured, key=lambda p: (p.views or 0), reverse=True)[:5]:
            first_line = (post.caption or "").strip().splitlines()[0][:44]
            self.stdout.write(
                f"    {post.datetime:%d %b}  {self._n(post.views):>8} views  "
                f"{self._n(post.saves):>6} saves  {first_line}"
            )
        self.stdout.write("")

    # --- output helpers -----------------------------------------------------

    def _label_price_bands(self, posts):
        """Attach a price band to each post, via the property it advertised."""
        from inventory.models import Property

        prices = dict(
            Property.objects.filter(
                url__in=[p.property_url for p in posts if p.property_url]
            ).values_list("url", "price")
        )
        for post in posts:
            price = prices.get(post.property_url)
            if price is None:
                post.price_band = "(unknown)"
                continue
            post.price_band = next(
                label for ceiling, label in PRICE_BANDS if price < ceiling
            )

    def _averages(self, posts):
        rows = group_by(posts, "content_type")
        combined = {
            key: [r[key] for r in rows if r[key] is not None]
            for key in ("views", "reach", "saves", "shares", "interactions", "watch_ms")
        }
        for key, values in combined.items():
            if not values:
                continue
            average = sum(values) / len(values)
            if key == "watch_ms":
                self.stdout.write(f"    avg watch time        {average / 1000:.1f}s")
            else:
                self.stdout.write(f"    {key:<21} {average:.0f}")

    def _table(self, heading, rows):
        if not rows:
            return
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{heading}"))
        self.stdout.write(
            f"    {'':<44} {'posts':>5} {'views':>8} {'reach':>8} "
            f"{'saves':>6} {'shares':>6} {'watch':>6}"
        )
        for row in rows:
            watch = f"{row['watch_ms'] / 1000:.1f}s" if row["watch_ms"] else "-"
            self.stdout.write(
                f"    {row['label'][:44]:<44} {row['posts']:>5} "
                f"{self._n(row['views']):>8} {self._n(row['reach']):>8} "
                f"{self._n(row['saves']):>6} {self._n(row['shares']):>6} {watch:>6}"
            )

    def _n(self, value):
        if value is None:
            return "-"
        return f"{value:.0f}" if isinstance(value, float) else str(value)

    def _row(self, label, value, of=None):
        share = f"  ({100 * value // of}%)" if of else ""
        self.stdout.write(f"    {label:<28} {value}{share}")
