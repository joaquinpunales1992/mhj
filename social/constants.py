PAGE_ACCESS_TOKEN = ""
PAGE_ID = "612249001976104"
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

# --- Reel video style (tunable) -------------------------------------------
# Reels are vertical 9:16. If ffmpeg is OOM-killed on the server again, the
# cheapest lever is to drop these to 720x1280 (≈40% fewer pixels per frame),
# then to set REEL_ENABLE_KEN_BURNS = False.
REEL_WIDTH = 1080
REEL_HEIGHT = 1920
REEL_BG_COLOR = (17, 17, 17)          # dark backdrop behind the photo
REEL_ENABLE_KEN_BURNS = False         # slow zoom on each photo (CPU-heavy; enable once the VPS proves it can keep up)
REEL_KEN_BURNS_ZOOM = 0.08            # 8% zoom over each slide
REEL_CROSSFADE = 0.4                  # seconds of crossfade between slides
REEL_BRAND_TEXT = "akiyainjapan.com"  # persistent watermark
