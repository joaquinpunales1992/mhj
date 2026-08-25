import os

# Long-lived Facebook *user* token, read from the environment (.env) so the
# secret stays out of git. refresh_access_token() exchanges it via /me/accounts
# for the (non-expiring) Page token it writes to social_access_token.json.
PAGE_ACCESS_TOKEN = os.getenv("PAGE_ACCESS_TOKEN", "")
PAGE_ID = "612249001976104"

# One place to bump the Graph API version. Meta retires a version roughly two
# years after release, and when it goes the posting calls fail rather than
# degrade — so this is worth checking before it fails on its own.
GRAPH_API_VERSION = "v19.0"
INSTAGRAM_USER_ID = "17841473089014615"

DOMAIN_CONTEXT = (
    "You are a copywriting assistant for a website that sells houses in Japan to foreigners.\n"
    "Your task is to generate short, engaging captions for Facebook posts based solely on the property's location and price.\n"
    "Guidelines:\n"
    "- Maximum total length: 100 characters\n"
    "- Main descriptive portion: max 65 characters\n"
    "- Appeal to foreign buyers—emphasize uniqueness, lifestyle, or investment potential\n"
    "- No emojis, no hashtags\n"
    "- Use natural, friendly language (avoid sales jargon or overly formal tone)\n"
    "- Do not add any extra text—only the caption should be returned\n"
    "- Do not invent or assume features—stick strictly to the given inputs"
)

PRICE_LIMIT_FACEBOOK = 5000
BATCH_SIZE_FACEBOOK = 2

PRICE_LIMIT_INSTAGRAM = 5000
BATCH_SIZE_INSTAGRAM = 2

# Only featured listings are posted. The flag is the shortlist: nothing goes out
# on social unless somebody has marked it in the admin.
#
# Set this False and featured goes back to being an ordering preference —
# featured first, then everything else eligible, cheapest first.
#
# WATCH THE SIZE OF THE SHORTLIST. The queue can only rotate through what is
# flagged, so with one listing marked, that listing is posted on every run
# forever; with three, they cycle. select_properties_to_post logs a warning
# when the pool is smaller than the batch it is being asked for.
POST_ONLY_FEATURED = True

# When POST_ONLY_FEATURED is False, a featured listing still leads the queue.
#
# `featured` alone used to be the first thing the queue sorted on, so a single
# featured 1400万 (US$98,000) listing outranked 1,558 never-posted properties on
# every run, including the 200万 ones directly behind it, and it did so after it
# had already been posted. It could never stop being next.
#
# There was a price cap here, so that only a cheap featured listing led the
# queue. It never fired: the one flagged listing in the table is 1400万 and the
# cap was 500万, so the sort key evaluated the same for every row and the flag
# did nothing at all on social.
#
# The cap existed to stop an expensive featured listing owning the queue. It was
# a second lock on a bolted door: never-posted listings already outrank reposts,
# so a featured one leads once and then rejoins the rotation. Any featured
# listing under the posting price limit now gets that turn.

USE_AI_CAPTION = True


DEFAULT_COMMENT = "Find out More at www.akiyainjapan.com"

# Hashtags are built as: CORE (always on, brand identity) + location-aware tags
# (derived from the property's prefecture/city) + a sampled handful of ROTATING
# tags for variety. This keeps a consistent, relevant count instead of the old
# "random 1..19 tags" behaviour, and drops the #aribnb typo / stray #saga.
CORE_HASHTAGS = [
    "#akiya",
    "#akiyainjapan",
    "#cheaphousesjapan",
    "#myakiyainjapan",
    "#japan",
]

ROTATING_HASHTAGS = [
    "#japanlife",
    "#vacationhouse",
    "#affordablehouse",
    "#japanesehouse",
    "#explorejpn",
    "#livingabroad",
    "#japanrealestate",
    "#japanesearchitecture",
    "#airbnb",
    "#homesforsale",
    "#japanproperty",
    "#japaneselifestyle",
    "#movetojapan",
    "#countrylife",
    "#renovationproject",
]

# How many rotating tags to sample per post (on top of core + location tags).
NUM_ROTATING_HASHTAGS = 6

# Romaji names of Japan's 47 prefectures, used to add a location-aware hashtag
# (e.g. a property in "Akita Prefecture" gets #akita).
JAPAN_PREFECTURES = [
    "Hokkaido", "Aomori", "Iwate", "Miyagi", "Akita", "Yamagata", "Fukushima",
    "Ibaraki", "Tochigi", "Gunma", "Saitama", "Chiba", "Tokyo", "Kanagawa",
    "Niigata", "Toyama", "Ishikawa", "Fukui", "Yamanashi", "Nagano", "Gifu",
    "Shizuoka", "Aichi", "Mie", "Shiga", "Kyoto", "Osaka", "Hyogo", "Nara",
    "Wakayama", "Tottori", "Shimane", "Okayama", "Hiroshima", "Yamaguchi",
    "Tokushima", "Kagawa", "Ehime", "Kochi", "Fukuoka", "Saga", "Nagasaki",
    "Kumamoto", "Oita", "Miyazaki", "Kagoshima", "Okinawa",
]

# Kept as an alias for backwards compatibility with any external references.
HASHTAGS_LIST = CORE_HASHTAGS + ROTATING_HASHTAGS

# --- TikTok ----------------------------------------------------------------
# The reel the Instagram pipeline builds is already what TikTok wants: 9:16,
# MP4, the price and the place burnt in. What is different is the account side.
#
# THE AUDIT. TikTok: "All content posted by unaudited clients will be restricted
# to private viewing mode." Until the app passes review, a post from here is
# visible to nobody but the account owner, and asking for a public privacy level
# is refused rather than downgraded. So this starts at SELF_ONLY — the runs
# work, they are simply private — and becomes PUBLIC_TO_EVERYONE the day the
# audit clears. Nothing else needs to change.
# Overridable from .env, because the day this changes is the day the audit
# clears — and editing a constant and deploying is the wrong amount of ceremony
# for flipping a switch you have been waiting weeks for:
#   TIKTOK_PRIVACY_LEVEL=PUBLIC_TO_EVERYONE
TIKTOK_PRIVACY_LEVEL = os.getenv("TIKTOK_PRIVACY_LEVEL", "SELF_ONLY")

# Comments are where this account earns its reach — see the reply bot — so they
# stay on. Duet and stitch are other people's remixes of our footage, which is
# free distribution and costs us nothing.
TIKTOK_DISABLE_COMMENT = False
TIKTOK_DISABLE_DUET = False
TIKTOK_DISABLE_STITCH = False

# Login Kit is a prerequisite of the Content Posting API in the developer
# portal — it is the OAuth flow the access token comes from — and it grants
# user.info.basic. So the posting page calls /user/info/ and shows the account
# it is about to post as: a scope that is requested and never called is a
# reason on its own for an app review to be refused.
#
# Overridable from .env, because the authorize screen refuses the whole request
# with one word — "scope" — when any single scope is not enabled on the app
# being authorised against, and sandbox and production are configured
# separately. Narrowing it to one scope is how you find out which:
#   TIKTOK_SCOPES=video.publish
TIKTOK_SCOPES = [
    scope.strip()
    for scope in os.getenv("TIKTOK_SCOPES", "user.info.basic,video.publish").split(",")
    if scope.strip()
]

# Seconds. Generous because one of these requests is a whole video going up a
# domestic connection.
TIKTOK_TIMEOUT = 180

# TikTok processes a video after the upload finishes, so the post is not live
# when the API returns. Poll a few times to log what became of it, then stop —
# a cron job holding a connection open to watch an encode is not doing anything
# useful with the wait.
# How long the posting page waits for the command it spawns. The encode plus
# the upload plus the status polls; generous, because being cut off after the
# upload has started is worse than waiting.
TIKTOK_POST_TIMEOUT = 600

TIKTOK_STATUS_POLLS = 3
TIKTOK_STATUS_POLL_SECONDS = 20

# --- Listing carousel cards (tunable) --------------------------------------
# A listing post used to be the scraped photos, unaltered. The price and the
# place — the only two facts that sell an akiya — lived in a caption Instagram
# truncates at ~125 characters, and a photo saved or reshared out of the
# carousel carried nothing at all: no price, no place, no brand.
#
# So the photos are drawn onto branded 4:5 cards first (social/content/
# listing_cards.py). Set this to False to go back to posting the raw photos —
# the same fallback the code takes on its own if rendering or hosting fails.
LISTING_CARDS_ENABLED = True

# Instagram allows 10 carousel items; 4 photos plus the closing card is what
# people actually swipe through, and every extra slide is another render and
# another upload on a slow box.
LISTING_CARDS_MAX_PHOTOS = 4

# End on a card that repeats the price and points at the listing, rather than on
# a photo. This is the slide people are looking at when they decide to click.
LISTING_CARDS_ADD_SUMMARY = True

# How long a rendered card is kept on disk. Five JPEGs per post, twice a day, in
# two directories is most of a gigabyte a year on a box that hasn't got one to
# spare; Instagram has fetched them within seconds of posting. 0 keeps them
# forever.
LISTING_CARDS_KEEP_DAYS = 30

# --- Reel video style (tunable) -------------------------------------------
# Reels are vertical 9:16. The server has very little RAM (it OOM-killed at
# 1080x1920), so we default to 720x1280 and FIT photos onto the canvas
# (downscale only — never upscale, which is what blew up memory). If it still
# gets Killed, drop to 540x960. Ken Burns is CPU-heavy; enable only once the
# VPS proves it can keep up.
REEL_WIDTH = 540
REEL_HEIGHT = 960
REEL_BG_COLOR = (17, 17, 17)          # dark backdrop behind the photo
REEL_ENABLE_KEN_BURNS = False         # slow zoom on each photo
REEL_KEN_BURNS_ZOOM = 0.08            # 8% zoom over each slide
REEL_CROSSFADE = 0.4                  # seconds of crossfade between slides
REEL_BRAND_TEXT = "akiyainjapan.com"  # persistent watermark

# The price is the hook in this niche — a $15,000 house stops a scroll that no
# amount of copy about quiet escapes will. So it goes at the top, large, where
# the eye lands on frame one, and the AI phrase moves down to the lower band.
REEL_HOOK_PRICE_FIRST = True
# Longer place names are truncated rather than shrunk: moviepy raises if text
# does not fit its caption box, and losing the whole overlay to a long address
# would cost the watermark too. Cut on a word boundary, and enough of them to
# name a town rather than just a prefecture — the card says "Oaza Inano, Usuki
# City, Oita Prefecture" and the reel of the same house should not say less than
# it has room for.
REEL_HOOK_PLACE_MAX_CHARS = 44

# Reels also appear in the main feed and the profile grid. There is no reason to
# publish into the Reels tab alone — it is the same video with less shelf space.
REEL_SHARE_TO_FEED = True


# --- Community manager -----------------------------------------------------
# The bot used to have exactly one thing to say: here is a house. These settings
# govern the layer that decides what else is worth saying today, and in which
# medium. Nothing here is a schedule — cron decides how often the planner runs,
# the planner decides what it does when it runs.

# Base weight per format, before performance is taken into account. Listings
# still lead: they are the reason the account exists. The rest exist so the feed
# is worth following between listings.
CONTENT_WEIGHTS = {
    "listing": 4.0,
    "news": 2.0,
    "data": 2.0,
    "faq": 2.0,
}

# How long before the same subject may be posted again. News is keyed by URL and
# never repeats. Listings are governed by the existing property queue.
CONTENT_COOLDOWN_DAYS = {
    "faq": 60,
    "data": 14,
}

# Autonomy. False means the planner posts on its own, which is the point of it.
# Flip to True and everything lands in Content drafts for approval instead —
# worth doing for a week if you want to watch what it would have said.
SOCIAL_REQUIRE_APPROVAL = False

# --- News ------------------------------------------------------------------
# Google News RSS, queried rather than a fixed publication list, so the net is
# as wide as the subject deserves. Each query is a separate feed fetch.
# Queries specific enough that anything they return is on-topic. Google matches
# the article body as well as the headline, so these are taken on trust: a story
# genuinely about akiya does not always say so in its headline.
NEWS_QUERIES_SPECIFIC = [
    'akiya japan',
    '"vacant homes" OR "abandoned homes" japan house',
    'japan countryside depopulation houses',
    '"kominka" OR "machiya" japan house',
]

# Broader queries, worth casting for but not worth trusting — whatever they
# return still has to pass the keyword tiers below.
NEWS_QUERIES_BROAD = [
    'japan property market foreign buyers',
    '"rural japan" moving OR renovation OR village',
    'japan house prices countryside',
]

# Alias for anything importing the old flat list.
NEWS_QUERIES = NEWS_QUERIES_SPECIFIC + NEWS_QUERIES_BROAD

NEWS_FEED_URL = "https://news.google.com/rss/search"

# Anything older than this is not news, and posting it as though it were is the
# fastest way to look automated.
NEWS_MAX_AGE_DAYS = 10

# Outlets we will not repost from: content farms and aggregators that rewrite
# other people's reporting. Matched as a substring of the source name.
NEWS_SOURCE_BLOCKLIST = [
    "msn.com",
    "news break",
    "newsbreak",
    "biztoc",
    "yahoo entertainment",
]

# A headline shorter than this is usually a truncated feed artefact rather than
# a story.
NEWS_MIN_HEADLINE_CHARS = 30

# Publisher feeds, checked in addition to the Google News queries. These carry
# the real article URL, which Google News no longer does (its links are opaque
# tokens that only resolve in a browser). That matters on Facebook, where a link
# is clickable; on Instagram it is dead text either way, which is why
# attribution there is by outlet name.
#
# These are general Japan feeds, so items are keyword-filtered against
# NEWS_KEYWORDS below rather than taken wholesale.
NEWS_RSS_FEEDS = [
    ("The Japan Times", "https://www.japantimes.co.jp/feed/"),
    ("SoraNews24", "https://soranews24.com/feed/"),
    ("Nippon.com", "https://www.nippon.com/en/feed/"),
    ("Unseen Japan", "https://unseen-japan.com/feed/"),
]

# What makes a general Japan story relevant to an akiya audience, in two tiers.
# A single boolean keyword list was too loose: "real estate" alone pulled in a
# story about a ninja theme park closing over a property dispute, which is not
# what this account is about. A strong term is enough on its own; weak ones have
# to corroborate each other.
NEWS_KEYWORDS_STRONG = [
    "akiya", "vacant house", "vacant home", "vacant homes",
    "abandoned house", "abandoned home", "abandoned homes",
    "empty house", "empty home", "empty homes",
    "kominka", "machiya", "minka", "depopulation",
    "rural japan", "countryside", "abandoned village",
]

NEWS_KEYWORDS_WEAK = [
    "rural", "village", "renovation", "renovate", "real estate",
    "property market", "housing market", "house price", "land price",
    "moving to japan", "move to japan", "second home", "inheritance",
    "population decline", "shrinking", "relocate", "homeowner",
]

# Kept as an alias: anything still importing the flat list gets both tiers.
NEWS_KEYWORDS = NEWS_KEYWORDS_STRONG + NEWS_KEYWORDS_WEAK
