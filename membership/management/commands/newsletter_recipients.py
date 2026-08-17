"""Who should receive this week's newsletter, and what to put in it.

The newsletter is a Pro benefit sent by hand. That's a reasonable choice at low
volume, but only if the two hard parts are trivial: knowing exactly who has paid
this week, and having the listings to write about. Doing either by clicking
around the admin is how a weekly commitment quietly stops happening.

    manage.py newsletter_recipients              # recipients + suggested picks
    manage.py newsletter_recipients --bcc        # just a paste-ready BCC line
    manage.py newsletter_recipients --picks 20   # how many listings to suggest
    manage.py newsletter_recipients --days 7     # how far back to look

Only subscribers whose access is currently valid are listed — including someone
who has cancelled but whose paid period hasn't ended, because they are still
owed the issue they paid for.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from inventory.models import Property, PropertyImage
from django.db import models

from membership.models import Subscription


class Command(BaseCommand):
    help = "List Pro members to send the newsletter to, plus listings to feature."

    def add_arguments(self, parser):
        parser.add_argument("--bcc", action="store_true",
                            help="Print only a comma-separated address list.")
        parser.add_argument("--picks", type=int, default=20,
                            help="How many recent listings to suggest (default 20).")
        parser.add_argument("--days", type=int, default=7,
                            help="Consider listings added in the last N days (default 7).")

    def handle(self, *args, **options):
        # is_active is a property, not a column, so filter broadly then let the
        # model decide — it knows about the cancelled-but-still-paid case.
        recipients = [
            s.user.email
            for s in Subscription.objects.select_related("user")
            if s.is_active and s.user.email
        ]

        if options["bcc"]:
            self.stdout.write(", ".join(sorted(recipients)))
            return

        self.stdout.write(self.style.SUCCESS(
            f"\n{len(recipients)} Pro member(s) to send to:"
        ))
        if recipients:
            for email in sorted(recipients):
                self.stdout.write(f"  {email}")
            self.stdout.write("\nPaste-ready BCC:")
            self.stdout.write(self.style.SUCCESS("  " + ", ".join(sorted(recipients))))
        else:
            self.stdout.write(
                "  (nobody yet — the newsletter is advertised as a Pro benefit, "
                "so there is nothing owed until someone subscribes)"
            )

        since = timezone.now() - timedelta(days=options["days"])
        picks = (
            Property.objects.annotate(
                has_any_image=models.Exists(
                    PropertyImage.objects.filter(property=models.OuterRef("pk"))
                )
            )
            .filter(
                show_in_front=True,
                price__gt=0,
                has_any_image=True,
                created_at__gte=since,
            )
            .order_by("price")[: options["picks"]]
        )

        self.stdout.write(self.style.SUCCESS(
            f"\n{picks.count()} listing(s) added in the last {options['days']} days, "
            f"cheapest first:"
        ))
        if not picks:
            self.stdout.write(
                "  (none — widen the window with --days, or the scraper hasn't "
                "run recently)"
            )
        for p in picks:
            # get_location_for_front is a method; get_price_for_front and
            # get_public_url are properties. Templates call methods for you,
            # Python does not.
            self.stdout.write(
                f"  {p.get_price_for_front:>10}  {p.get_location_for_front():<12} "
                f"{(p.floor_plan or '')[:14]:<14} https://akiyainjapan.com{p.get_public_url}"
            )
