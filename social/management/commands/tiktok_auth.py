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
        from django.conf import settings

        # Which credentials are in play, said out loud. Sandbox and production
        # are different apps with different keys, and having the wrong pair in
        # .env looks exactly like a broken integration.
        key = settings.TIKTOK_CLIENT_KEY or "(unset)"
        self.stdout.write(f"Client key: {key}")
        self.stdout.write(f"Token file: {tiktok._token_path()}")

        granted = tiktok.load_tokens().get("scope", "")
        self.stdout.write(f"Granted scopes: {granted or '(unknown)'}")

        token = tiktok.get_fresh_token()
        creator = tiktok.query_creator_info(token)
        self.stdout.write(self.style.SUCCESS(
            f"Posting as @{creator.get('creator_username', '?')} "
            f"({creator.get('creator_nickname', '?')})"
        ))
        from social.constants import TIKTOK_PRIVACY_LEVEL

        options = creator.get("privacy_level_options") or []
        self.stdout.write(f"Privacy levels this account allows: {options}")
        self.stdout.write(
            "Account reports: "
            f"comments {'off' if creator.get('comment_disabled') else 'on'}, "
            f"duet {'off' if creator.get('duet_disabled') else 'on'}, "
            f"stitch {'off' if creator.get('stitch_disabled') else 'on'}"
        )
        self.stdout.write(f"Configured privacy level: {TIKTOK_PRIVACY_LEVEL}")

        # An unaudited app can only post to a private account, and this is how
        # to tell one from the other without leaving the terminal: a private
        # account cannot be duetted or stitched, and is not offered
        # PUBLIC_TO_EVERYONE. Both readings are shown because either can be
        # stale by a few minutes after the switch is flipped in the app.
        looks_public = (
            "PUBLIC_TO_EVERYONE" in options
            or not creator.get("duet_disabled")
            or not creator.get("stitch_disabled")
        )
        if looks_public:
            self.stdout.write(self.style.WARNING(
                "This account still looks PUBLIC to TikTok. An unaudited app "
                "can only post to a private account, so posting will be refused "
                "with unaudited_client_can_only_post_to_private_accounts until "
                "the account itself is switched to private in the TikTok app "
                "(Settings and privacy > Privacy > Private account). TikTok can "
                "take a few minutes to report the change here."
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                "This account looks private, which is what an unaudited app "
                "needs. Posts will be visible only to the account owner until "
                "the app passes review."
            ))

        if TIKTOK_PRIVACY_LEVEL != "SELF_ONLY":
            self.stdout.write(self.style.WARNING(
                f"TIKTOK_PRIVACY_LEVEL is {TIKTOK_PRIVACY_LEVEL}. Until the app "
                "is audited, anything other than SELF_ONLY is refused. Unset it "
                "in .env, or set it to SELF_ONLY, until approval comes through."
            ))
