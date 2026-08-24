"""One-time TikTok authorisation, and a way to check it later.

The API needs a user access token, which only a human clicking "allow" in a
browser can produce. This command is both halves of that: it prints the URL to
open, and it turns the code the browser comes back with into the stored tokens
everything else uses.
"""

from django.core.management.base import BaseCommand

from social import tiktok


class Command(BaseCommand):
    help = "Authorise the TikTok app, or check the stored token."

    def add_arguments(self, parser):
        parser.add_argument(
            "--code",
            help="The ?code= value TikTok put in the redirect URL. Run with no "
                 "arguments first to get the link that produces it.",
        )
        parser.add_argument(
            "--redirect-uri",
            help="Overrides TIKTOK_REDIRECT_URI. Must match the app's "
                 "registered redirect exactly, including the trailing slash.",
        )
        parser.add_argument(
            "--check", action="store_true",
            help="Refresh the stored token and report which account it posts as.",
        )

    def handle(self, *args, **options):
        from django.conf import settings

        redirect_uri = options["redirect_uri"] or settings.TIKTOK_REDIRECT_URI

        try:
            if options["check"]:
                return self.report_account()
            if options["code"]:
                return self.exchange(options["code"], redirect_uri)
            return self.start(redirect_uri)
        except tiktok.TikTokError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))

    def start(self, redirect_uri):
        # Built before anything is printed: with no client key this raises, and
        # "1. Open this URL" followed by no URL and an error is a worse thing to
        # read than the error on its own.
        url = tiktok.authorize_url(redirect_uri)
        self.stdout.write("1. Open this URL and approve the app:\n")
        self.stdout.write(self.style.SUCCESS(url))
        self.stdout.write(
            "\n2. TikTok redirects to your redirect URI with ?code=... in the "
            "address bar. The page itself does not have to work — only the URL "
            "matters. Copy the code and run:\n"
        )
        self.stdout.write("   manage.py tiktok_auth --code THE_CODE\n")
        self.stdout.write(
            "\nThe code is single-use and expires within minutes, so do the "
            "second step straight away."
        )

    def exchange(self, code, redirect_uri):
        tokens = tiktok.exchange_code(code, redirect_uri)
        self.stdout.write(self.style.SUCCESS(
            f"Stored tokens for open_id {tokens.get('open_id', '?')}."
        ))
        self.report_account()

    # NOT `check`: BaseCommand.check is Django's system-check hook, called by
    # execute() before handle() ever runs. Overriding it made every invocation
    # of this command try to reach TikTok during startup.
    def report_account(self):
        token = tiktok.get_fresh_token()
        creator = tiktok.query_creator_info(token)
        self.stdout.write(self.style.SUCCESS(
            f"Posting as @{creator.get('creator_username', '?')} "
            f"({creator.get('creator_nickname', '?')})"
        ))
        options = creator.get("privacy_level_options") or []
        self.stdout.write(f"Privacy levels this account allows: {options}")
        if options == ["SELF_ONLY"]:
            self.stdout.write(self.style.WARNING(
                "SELF_ONLY is the only option, which means the app has not "
                "passed TikTok's audit yet: everything it posts will be "
                "private. Posting still works — nobody else can see it."
            ))
