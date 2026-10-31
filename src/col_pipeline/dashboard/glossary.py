"""The glossary page's text: every acronym on the dashboard, and what each number means and why it
matters to the question. Plain data, no Streamlit, so tests can check it (DECISIONS.md #64).

Each definition describes what the code does, with the decision behind it where there is one. When a
page changes what a number means, change its entry here too.
"""

from __future__ import annotations

from dataclasses import dataclass

# Page titles exactly as app.py names them, in the order of the navigation bar.
PAGES = ["The answer", "States", "Fuel prices", "Cost of living", "About the data"]

ACRONYM, DATAPOINT = "acronym", "datapoint"


@dataclass(frozen=True)
class Term:
    term: str  # as written on the dashboard
    meaning: str
    why_it_matters: str
    pages: tuple[str, ...]  # where it appears; the first is where it is grouped
    kind: str = DATAPOINT
    stands_for: str | None = None  # the expansion of an acronym
    aliases: tuple[str, ...] = ()  # other spellings on screen, e.g. RON97 for RON95

    @property
    def keys(self) -> tuple[str, ...]:
        return (self.term, *self.aliases)


TERMS: list[Term] = [
    # ---------- acronyms and units ----------
    Term(
        "CPI",
        stands_for="Consumer Price Index",
        meaning=(
            "How the price of a fixed basket of goods and services has moved, as an index against "
            "a base year. OpenDOSM publishes it for each state, overall and for each part of "
            "spending, such as transport or food."
        ),
        why_it_matters=(
            "Transport CPI is the effect side of the dashboard's question: does it move the month "
            "after the pump price does?"
        ),
        pages=("The answer", "States", "Cost of living"),
        kind=ACRONYM,
    ),
    Term(
        "2010 = 100",
        meaning=(
            "The CPI base. An index of 112.0 means prices are 12% above their 2010 average. The "
            "level alone says little; what matters is how much it changes."
        ),
        why_it_matters=(
            "The dashboard compares changes in the index (on a month, on a year), never one "
            "state's level against another's: each state's index starts from its own 2010 prices."
        ),
        pages=("The answer",),
        kind=ACRONYM,
    ),
    Term(
        "COICOP",
        stands_for="Classification of Individual Consumption According to Purpose",
        meaning=(
            "The United Nations standard that splits household spending into 13 divisions, coded "
            "01 to 13. Division 01 is food and non-alcoholic beverages; division 07 is transport. "
            "The dashboard uses the COICOP 2018 division names."
        ),
        why_it_matters=(
            "Division 07, transport, is the index that should move with pump prices, so it is the "
            "one the answer uses. The Cost of living page sets it beside the other divisions."
        ),
        pages=("Cost of living", "The answer"),
        kind=ACRONYM,
    ),
    Term(
        "DOSM",
        stands_for="Department of Statistics Malaysia",
        aliases=("OpenDOSM",),
        meaning=(
            "The national statistics office. OpenDOSM is its open-data portal, which publishes CPI "
            "by state every month, about seven weeks after the month ends."
        ),
        why_it_matters=(
            "Every CPI figure on the dashboard comes from OpenDOSM. Its publication delay is why "
            "the newest months show as awaiting CPI."
        ),
        pages=("About the data",),
        kind=ACRONYM,
    ),
    Term(
        "data.gov.my",
        meaning=(
            "Malaysia's national open-data portal. It publishes the retail fuel prices set by the "
            "government each week."
        ),
        why_it_matters="It is the source of every pump price on the dashboard.",
        pages=("About the data", "Fuel prices"),
        kind=ACRONYM,
    ),
    Term(
        "BNM",
        stands_for="Bank Negara Malaysia",
        meaning=(
            "Malaysia's central bank. It publishes daily exchange rates for the ringgit against "
            "other currencies."
        ),
        why_it_matters=(
            "The pipeline loads its exchange rates as the third source, and the About the data "
            "page reports how current they are. No chart uses them yet."
        ),
        pages=("About the data",),
        kind=ACRONYM,
    ),
    Term(
        "RON95",
        stands_for="Research Octane Number 95",
        aliases=("RON97", "RON"),
        meaning=(
            "Petrol grades, named by octane rating. RON95 is the everyday grade whose price the "
            "government controls; RON97 is the premium grade."
        ),
        why_it_matters=(
            "RON95 is the headline fuel: the one the answer is about. The dashboard reads which "
            "fuel that is from the warehouse rather than assuming it (DECISIONS #41)."
        ),
        pages=("The answer", "Fuel prices"),
        kind=ACRONYM,
    ),
    Term(
        "BUDI95",
        aliases=("BUDI MADANI",),
        meaning=(
            "Malaysia's targeted RON95 petrol subsidy programme, under the BUDI MADANI initiative. "
            "The fuel series named RON95 (BUDI95) is the RON95 price published for the programme."
        ),
        why_it_matters=(
            "It is a separate series from RON95, starting with the 2025 subsidy change. It is "
            "shown on the Fuel prices page for comparison and is not part of the answer."
        ),
        pages=("Fuel prices",),
        kind=ACRONYM,
    ),
    Term(
        "SKPS",
        stands_for="Sistem Kawalan Petrol Bersubsidi (Subsidised Petrol Control System)",
        meaning=(
            "The control system for subsidised petrol. The fuel series named RON95 (SKPS) is the "
            "RON95 price published under it."
        ),
        why_it_matters=(
            "Like BUDI95, it is a separate RON95 series shown for comparison, not part of the "
            "answer."
        ),
        pages=("Fuel prices",),
        kind=ACRONYM,
    ),
    Term(
        "SKDS",
        stands_for="Sistem Kawalan Diesel Bersubsidi (Subsidised Diesel Control System)",
        meaning=(
            "The control system for subsidised diesel. The fuel series named Diesel (SKDS) is the "
            "diesel price published under it."
        ),
        why_it_matters=(
            "Diesel (SKDS) is one of the diesel series with an open data-quality finding: for long "
            "stretches it carries exactly the same price as another diesel series. Treat it with "
            "caution."
        ),
        pages=("Fuel prices",),
        kind=ACRONYM,
    ),
    Term(
        "RM",
        stands_for="Ringgit Malaysia",
        aliases=("sen", "MYR"),
        meaning=(
            "The currency. 100 sen make RM1; MYR is its international code. Pump prices are in RM "
            "per litre, and price changes are in sen."
        ),
        why_it_matters=(
            "A 10 sen rise on a RM2.05 litre is about 5%. The answer page also gives the effect "
            "per 10 sen of fuel change."
        ),
        pages=("The answer", "Fuel prices"),
        kind=ACRONYM,
    ),
    Term(
        "MoM",
        stands_for="Month on month",
        aliases=("on month",),
        meaning="The change from the previous month, in percent for CPI or in sen for fuel.",
        why_it_matters=(
            "The question is about monthly changes: last month's fuel change beside this month's "
            "transport CPI change."
        ),
        pages=("The answer", "States"),
        kind=ACRONYM,
    ),
    Term(
        "YoY",
        stands_for="Year on year",
        meaning=(
            "The change from the same month a year earlier. It needs at least 13 months of data."
        ),
        why_it_matters=(
            "It removes the regular seasonal pattern, so the Cost of living page uses it to "
            "compare how fast each part of spending is rising."
        ),
        pages=("Cost of living",),
        kind=ACRONYM,
    ),
    Term(
        "pp",
        stands_for="Percentage points",
        meaning=(
            "The difference between two percentages. If transport CPI rose 0.40% after a fuel rise "
            "and 0.08% after no change, the gap is 0.32 pp. Saying 0.32% would be wrong, because "
            "that means 0.32% of something."
        ),
        why_it_matters=(
            "The answer's headline gap and the spread between states are differences between "
            "percentages, so they are in pp."
        ),
        pages=("The answer", "States"),
        kind=ACRONYM,
    ),
    Term(
        "r",
        stands_for="Pearson correlation coefficient",
        meaning=(
            "How closely two things move together, from -1 (always opposite) to +1 (always "
            "together); 0 means no straight-line relation."
        ),
        why_it_matters=(
            "The answer page gives r for last month's fuel change against this month's transport "
            "CPI change. Across few months it is weak evidence, and it never shows cause."
        ),
        pages=("The answer",),
        kind=ACRONYM,
    ),
    # ---------- the answer ----------
    Term(
        "Pump price",
        meaning=(
            "The latest weekly national price, in RM per litre, with its change in sen against the "
            "price in effect a year earlier. The small line shows the last 52 weeks."
        ),
        why_it_matters="It is the cause side of the question: the price change to follow.",
        pages=("The answer", "Fuel prices"),
    ),
    Term(
        "Transport CPI",
        meaning=(
            "A state's index for COICOP division 07, transport, in its latest published month, "
            "with its change on the month before."
        ),
        why_it_matters=(
            "It is the effect side of the question. Pick a state to see its own figure."
        ),
        pages=("The answer", "States"),
    ),
    Term(
        "Monthly fuel price",
        meaning=(
            "A month's average pump price, with each weekly price weighted by the days it was in "
            "effect. A price that held for 25 days counts five times as much as one that held "
            "for 5."
        ),
        why_it_matters=(
            "CPI is monthly and prices change weekly, so the two are compared month to month. A "
            "plain average of announcements would overweight short-lived prices (DECISIONS #46)."
        ),
        pages=("The answer",),
    ),
    Term(
        "The one-month lag",
        meaning=(
            "Each month's transport CPI change is set beside the fuel change of the month before. "
            "On the charts, dashed lines mark fuel moves: look one bar to the right for the effect."
        ),
        why_it_matters=(
            "A pump price rise can take time to reach fares, delivery charges and other transport "
            "costs. Comparing the same month would miss most of it."
        ),
        pages=("The answer", "States"),
    ),
    Term(
        "Rise, fall or no change",
        meaning=(
            "How a month's average fuel price moved. A move of less than half a sen counts as no "
            "change."
        ),
        why_it_matters=(
            "A price held all month can still differ in the fourth decimal because of day "
            "weighting. Without the tolerance, that dust would count as a rise or a fall."
        ),
        pages=("The answer", "States"),
    ),
    Term(
        "Extra transport inflation after a rise",
        meaning=(
            "The average transport CPI change in the month after fuel rose, minus the average "
            "after months when it held. All states, the selected period, in pp."
        ),
        why_it_matters=(
            "It is the answer in one number. Subtracting the months with no change takes out the "
            "inflation that happens anyway, so only the extra after a rise is credited to fuel."
        ),
        pages=("The answer",),
    ),
    Term(
        "What the data says",
        meaning=(
            "The page's one-sentence answer, written from the numbers each time the page loads, "
            "with the count of months it rests on."
        ),
        why_it_matters=(
            "It can never overstate the data. With few months, one unusual month moves it a lot, "
            "which is why the count is shown."
        ),
        pages=("The answer",),
    ),
    Term(
        "pp per 10 sen",
        meaning=(
            "The straight-line slope of transport CPI change against last month's fuel change: "
            "roughly how many pp transport CPI moves per 10 sen of fuel change."
        ),
        why_it_matters=(
            "It gives the size of the effect, where the rise-versus-hold gap only gives its "
            "direction. It is an association, not proof of cause."
        ),
        pages=("The answer",),
    ),
    Term(
        "Price changes",
        meaning=(
            "The number of weekly announcements in the selected period that changed the price, "
            "with the latest change in sen."
        ),
        why_it_matters=(
            "Few changes mean few rise months to learn from, so the answer is less certain."
        ),
        pages=("The answer", "Fuel prices"),
    ),
    Term(
        "The average state",
        meaning="A simple average of the 16 states' figures, each state counting equally.",
        why_it_matters=(
            "The warehouse holds no official national CPI, so the dashboard never calls this the "
            "national figure."
        ),
        pages=("The answer",),
    ),
    # ---------- states ----------
    Term(
        "Highest and lowest transport CPI change",
        meaning=(
            "The states whose transport CPI rose most and least on the month, in the latest month "
            "with published CPI."
        ),
        why_it_matters=(
            "It shows which states feel a price change most, the second half of the question."
        ),
        pages=("States",),
    ),
    Term(
        "Spread between states",
        meaning="The highest state's transport CPI change minus the lowest's, in pp.",
        why_it_matters=(
            "The fuel price is the same everywhere, so a wide spread means local conditions, not "
            "the pump price, drive the difference."
        ),
        pages=("States",),
    ),
    Term(
        "Most reactive to a rise",
        meaning=(
            "The state with the largest extra transport CPI change after a fuel rise, against its "
            "own months with no change. The table below ranks every state, with the rise months "
            "behind each figure."
        ),
        why_it_matters=(
            "It points to where a price rise bites hardest. With few rise months, treat the order "
            "as a pointer to look closer, not a league table."
        ),
        pages=("States",),
    ),
    # ---------- fuel prices ----------
    Term(
        "Biggest rise and biggest fall",
        meaning="The largest single price increase and decrease in the selected period, in sen.",
        why_it_matters="They show how sharp the price moves behind the answer were.",
        pages=("Fuel prices",),
    ),
    Term(
        "Reviewed event",
        meaning=(
            "A policy change or similar event behind a price change, from the project's list of "
            "events that have been checked by hand. Shown as a dotted line on the price chart."
        ),
        why_it_matters=(
            "It explains why a price moved. Only reviewed events are listed, so a blank means not "
            "yet reviewed, not that nothing happened."
        ),
        pages=("Fuel prices",),
    ),
    Term(
        "Diesel series",
        meaning=(
            "Diesel, Diesel (East Malaysia) for Sabah, Sarawak and Labuan, and Diesel (SKDS)."
        ),
        why_it_matters=(
            "They have an open data-quality finding: for long stretches two diesel series carry "
            "exactly the same price, which has not been reviewed. Diesel is kept off the answer, "
            "and RON95 is not affected."
        ),
        pages=("Fuel prices", "About the data"),
    ),
    # ---------- cost of living ----------
    Term(
        "All items",
        meaning="The index for the whole basket: a state's headline inflation rate.",
        why_it_matters=(
            "It is the yardstick: transport rising faster than all items means transport is "
            "getting dearer faster than the basket as a whole."
        ),
        pages=("Cost of living",),
    ),
    Term(
        "Rising fastest",
        meaning="The COICOP division with the highest year-on-year change in the chosen state.",
        why_it_matters=(
            "It shows whether transport is the main pressure on prices in that state, or something "
            "else is."
        ),
        pages=("Cost of living",),
    ),
    # ---------- about the data, and every page ----------
    Term(
        "Partial month",
        meaning=(
            "The current month, whose fuel average covers only the days so far. It is shaded and "
            "labelled on the charts."
        ),
        why_it_matters=(
            "Its average can still change, so it is never compared as if it were complete "
            "(DECISIONS #42)."
        ),
        pages=("About the data", "The answer"),
    ),
    Term(
        "Awaiting CPI",
        meaning=(
            "A month whose CPI OpenDOSM has not published yet. It is labelled as awaiting on the "
            "charts and left out of every comparison and ranking."
        ),
        why_it_matters=(
            "A missing figure is not a zero or a fall. Counting it as one would make prices look "
            "like they stopped rising."
        ),
        pages=("About the data", "The answer", "States"),
    ),
    Term(
        "National fuel price",
        meaning=(
            "Each fuel has one pump price, set by the government and the same in every state; a "
            "state's rows repeat it. Charts label the fuel line as national."
        ),
        why_it_matters=(
            "Differences between states come from their CPI alone, not from different pump prices "
            "(DECISIONS #33)."
        ),
        pages=("About the data", "The answer", "States", "Fuel prices"),
    ),
    Term(
        "Synthetic data",
        meaning=(
            "Generated demonstration rows, loaded with python tasks.py load-sample. The About the "
            "data page counts them per source as synthetic rows, beside the real rows."
        ),
        why_it_matters=(
            "A banner on every page says when any shown data is synthetic. Its numbers are not "
            "real prices or CPI, and no finding drawn from them describes Malaysia."
        ),
        pages=("About the data",),
    ),
    Term(
        "Last successful load",
        meaning=(
            "When the pipeline last loaded each source without error, with the date range its data "
            "covers."
        ),
        why_it_matters=(
            "It tells you how current the numbers are. A stale source means the newest months may "
            "be missing."
        ),
        pages=("About the data",),
    ),
]


def matches(term: Term, query: str) -> bool:
    """True when every word of the query appears somewhere in the entry, ignoring case."""
    text = " ".join(
        [*term.keys, term.stands_for or "", term.meaning, term.why_it_matters, *term.pages]
    ).lower()
    return all(word in text for word in query.lower().split())


def search(query: str, terms: list[Term] | None = None) -> list[Term]:
    """The entries matching the query, in glossary order. An empty query matches everything."""
    terms = TERMS if terms is None else terms
    return [t for t in terms if matches(t, query)]


def find(key: str, terms: list[Term] | None = None) -> Term | None:
    """The entry for a term or one of its aliases, exactly as written on screen."""
    terms = TERMS if terms is None else terms
    return next((t for t in terms if key in t.keys), None)


def by_page(terms: list[Term]) -> dict[str, list[Term]]:
    """Datapoints grouped under the first page they appear on, in navigation order."""
    groups: dict[str, list[Term]] = {page: [] for page in PAGES}
    for t in terms:
        if t.kind == DATAPOINT:
            groups[t.pages[0]].append(t)
    return {page: entries for page, entries in groups.items() if entries}
