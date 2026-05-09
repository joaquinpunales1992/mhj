from django.core.management.base import BaseCommand, CommandError

from scrapper.constants import PREFECTURE_SLUGS
from scrapper.scrapper import run_source
from scrapper.sources import SOURCES


class Command(BaseCommand):
    help = "Pull properties from a supported listing site."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            choices=sorted(SOURCES.keys()),
            required=True,
            help="Listing site to pull from.",
        )
        parser.add_argument(
            "--region",
            type=str,
            help="Prefecture slug (e.g. tokyo). If omitted, all prefectures are pulled.",
        )
        parser.add_argument("--page-from", type=int, default=1)
        parser.add_argument("--page-to", type=int, default=50)
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Fetch and parse but do not write to the database.",
        )

    def handle(self, *args, **options):
        source = options["source"]
        region = options.get("region")
        page_from = options["page_from"]
        page_to = options["page_to"]
        dry_run = options["dry_run"]

        regions = [region] if region else PREFECTURE_SLUGS
        if region and region not in PREFECTURE_SLUGS:
            raise CommandError(
                f"Unknown region {region!r}. Known: {', '.join(PREFECTURE_SLUGS)}"
            )

        for slug in regions:
            try:
                run_source(
                    source=source,
                    region=slug,
                    page_from=page_from,
                    page_to=page_to,
                    dry_run=dry_run,
                )
                self.stdout.write(
                    self.style.SUCCESS(f"Done: {source} / {slug}")
                )
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"{source} / {slug} failed: {exc}")
                )
