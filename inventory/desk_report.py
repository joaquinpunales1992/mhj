"""Assemble a pre-purchase desk report for one listing.

What this is: the automatable part of a paid report — the findings, the price
context, the published record, and the questions to put to the agent — derived
from fields already in the database. It is explicitly *not* the whole product.
Three things need a person, and the report says so on its face rather than
guessing: the municipal enquiry, a proper reading of the Japanese remarks, and
the verdict paragraph.

Why a desk report and not an inspection: an inspection needs physical access to
a house whose access the seller's agent controls, so it cannot honestly be sold
in advance (see membership.models.InspectionRequest). Desk research has no such
constraint. Everything here is answerable from the listing plus our own
inventory.

Two rules the findings obey, because the report will be read by someone deciding
whether to spend six figures:

  1. Silence is reported as silence. Where the listing gives no utilities line,
     the finding says the listing does not say — it never estimates. A guess
     dressed as a finding is the one thing that would make this product a
     liability rather than an asset.

  2. A designation is not a determination. "Inside an urbanization control area"
     is a fact from the listing; "you cannot rebuild here" is a decision only the
     municipal office can give. The findings state what a designation generally
     means and then send the reader to ask.

Severity is about what it does to the purchase, not how alarming it sounds:
CRITICAL can void the deal, CAUTION changes the price or the plan, UNKNOWN is a
gap that must be closed before offering, CLEARED is a thing checked and fine.
"""

import re

from inventory.utils import parse_area_to_m2

CRITICAL = "critical"
CAUTION = "caution"
UNKNOWN = "unknown"
CLEARED = "cleared"

SEVERITY_LABEL = {
    CRITICAL: "Critical",
    CAUTION: "Caution",
    UNKNOWN: "Not stated",
    CLEARED: "Clear",
}
# Report order. Unknowns rank above cautions on purpose: a gap you have not
# closed is more dangerous than a risk you have priced.
SEVERITY_ORDER = [CRITICAL, UNKNOWN, CAUTION, CLEARED]

# The year the current earthquake standard (新耐震) came in. A building older
# than this is a different risk and financing class.
NEW_SEISMIC_STANDARD_YEAR = 1981

# A listing we first saw this long ago, still on sale, is stale enough to be
# worth asking about — either it has not sold or the record is not maintained.
STALE_AFTER_DAYS = 270

# Walk times. Over the first bound the property is realistically car-dependent;
# under the second it is genuinely walkable and worth recording as fine.
WALK_MINUTES_FAR = 30
WALK_MINUTES_CLOSE = 15

# Utilities. Matched against the 設備 segment in either language, since the
# scraper stores the translation and the translation is not stable.
_MAINS = [
    (r"都市ガス|city gas", "city gas"),
    (r"公共水道|上水道|public water|water supply", "mains water"),
    (r"公共下水|下水道|public sewer|public sewage", "public sewer"),
]
_OFF_GRID = [
    (r"プロパン|lp ?ガス|propane|lpg", "propane gas rather than mains"),
    (r"井戸|well water|\bwell\b", "a well rather than mains water"),
    (r"浄化槽|septic", "a septic tank rather than public sewer"),
    (r"汲み取り|vault toilet|night soil", "a collection tank — no sewer connection"),
]

_FARMLAND = r"農地|田$|畑|farmland|rice ?field|rice ?paddy|\bfield\b"
_LEASEHOLD = r"借地|leasehold|lease ?right|lease ?hold"
_PRIVATE_ROAD = r"私道|private road"
_CONTROL_AREA = r"市街化調整区域|urbanization control|urbanisation control"
_SEISMIC_WORK = r"耐震|earthquake[- ]?resist|seismic"
_NO_VALUE = {"", "-", "—", "無", "なし", "none", ".", ","}


def _clean(value):
    return (value or "").strip()


def _has_value(value):
    return _clean(value).lower() not in _NO_VALUE


def _finding(severity, title, body, source_label="", source_value="", questions=()):
    return {
        "severity": severity,
        "severity_label": SEVERITY_LABEL[severity],
        "title": title,
        "body": [p for p in body if p],
        "source_label": source_label,
        "source_value": source_value,
        "questions": list(questions),
    }


def _built_year(property):
    """Construction year out of '1976年7月（築49年）' or 'March 2000'.

    Takes the *first* four-digit year: the parenthetical age that follows on
    Japanese listings contains digits too, and on a 2000s house the age would
    otherwise win.
    """
    match = re.search(r"(1[89]\d{2}|20\d{2})", _clean(property.construction_date))
    return int(match.group(1)) if match else None


# --- Rules -----------------------------------------------------------------
# Each takes the property and returns a finding or None. Order here is only for
# reading; output is sorted by severity.


def _rule_control_area(property):
    if not re.search(_CONTROL_AREA, _clean(property.city_planning), re.I):
        return None
    return _finding(
        CRITICAL,
        "Inside an urbanization control area",
        [
            "Land in a 市街化調整区域 is designated to stay undeveloped. In "
            "general a new dwelling cannot be built there without specific "
            "permission, and permission commonly depends on who is building and "
            "why — an existing house may be repairable and yet not replaceable.",
            "This cannot be answered from the listing. It needs the city's "
            "planning department, on this parcel, in writing, and it is the "
            "question to resolve before offering.",
        ],
        "都市計画", property.city_planning,
        ["Will the city permit a replacement dwelling on this parcel, given it "
         "sits in an urbanization control area? Ask for the answer in writing."],
    )


def _rule_utilities(property):
    equipment = _clean(property.equipment)
    if not _has_value(equipment):
        return _finding(
            UNKNOWN,
            "Water, sewer and gas are not disclosed",
            [
                "The listing gives no utilities line. On a rural property the "
                "realistic possibilities differ by a large amount of money: "
                "mains water and public sewer, versus a well, a septic tank and "
                "propane.",
                "This has to be asked before offering. It is the most expensive "
                "thing a listing can leave out.",
            ],
            "設備", "— no value published",
            ["What are the water, sewer and gas arrangements — mains, well, "
             "septic tank, propane?"],
        )

    off_grid = [label for pattern, label in _OFF_GRID
                if re.search(pattern, equipment, re.I)]
    mains = [label for pattern, label in _MAINS
             if re.search(pattern, equipment, re.I)]

    if off_grid:
        return _finding(
            CAUTION,
            "Off-grid services: " + ", ".join(off_grid),
            [
                "The listing states this property has "
                + ", ".join(off_grid) + ". None of that is a reason not to buy, "
                "but each carries running costs and maintenance a mains "
                "connection does not, and a septic tank has a service interval.",
                "Get the age and condition of each, and a quote for connection "
                "if mains service reaches the road.",
            ],
            "設備", equipment,
            ["How old are the well, tank or bottled-gas installation, and when "
             "were they last serviced?",
             "Does mains water or public sewer reach the road, and what would "
             "connection cost?"],
        )

    if mains:
        return _finding(
            CLEARED,
            "On mains services",
            ["The listing states " + ", ".join(mains) + ". That removes the "
             "largest hidden cost on a rural property."],
            "設備", equipment,
        )

    return _finding(
        UNKNOWN,
        "Utilities line present but unreadable",
        ["The listing publishes a 設備 value that does not name any recognised "
         "water, sewer or gas arrangement. Treat it as undisclosed and ask."],
        "設備", equipment,
        ["What are the water, sewer and gas arrangements?"],
    )


def _rule_seismic(property):
    year = _built_year(property)
    if year is None or year >= NEW_SEISMIC_STANDARD_YEAR:
        return None

    reinforced = re.search(
        _SEISMIC_WORK, f"{property.description} {property.renovation}", re.I
    )
    mitigation = (
        "Mitigated here: the listing describes earthquake-resistance work "
        "already carried out. Establish what was done, by whom, and whether a "
        "conformance certificate can be issued — that certificate is what opens "
        "the mortgage and acquisition-tax reductions."
        if reinforced else
        "No reinforcement work is mentioned. Price a seismic assessment into "
        "the purchase; it also determines whether the tax reductions are "
        "reachable."
    )
    return _finding(
        CAUTION,
        f"Built {year} — before the current earthquake standard",
        [
            f"The 新耐震 standard dates from {NEW_SEISMIC_STANDARD_YEAR}. A "
            f"{year} building predates it, which affects both risk and, for some "
            "buyers, financing and tax treatment.",
            mitigation,
        ],
        "築年月", property.construction_date,
        ["What would an earthquake-resistance conformance certificate cost, and "
         "has any assessment already been done?"],
    )


def _rule_private_road(property):
    road = _clean(property.road_condition)
    if not re.search(_PRIVATE_ROAD, road, re.I):
        return None
    return _finding(
        CAUTION,
        "Access is over a private road",
        [
            "A private road means shared maintenance liability, and its "
            "ownership affects rebuilding rights and some lenders' willingness.",
            "Establish who owns the strip, whether there is a written "
            "agreement, and whether the plot's frontage satisfies the "
            "building-access requirement.",
        ],
        "私道負担・道路", road,
        ["Who owns the private road, and is there a written maintenance and "
         "access agreement?",
         "Does the plot's frontage satisfy the building-access requirement?"],
    )


def _rule_setback(property):
    setback = _clean(property.setback)
    if not _has_value(setback):
        return None
    return _finding(
        CAUTION,
        "A setback is recorded",
        ["Part of the plot must be given up to widen the road when the house is "
         "rebuilt, which reduces the land you can actually build on. Get the "
         "area affected in m²."],
        "セットバック", setback,
        ["How many square metres does the setback take, and is it already "
         "reflected in the stated land area?"],
    )


def _rule_land_category(property):
    category = _clean(property.land_category)
    if not re.search(_FARMLAND, category, re.I):
        return None
    return _finding(
        CRITICAL,
        "The land is registered as farmland",
        [
            "Agricultural land cannot be bought freely. A transfer needs the "
            "local agricultural committee's approval, and the tests it applies "
            "are difficult for a buyer who is not farming the land.",
            "Confirm whether a category change (農地転用) has been granted or is "
            "obtainable before committing to anything.",
        ],
        "地目", category,
        ["Has a change of land category been granted, or is one obtainable for "
         "this parcel?"],
    )


def _rule_tenure(property):
    rights = _clean(property.land_rights)
    if re.search(_LEASEHOLD, rights, re.I):
        return _finding(
            CRITICAL,
            "The land is leasehold, not freehold",
            [
                "You would own the building and rent the ground under it. That "
                "means ground rent, a renewal date, and the landowner's consent "
                "for a rebuild or a sale.",
                "Get the remaining term, the rent, the renewal terms and the "
                "consent conditions in writing before valuing this at all.",
            ],
            "土地権利", rights,
            ["What is the remaining leasehold term, the ground rent, and the "
             "landowner's position on rebuilding and resale?"],
        )
    if not rights or not re.search(r"所有権|ownership", rights, re.I):
        return None

    category = _clean(property.land_category)
    clean_category = category and not re.search(_FARMLAND, category, re.I)
    body = ["Freehold ownership, not a leasehold."]
    if clean_category:
        body.append(
            f"The land category is {category.lower()} — not farmland, which "
            "would have required agricultural committee approval and is the "
            "commonest way an overseas buyer's purchase falls apart."
        )
    return _finding(
        CLEARED, "Land tenure is freehold", body,
        "土地権利", rights,
    )


def _rule_access(property):
    minutes = property.station_walk_minutes
    station = _clean(property.nearest_station)

    if minutes is not None and minutes <= WALK_MINUTES_CLOSE:
        return _finding(
            CLEARED,
            f"{minutes} minutes' walk to {station} Station",
            ["Genuinely walkable rail access, which supports both resale and a "
             "short-term rental case."],
            "交通", f"{station} · {minutes} min on foot",
        )

    if minutes is not None:
        severity = CAUTION if minutes > WALK_MINUTES_FAR else CLEARED
        note = (
            "In practice this is a car-dependent property. It also weakens any "
            "short-term rental case, where walkable rail access does most of the "
            "work."
            if severity == CAUTION else
            "Walkable at a stretch, though most buyers here will want a car."
        )
        return _finding(
            severity,
            f"{minutes} minutes' walk to the nearest station",
            [f"{station} Station is a {minutes}-minute walk.", note],
            "交通", f"{station} · {minutes} min on foot",
        )

    if property.needs_bus:
        return _finding(
            CAUTION,
            "No walkable station — access is by bus",
            ["The listing describes access by bus rather than a walk from a "
             "station. Assume a car is necessary, and do not count on rental "
             "demand that depends on rail access."],
            "交通", _clean(property.traffic)[:120],
        )
    return None


def _rule_stale_listing(property):
    from django.utils import timezone

    reasons = []
    questions = []

    handover = _clean(property.handover)
    handover_date = _parse_month_year(handover)
    if handover_date and handover_date < timezone.now().date():
        reasons.append(
            f"The listing states a handover of {handover}, which is in the past."
        )
        questions.append(
            f"Why has the stated handover date ({handover}) passed — is there an "
            "occupancy or probate issue?"
        )

    first_seen = property.created_at
    age_days = (timezone.now() - first_seen).days if first_seen else None
    if age_days and age_days > STALE_AFTER_DAYS:
        months = age_days // 30
        reasons.append(
            f"It has been in our inventory since {first_seen:%-d %B %Y} — about "
            f"{months} months."
        )

    if not reasons:
        return None

    reasons.append(
        "Either it has not sold or the record is not being maintained. Confirm "
        "it is still available and whether the asking price has moved; a listing "
        "this old is often negotiable."
    )
    questions.append(
        "Is the property still available, and has the asking price changed since "
        "it was listed?"
    )
    return _finding(
        CAUTION, "Stale listing", reasons,
        "引渡可能時期", handover or "—", questions,
    )


def _rule_renovation(property):
    renovation = _clean(property.renovation)
    if not _has_value(renovation):
        return None
    return _finding(
        CLEARED,
        "Renovation work is documented",
        ["The listing records work already carried out: " + renovation.rstrip(".")
         + ". Get dates, invoices and any warranties — documented work is worth "
         "more than described work."],
        "リフォーム", renovation,
    )


def _rule_price_position(property):
    comparison = property.price_per_sqm_comparison()
    if not comparison or comparison["percentile"] < 67:
        return None
    return _finding(
        CAUTION,
        f"Priced in the top third for {comparison['area']}",
        [
            f"At {comparison['value_per_m2_display']} of building area, this sits "
            f"above {comparison['percentile']}% of the "
            f"{comparison['sample_size']} comparable listings we hold in "
            f"{comparison['area']}, where the middle of the market is "
            f"{comparison['area_range_display']}.",
            "That is not an argument against buying — renovation work and land "
            "size can justify it — but it is the number to negotiate from.",
        ],
        "価格", property.get_price_for_front,
    )


RULES = [
    _rule_control_area,
    _rule_land_category,
    _rule_tenure,
    _rule_utilities,
    _rule_seismic,
    _rule_private_road,
    _rule_setback,
    _rule_access,
    _rule_stale_listing,
    _rule_price_position,
    _rule_renovation,
]


def _parse_month_year(text):
    """'July 2025' / '2025年7月' -> a date on the 1st. None if not a month."""
    import datetime

    text = _clean(text)
    if not text:
        return None

    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月", text)
    if match:
        year, month = int(match.group(1)), int(match.group(2))
    else:
        months = ("january february march april may june july august september "
                  "october november december").split()
        match = re.search(r"([A-Za-z]{3,9})\.?\s+(20\d{2})", text)
        if not match:
            return None
        name = match.group(1).lower()
        month = next((i + 1 for i, m in enumerate(months) if m.startswith(name[:3])),
                     None)
        if not month:
            return None
        year = int(match.group(2))
    try:
        return datetime.date(year, month, 1)
    except ValueError:
        return None


# --- Report ----------------------------------------------------------------

# Rows of the published record, as (heading, attribute, Japanese source label).
RECORD_ROWS = [
    ("Structure", "building_structure", "建物構造"),
    ("Layout", "floor_plan", "間取り"),
    ("Building area", "building_area", "建物面積"),
    ("Land area", "land_area", "土地面積"),
    ("Built", "construction_date", "築年月"),
    ("Land rights", "land_rights", "土地権利"),
    ("Land category", "land_category", "地目"),
    ("Zoning", "zoning", "用途地域"),
    ("City planning", "city_planning", "都市計画"),
    ("Coverage ratio", "building_coverage_ratio", "建ぺい率"),
    ("Floor area ratio", "floor_area_ratio", "容積率"),
    ("Road", "road_condition", "私道負担・道路"),
    ("Parking", "parking", "駐車場"),
    ("Utilities", "equipment", "設備"),
    ("Estimated running cost", "estimated_utility_cost", "目安光熱費"),
    ("Insulation", "insulation_performance", "断熱性能"),
    ("Current status", "current_status", "現況"),
    ("Handover", "handover", "引渡可能時期"),
    ("Transaction type", "transaction_type", "取引態様"),
]

# Asked on every report regardless of what the listing says, because every
# purchase needs them and no listing publishes them.
STANDARD_QUESTIONS = [
    "What are the annual fixed-asset and city planning taxes?",
    "Are there neighbourhood association fees, road levies or shared water "
    "charges?",
    "Is there anything the seller knows of that is not in the listing — "
    "boundary disputes, rights of way, unregistered extensions?",
]

# The part a person has to write. Named in the report so a half-finished one
# cannot be mistaken for a complete one.
HUMAN_SECTIONS = [
    ("Municipal enquiry", "Call or write to the relevant municipal office about "
     "the planning designation on this parcel, and record the answer verbatim."),
    ("The Japanese remarks, read properly", "The stored description is machine "
     "translated. Read the source listing's 備考 and note anything the "
     "translation lost — rebuild restrictions and defect disclosures hide here."),
    ("Verdict", "Two or three sentences: is this worth pursuing, at what price, "
     "and on what conditions."),
]


# The price rule is the only one that queries: it compares against every listing
# in the prefecture. Excluded from the preview so a property page can show real
# findings without paying for that scan on every view.
_QUERYING_RULES = (_rule_price_position,)


def preview_findings(property):
    """The findings only, cheaply, for the teaser on a property page.

    Real findings for the property being viewed — not a generic sample — because
    the honest way to sell the report is to show what it actually found on this
    house and withhold the reasoning, rather than to describe the report in the
    abstract and hope.
    """
    findings = [rule(property) for rule in RULES if rule not in _QUERYING_RULES]
    findings = [f for f in findings if f]
    findings.sort(key=lambda f: SEVERITY_ORDER.index(f["severity"]))
    return findings


def preview(property):
    """Teaser context: the counts, the titles, and nothing that explains them."""
    findings = preview_findings(property)
    return {
        "findings": findings,
        "counts": {severity: sum(1 for f in findings if f["severity"] == severity)
                   for severity in SEVERITY_ORDER},
        "blocking": [f for f in findings
                     if f["severity"] in (CRITICAL, UNKNOWN)],
        # Titles are shown; bodies, sources and questions are what Pro unlocks.
        "titles": [
            {"severity": f["severity"], "severity_label": f["severity_label"],
             "title": f["title"]}
            for f in findings
        ],
        "question_count": len(STANDARD_QUESTIONS) + sum(
            len(f["questions"]) for f in findings
        ),
    }


def build_report(property):
    """Everything the template needs. No side effects, no network."""
    findings = [rule(property) for rule in RULES]
    findings = [f for f in findings if f]
    findings.sort(key=lambda f: SEVERITY_ORDER.index(f["severity"]))

    questions = []
    for finding in findings:
        for question in finding["questions"]:
            if question not in questions:
                questions.append(question)
    questions.extend(q for q in STANDARD_QUESTIONS if q not in questions)

    record = [
        {"heading": heading, "value": _clean(getattr(property, attribute, "")),
         "source": source}
        for heading, attribute, source in RECORD_ROWS
    ]

    counts = {severity: sum(1 for f in findings if f["severity"] == severity)
              for severity in SEVERITY_ORDER}

    return {
        "property": property,
        "findings": findings,
        "counts": counts,
        "blocking": [f for f in findings
                     if f["severity"] in (CRITICAL, UNKNOWN)],
        "questions": questions,
        "record": [row for row in record if row["value"]],
        "withheld": [row for row in record if not row["value"]],
        "comparison": property.price_per_sqm_comparison(),
        "human_sections": HUMAN_SECTIONS,
        "building_m2": parse_area_to_m2(property.building_area),
        "land_m2": parse_area_to_m2(property.land_area),
        "photo_count": property.images.count(),
    }
