"""Generate a pre-purchase desk report for one listing.

    manage.py desk_report 969                     write desk-report-969.html
    manage.py desk_report 969 --out ~/report.html
    manage.py desk_report 969 --text              read it in the terminal first
    manage.py desk_report 969 --verdict "..."     fill in the verdict
    manage.py desk_report 969 --final             drop the draft marking

Produces the automatable part: the findings, the price context, the published
record and the questions for the agent. The three parts that need a person are
listed in the output as outstanding, and the report marks itself a draft until
--final is passed — so a half-written one cannot go out looking finished.

See inventory.desk_report for what each finding means and why the rules refuse to
guess at anything the listing does not say.
"""

from django.core.management.base import BaseCommand, CommandError
from django.template.loader import render_to_string
from django.utils import timezone

from inventory.desk_report import CRITICAL, SEVERITY_ORDER, UNKNOWN, build_report
from inventory.models import Property
from inventory.utils import YEN_TO_USD


class Command(BaseCommand):
    help = "Generate a pre-purchase desk report for one property."

    def add_arguments(self, parser):
        parser.add_argument("pk", type=int, help="Property id.")
        parser.add_argument("--out", help="Where to write the HTML.")
        parser.add_argument("--text", action="store_true",
                            help="Print a plain-text summary instead of writing HTML.")
        parser.add_argument("--verdict", default="",
                            help="The verdict paragraph, once you have written it.")
        parser.add_argument("--final", action="store_true",
                            help="Remove the draft marking. Use when the three "
                                 "human sections are genuinely done.")

    def handle(self, *args, **options):
        try:
            property = Property.objects.get(pk=options["pk"])
        except Property.DoesNotExist:
            raise CommandError(f"No property with id {options['pk']}.")

        report = build_report(property)

        if options["text"]:
            self._print_text(report, property)
            return

        context = dict(
            report,
            report_date=timezone.now().date(),
            verdict=options["verdict"],
            draft=not options["final"],
            open_sections=len(report["human_sections"]),
            inventory_size=Property.objects.filter(show_in_front=True).count(),
            yen_to_usd=YEN_TO_USD,
        )
        html = render_to_string("desk_report.html", context)

        path = options["out"] or f"desk-report-{property.pk}.html"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(html)

        counts = report["counts"]
        self.stdout.write(self.style.SUCCESS(f"\nWrote {path}"))
        self.stdout.write(
            f"  {counts[CRITICAL]} critical · {counts[UNKNOWN]} not stated · "
            f"{counts['caution']} caution · {counts['cleared']} clear\n"
        )
        if not options["final"]:
            self.stdout.write(
                "  Marked as a draft. Write the municipal enquiry, the reading of\n"
                "  the Japanese remarks and the verdict, then re-run with "
                "--final.\n"
            )

    def _print_text(self, report, property):
        """Terminal view — for deciding whether a listing is worth reporting on
        at all before any of it goes to a customer."""
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{property.title}"))
        self.stdout.write(
            f"  {property.get_price_for_front} · {property.location}\n"
        )

        for severity in SEVERITY_ORDER:
            for finding in report["findings"]:
                if finding["severity"] != severity:
                    continue
                label = f"[{finding['severity_label'].upper()}]"
                style = (self.style.ERROR if severity == CRITICAL
                         else self.style.WARNING if severity in (UNKNOWN, "caution")
                         else self.style.SUCCESS)
                self.stdout.write(style(f"  {label:<13} {finding['title']}"))
                if finding["source_label"]:
                    self.stdout.write(
                        f"                {finding['source_label']}: "
                        f"{finding['source_value']}"
                    )

        comparison = report["comparison"]
        if comparison:
            self.stdout.write(
                f"\n  Price: {comparison['value_per_m2_display']} vs "
                f"{comparison['area']} {comparison['area_range_display']} "
                f"({comparison['percentile']}th percentile of "
                f"{comparison['sample_size']})"
            )

        self.stdout.write(f"\n  {len(report['questions'])} questions for the agent.")
        if report["withheld"]:
            headings = ", ".join(row["heading"].lower()
                                 for row in report["withheld"])
            self.stdout.write(f"  Listing is silent on: {headings}.")
        self.stdout.write("")
