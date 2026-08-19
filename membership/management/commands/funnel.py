"""What happened at the two places money changes hands.

    manage.py funnel            last 30 days
    manage.py funnel --days 7
    manage.py funnel --all

Exists because the interesting number is not how many people paid — it is how
many started and did not. Both flows record an attempt before the visitor leaves
for PayPal, so an abandoned checkout is visible rather than being indistinguishable
from nobody having tried.

Read-only. Safe to run on production as often as you like.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone


class Command(BaseCommand):
    help = "Consultation and Pro funnels: attempted, abandoned, paid."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=30,
                            help="How far back to look (default 30).")
        parser.add_argument("--all", action="store_true",
                            help="Ignore --days and report everything.")
        parser.add_argument("--detail", action="store_true",
                            help="List the individual attempts, most recent first.")

    def handle(self, *args, **options):
        from membership.models import Consultation, Subscription

        now = timezone.now()
        if options["all"]:
            since, label = None, "all time"
        else:
            since = now - timedelta(days=options["days"])
            label = f"last {options['days']} days"

        self.stdout.write(self.style.MIGRATE_HEADING(f"\nConsultations — {label}"))
        bookings = Consultation.objects.all()
        if since:
            bookings = bookings.filter(created_at__gte=since)

        paid = bookings.filter(
            status__in=(Consultation.STATUS_PAID, Consultation.STATUS_COMPLETED)
        )
        # An expired hold is somebody who reached PayPal and never came back.
        abandoned = bookings.filter(
            status=Consultation.STATUS_HOLD, hold_expires_at__lte=now
        )
        in_checkout = bookings.filter(
            status=Consultation.STATUS_HOLD
        ).filter(Q(hold_expires_at__gt=now) | Q(hold_expires_at__isnull=True))
        cancelled = bookings.filter(status=Consultation.STATUS_CANCELLED)

        total = bookings.count()
        self._row("started checkout", total)
        self._row("paid", paid.count(), total, good=True)
        self._row("abandoned at PayPal", abandoned.count(), total)
        self._row("cancelled at PayPal", cancelled.count(), total)
        if in_checkout.exists():
            self._row("in checkout right now", in_checkout.count(), total)

        revenue = sum((b.amount or 0) for b in paid)
        if revenue:
            currency = paid.first().currency or ""
            self.stdout.write(f"    revenue            {revenue} {currency}")

        upcoming = Consultation.objects.filter(
            status=Consultation.STATUS_PAID, starts_at__gte=now
        ).order_by("starts_at")
        if upcoming.exists():
            self.stdout.write("\n  Calls still to happen:")
            for b in upcoming[:10]:
                self.stdout.write(
                    f"    {b.starts_at:%a %d %b %H:%M} UTC  {b.name} <{b.email}>"
                )
        else:
            self.stdout.write("\n  No calls booked ahead.")

        self.stdout.write(self.style.MIGRATE_HEADING(f"\nPro — {label}"))
        subs = Subscription.objects.all()
        if since:
            subs = subs.filter(created_at__gte=since)

        started = subs.count()
        active = subs.filter(status=Subscription.STATUS_ACTIVE)
        pending = subs.filter(status=Subscription.STATUS_APPROVAL_PENDING)
        gone = subs.exclude(
            status__in=(Subscription.STATUS_ACTIVE,
                        Subscription.STATUS_APPROVAL_PENDING)
        )

        self._row("started checkout", started)
        self._row("active", active.count(), started, good=True)
        self._row("started, never approved", pending.count(), started)
        self._row("cancelled / expired / suspended", gone.count(), started)

        if pending.exists():
            self.stdout.write("\n  Started Pro and did not finish — worth an email:")
            for s in pending.order_by("-created_at")[:10]:
                self.stdout.write(
                    f"    {s.created_at:%d %b %H:%M}  {s.user.email or s.user.username}"
                )

        self.stdout.write(self.style.MIGRATE_HEADING(f"\nInspection requests — {label}"))
        from membership.models import InspectionRequest

        insp = InspectionRequest.objects.all()
        if since:
            insp = insp.filter(created_at__gte=since)
        asked = insp.count()
        self._row("asked for an inspection", asked)
        self._row("still needs a reply", insp.filter(
            status=InspectionRequest.STATUS_NEW).count(), asked)
        self._row("quoted", insp.filter(
            status=InspectionRequest.STATUS_QUOTED).count(), asked)
        self._row("inspection booked", insp.filter(
            status=InspectionRequest.STATUS_BOOKED).count(), asked, good=True)
        self._row("no access / declined", insp.filter(
            status__in=(InspectionRequest.STATUS_UNAVAILABLE,
                        InspectionRequest.STATUS_DECLINED)).count(), asked)

        owed = insp.filter(status=InspectionRequest.STATUS_NEW).order_by("created_at")
        if owed.exists():
            self.stdout.write("\n  Waiting on you — oldest first:")
            for r in owed[:10]:
                self.stdout.write(
                    f"    {r.created_at:%d %b %H:%M}  {r.email:<32} "
                    f"{r.listing_location or 'no location'}"
                )

        if options["detail"]:
            self.stdout.write(self.style.MIGRATE_HEADING("\nEvery consultation attempt"))
            for b in bookings.order_by("-created_at")[:40]:
                state = "abandoned" if b.is_expired_hold else b.status
                self.stdout.write(
                    f"    {b.created_at:%d %b %H:%M}  {state:<10} "
                    f"{b.name[:24]:<26} {b.email}"
                )

        if total == 0 and started == 0 and asked == 0:
            self.stdout.write(self.style.WARNING(
                "\n  Nothing in this window. If you expected traffic, check that "
                "/consultation/ shows slots and /pro/ shows the subscribe button."
            ))
        self.stdout.write("")

    def _row(self, label, value, of=None, good=False):
        share = ""
        if of:
            share = f"  ({100 * value // of}%)" if of else ""
        text = f"    {label:<32} {value}{share}"
        if good and value:
            self.stdout.write(self.style.SUCCESS(text))
        else:
            self.stdout.write(text)
