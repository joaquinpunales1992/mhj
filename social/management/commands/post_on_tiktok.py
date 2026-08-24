from django.core.management.base import BaseCommand

from membership.utils import notify_social_token_expired
from social.utils import post_tiktok_reel


class Command(BaseCommand):
    help = "Post the next queued property to TikTok"

    def add_arguments(self, parser):
        parser.add_argument(
            "--privacy-level",
            help="PUBLIC_TO_EVERYONE, SELF_ONLY, … Defaults to "
                 "TIKTOK_PRIVACY_LEVEL. Whatever is given is still checked "
                 "against what the account allows.",
        )
        for name in ("comment", "duet", "stitch"):
            parser.add_argument(
                f"--disable-{name}", action="store_true",
                help=f"Turn {name} off on this post.",
            )

    def handle(self, *args, **options):
        try:
            posted = post_tiktok_reel(
                privacy_level=options.get("privacy_level"),
                options={
                    "disable_comment": options.get("disable_comment", False),
                    "disable_duet": options.get("disable_duet", False),
                    "disable_stitch": options.get("disable_stitch", False),
                },
            )
            if posted:
                self.stdout.write(self.style.SUCCESS("Posted to TikTok."))
            else:
                self.stderr.write("Nothing was posted; see the log.")
        except Exception as e:
            notify_social_token_expired()
            self.stderr.write(self.style.ERROR(f"Error on TikTok post: {e}"))
