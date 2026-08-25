"""The questions we answer, and the facts we answer them with.

Why a hand-written bank rather than asking the model what it knows: an account
that sells houses to foreigners cannot afford a confidently wrong answer about
whether buying grants a visa, or what a purchase costs on top of the price.
People make plans on the back of these posts.

So the division of labour is: this file states the facts, and the model is only
allowed to phrase them (see faq.py — the prompt forbids adding anything). If a
fact is not in `facts`, it cannot appear on the card.

`needs_review` marks answers that state something specific enough to go out of
date — a rate, a fee, a legal rule. Those are held for a human read every time,
not once. Everything here is general information about how akiya purchases
work, and the caption says as much; it is not advice about a specific deal.
"""

# Keep answers to roughly 3 short facts. A card is read in about two seconds on
# a phone, and the value of an FAQ post is that it answers ONE thing clearly.
FAQ_BANK = [
    {
        "key": "can-foreigners-buy",
        "question": "Can foreigners buy a house in Japan?",
        "facts": [
            "Yes. Japan puts no nationality or residency restriction on buying "
            "property — you do not need to live here, and you do not need a visa.",
            "You buy the land and the building outright (freehold), the same as "
            "a Japanese buyer would.",
            "You can do it without ever having lived in Japan, though you will "
            "need someone on the ground for the paperwork.",
        ],
        "needs_review": False,
    },
    {
        "key": "does-buying-give-visa",
        "question": "Does buying a house get me a visa for Japan?",
        "facts": [
            "No. Owning property in Japan gives you no residency right and no "
            "extra time on a tourist entry.",
            "Property ownership and immigration status are two completely "
            "separate systems in Japan — one does not feed the other.",
            "Plenty of owners use their house on ordinary visa-free short stays, "
            "and come and go.",
        ],
        "needs_review": False,
    },
    {
        "key": "what-is-akiya",
        "question": "What exactly is an akiya?",
        "facts": [
            "An akiya (空き家) is simply a vacant house. It is not a category of "
            "quality or a type of building.",
            "Japan has millions of them, mostly because of an ageing and "
            "shrinking rural population rather than because of anything wrong "
            "with the houses.",
            "They range from move-in-ready to a shell that needs everything. The "
            "price usually tells you which.",
        ],
        "needs_review": False,
    },
    {
        "key": "why-so-cheap",
        "question": "Why are these houses so cheap?",
        "facts": [
            "In Japan a house is treated as a depreciating asset, not a store of "
            "value — an older building is often valued at close to nothing, and "
            "what you are really paying for is the land.",
            "Rural depopulation means far more houses than buyers in the areas "
            "these listings come from.",
            "Inherited houses are often a cost to the family — tax and upkeep — "
            "so selling cheaply and quickly beats holding.",
        ],
        "needs_review": False,
    },
    {
        "key": "costs-on-top",
        "question": "What do I pay on top of the purchase price?",
        "facts": [
            "Budget meaningfully more than the sticker price. The usual items are "
            "the agent's commission, a judicial scrivener to register the "
            "transfer, stamp duty, a registration tax and a one-off real estate "
            "acquisition tax.",
            "On a cheap house these costs do not shrink with the price, so as a "
            "share of a very low purchase price they can look enormous.",
            "Rates and exemptions differ by municipality and change over time — "
            "get the actual figures for the specific property before you commit.",
        ],
        "needs_review": True,
    },
    {
        "key": "annual-costs",
        "question": "What does it cost to own, every year?",
        "facts": [
            "There is an annual fixed asset tax, charged on the municipality's "
            "assessed value rather than what you paid.",
            "On a cheap rural house that bill is usually modest, but it does not "
            "go away while the house sits empty.",
            "Then the real running costs: utilities, and someone to keep an eye "
            "on the place if you are not living in it.",
        ],
        "needs_review": True,
    },
    {
        "key": "renovation-cost",
        "question": "Will renovation cost more than the house?",
        "facts": [
            "Very often, yes — and that is normal in this market rather than a "
            "sign you bought badly.",
            "The items that decide the number are the roof, the foundations, "
            "wiring, plumbing, insulation and any water damage.",
            "This is why an inspection before you buy is worth far more on a "
            "cheap house than on an expensive one.",
        ],
        "needs_review": False,
    },
    {
        "key": "mortgage",
        "question": "Can I get a mortgage as a non-resident?",
        "facts": [
            "Realistically, assume cash. Japanese banks lend against residency, "
            "local income and a long credit history here.",
            "Some buyers borrow in their own country instead, against something "
            "they already own there.",
            "At these prices most purchases in this market are cash anyway, which "
            "is part of why they move fast.",
        ],
        "needs_review": False,
    },
    {
        "key": "akiya-bank",
        "question": "What is an akiya bank?",
        "facts": [
            "It is a municipal listing service — a town publishing the empty "
            "houses in its own area, not a national database.",
            "Each one sets its own rules, and some list only to people who intend "
            "to move there or start something locally.",
            "Coverage is patchy and the sites are Japanese-only, which is most of "
            "why these houses are hard to find from abroad.",
        ],
        "needs_review": False,
    },
    {
        "key": "do-i-have-to-live-there",
        "question": "Do I have to live in the house?",
        "facts": [
            "For an ordinary private sale, no — nobody requires you to occupy it.",
            "Some municipal akiya bank listings do attach a residency or "
            "renovation condition, and that is set by the town, not the seller.",
            "An empty house still needs airing, checking and small repairs, so "
            "'leave it alone' is rarely the cheap option it sounds like.",
        ],
        "needs_review": False,
    },
    {
        "key": "can-i-rent-it-out",
        "question": "Can I rent it out to travellers?",
        "facts": [
            "Short-term letting is regulated in Japan and the rules depend on the "
            "municipality — some are welcoming, some effectively are not.",
            "It usually means registering the property and meeting fire and "
            "safety requirements, not simply listing it online.",
            "Check what the specific town allows before you buy on the strength "
            "of rental income.",
        ],
        "needs_review": True,
    },
    {
        "key": "how-to-start",
        "question": "How do I actually start?",
        "facts": [
            "Decide the region first. Access, winters and how far you are from a "
            "hospital and a station matter more than the house does.",
            "Then set a total budget — purchase, buying costs and renovation as "
            "one number, not three separate hopes.",
            "Then look at real listings, and get someone to check the ones you "
            "are serious about before you travel.",
        ],
        "needs_review": False,
    },
]

FAQ_BY_KEY = {entry["key"]: entry for entry in FAQ_BANK}

# Days before the same question may be drafted again. A follower who saw the
# visa answer last week does not need it again; a new follower does, eventually.
FAQ_COOLDOWN_DAYS = 60
