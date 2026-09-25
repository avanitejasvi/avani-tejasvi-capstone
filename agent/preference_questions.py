"""General, menu-independent baseline preference intake (onboarding).

This is deliberately NOT a second learning system. It's a front end for the
same `UserPreferences` object `MenuPreferenceMatchingSkill` and
`compute_tag_weights`/`match_dish`/`apply_feedback` in react_agent.py have
always read — those functions are untouched. Every answer here becomes one
of exactly three things the existing schema already understands:

  - `dietary_restrictions` keywords (checked by
    MenuPreferenceMatchingSkill.violates_dietary_restriction, unchanged,
    which substring-matches a dish's name and tags — so a restriction has
    to be a word that actually shows up in dish names, like "onion" or
    "paneer", not an abstract label like "dairy" that no dish is called), or
  - a seeded *profile* `KnownDish` — e.g. "Gravy dishes (in general)" —
    carrying only broad tags, or
  - a line folded into `comment` (free text the schema has always
    round-tripped without scoring on it).

Why profile entries instead of real dish names: onboarding has to work
before any menu has been uploaded, so no question can name "this week's"
dishes. A profile entry never matches a real dish by name (its name is
deliberately unlike any menu item, so neither the exact nor the fuzzy
match in match_dish picks it up); it only feeds compute_tag_weights. Any
future dish carrying the same tag — from its menu category via
derive_tags_from_category, or from NAIVE_TAG_KEYWORDS — then gets a
`tag_weight_estimate` from it, the same path any unseen dish already takes.
Every tag used below is one real dishes actually receive (category tags
from mess_structure.json, or a NAIVE_TAG_KEYWORDS entry).

Profile entries also show up in the /feedback Likes/Avoids summary like any
confirmed dish, so a student can see and correct their baseline there.

Rating tiers reuse the scale react_agent.py already scores on elsewhere
(RATING_MIN/MAX = 0/5, EAT_THRESHOLD = 3.0).
"""
from dataclasses import dataclass
from typing import Optional

from agent.react_agent import KnownDish, UserPreferences, normalize_dish_name
from agent.timezone import today_ist

FAVORITE = 5.0
LIKE = 4.0
NEUTRAL = 3.0
DISLIKE = 2.0
STRONG_DISLIKE = 1.0

COMMENT_MARKER = "--- intake answers ---"


@dataclass
class Seed:
    name: str
    tags: tuple
    rating: float


@dataclass
class Option:
    value: str
    label: str
    seeds: tuple = ()
    restrictions: tuple = ()
    comment_note: Optional[str] = None


@dataclass
class Question:
    id: str
    kind: str  # "single" | "multi" | "text"
    prompt: str
    help: Optional[str] = None
    options: tuple = ()
    label: Optional[str] = None  # short prefix used for "text" kind comment lines


def _scale(name: str, tags: tuple, *levels):
    """One profile entry rated at a different level per option — the common
    shape for "how do you feel about X" questions."""
    return {value: (Seed(name, tags, rating),) for value, rating in levels}


GRAVY = ("Gravy dishes (in general)", ("gravy",))
DRY = ("Dry sabzi (in general)", ("dry",))
RICE = ("Rice (in general)", ("rice",))
ROTI = ("Roti / phulka (in general)", ("roti", "phulka"))
DAL = ("Dal (in general)", ("dal",))
PANEER = ("Paneer dishes (in general)", ("paneer",))
SPICY = ("Spicy food (in general)", ("spicy",))
FRIED = ("Fried food (in general)", ("fried",))
INDO_CHINESE = ("Indo-Chinese dishes (in general)", ("indo-chinese",))
LIGHT_BREAKFAST = ("Light breakfasts (in general)", ("light",))
SOUTH_INDIAN = ("South-Indian dishes (in general)", ("south-indian",))
SWEETS = ("Desserts & sweets (in general)", ("dessert", "sweet"))
CURD = ("Curd / raita / buttermilk (in general)", ("curd", "curd-based"))
SALAD = ("Salads (in general)", ("salad",))

paneer = _scale(*PANEER, ("favorite", FAVORITE), ("like", LIKE), ("neutral", NEUTRAL), ("dislike", STRONG_DISLIKE))
spicy = _scale(*SPICY, ("love", LIKE), ("medium", NEUTRAL), ("mild", STRONG_DISLIKE))
fried = _scale(*FRIED, ("love", LIKE), ("sometimes", NEUTRAL), ("avoid", DISLIKE))
dal = _scale(*DAL, ("love", LIKE), ("fine", NEUTRAL), ("skip", DISLIKE))
indo_chinese = _scale(*INDO_CHINESE, ("love", LIKE), ("sometimes", NEUTRAL), ("skip", DISLIKE))
sweets = _scale(*SWEETS, ("every_time", LIKE), ("sometimes", NEUTRAL), ("rarely", DISLIKE))
curd = _scale(*CURD, ("yes", LIKE), ("no", DISLIKE))
salad = _scale(*SALAD, ("yes", LIKE), ("sometimes", NEUTRAL), ("no", DISLIKE))

QUESTIONS = [
    Question(
        id="hard_excludes",
        kind="multi",
        prompt="Anything you can't or won't eat at all?",
        options=(
            Option("jain", "Jain — no onion, garlic, or root vegetables",
                   restrictions=("onion", "garlic", "potato", "aloo", "carrot", "beetroot")),
            Option("dairy", "Dairy — milk, curd, paneer, ghee",
                   restrictions=("paneer", "curd", "dahi", "raita", "milk", "taak", "kheer", "cheese", "ghee")),
            Option("gluten", "Gluten / wheat — roti, bread, poori",
                   restrictions=("roti", "phulka", "poori", "paratha", "bread", "pav", "naan")),
            Option("egg", "Eggs", restrictions=("egg",)),
            Option("paneer", "Paneer", restrictions=("paneer",)),
        ),
    ),
    Question(
        id="spice",
        kind="single",
        prompt="How spicy do you like your food?",
        options=(
            Option("love", "The spicier the better", seeds=spicy["love"]),
            Option("medium", "Medium is fine", seeds=spicy["medium"]),
            Option("mild", "Keep it mild", seeds=spicy["mild"]),
        ),
    ),
    Question(
        id="gravy_vs_dry",
        kind="single",
        prompt="Gravy or dry sabzi?",
        options=(
            Option("gravy", "Mostly gravy", seeds=(Seed(*GRAVY, LIKE), Seed(*DRY, DISLIKE))),
            Option("dry", "Mostly dry", seeds=(Seed(*DRY, LIKE), Seed(*GRAVY, DISLIKE))),
            Option("both", "Either, no strong lean", seeds=(Seed(*GRAVY, LIKE), Seed(*DRY, LIKE))),
        ),
    ),
    Question(
        id="rice_vs_roti",
        kind="single",
        prompt="Rice or roti?",
        options=(
            Option("rice", "Usually rice", seeds=(Seed(*RICE, LIKE), Seed(*ROTI, DISLIKE))),
            Option("roti", "Usually roti / phulka", seeds=(Seed(*ROTI, LIKE), Seed(*RICE, DISLIKE))),
            Option("both", "Both", seeds=(Seed(*RICE, LIKE), Seed(*ROTI, LIKE))),
        ),
    ),
    Question(
        id="dal",
        kind="single",
        prompt="Dal?",
        options=(
            Option("love", "Love it", seeds=dal["love"]),
            Option("fine", "It's fine", seeds=dal["fine"]),
            Option("skip", "Usually skip it", seeds=dal["skip"]),
        ),
    ),
    Question(
        id="paneer",
        kind="single",
        prompt="Paneer?",
        options=(
            Option("favorite", "One of my favourites", seeds=paneer["favorite"]),
            Option("like", "I like it", seeds=paneer["like"]),
            Option("neutral", "Take it or leave it", seeds=paneer["neutral"]),
            Option("dislike", "Not for me", seeds=paneer["dislike"]),
        ),
    ),
    Question(
        id="fried",
        kind="single",
        prompt="Fried food — pakoda, wada, samosa, poori?",
        options=(
            Option("love", "Love it", seeds=fried["love"]),
            Option("sometimes", "Now and then", seeds=fried["sometimes"]),
            Option("avoid", "Try to avoid it", seeds=fried["avoid"]),
        ),
    ),
    Question(
        id="indo_chinese",
        kind="single",
        prompt="Indo-Chinese — noodles, manchurian, schezwan?",
        options=(
            Option("love", "Love it", seeds=indo_chinese["love"]),
            Option("sometimes", "Now and then", seeds=indo_chinese["sometimes"]),
            Option("skip", "Usually skip it", seeds=indo_chinese["skip"]),
        ),
    ),
    Question(
        id="breakfast",
        kind="single",
        prompt="What do you usually want for breakfast?",
        options=(
            Option("light", "Something light — cereal, fruit, bread", seeds=(Seed(*LIGHT_BREAKFAST, LIKE),)),
            Option("south_indian", "South-Indian — idly, upma, uttapam", seeds=(Seed(*SOUTH_INDIAN, LIKE),)),
            Option("hearty", "Something filling — poori, wada, poha"),
            Option("any", "Whatever's there"),
        ),
    ),
    Question(
        id="sweets",
        kind="single",
        prompt="Desserts?",
        options=(
            Option("every_time", "Every time", seeds=sweets["every_time"]),
            Option("sometimes", "Sometimes", seeds=sweets["sometimes"]),
            Option("rarely", "Rarely", seeds=sweets["rarely"]),
        ),
    ),
    Question(
        id="curd",
        kind="single",
        prompt="Curd, raita, or buttermilk with meals?",
        options=(
            Option("yes", "Usually yes", seeds=curd["yes"]),
            Option("no", "Not really", seeds=curd["no"]),
        ),
    ),
    Question(
        id="salad",
        kind="single",
        prompt="Salads?",
        options=(
            Option("yes", "Usually take some", seeds=salad["yes"]),
            Option("sometimes", "Sometimes", seeds=salad["sometimes"]),
            Option("no", "Mostly skip", seeds=salad["no"]),
        ),
    ),
    Question(
        id="new_dishes",
        kind="single",
        prompt="Something new on the menu — do you try it?",
        options=(
            Option("usually_try", "Usually", comment_note="Approach to new dishes: usually tries them."),
            Option("only_if_safe", "Only if it sounds familiar", comment_note="Approach to new dishes: only tries ones that sound safe."),
            Option("stick_to_known", "I stick to what I know", comment_note="Approach to new dishes: sticks to known dishes."),
        ),
    ),
    Question(
        id="anything_else",
        kind="text",
        prompt="Anything else? (e.g. a dish you love, or \"nothing too oily\")",
        label="Note",
    ),
]


HARD_EXCLUDES = next(q for q in QUESTIONS if q.id == "hard_excludes")

# The first version of this form stored these abstract labels as
# restrictions. violates_dietary_restriction substring-matches them against
# dish names/tags, so "dairy"/"gluten" excluded almost nothing and "jain"
# excluded the Jain-version dishes (tagged "jain") — the opposite of intended.
# They're read back as the matching checkbox and never re-saved.
LEGACY_RESTRICTIONS = {"jain": "jain", "dairy": "dairy", "gluten": "gluten"}


def selected_excludes(prefs: UserPreferences) -> set:
    """Which hard_excludes checkboxes to pre-tick: every keyword of the
    option is already stored, or the student has its legacy label."""
    stored = {r.lower() for r in prefs.dietary_restrictions}
    ticked = [
        o for o in HARD_EXCLUDES.options
        if set(o.restrictions) <= stored or LEGACY_RESTRICTIONS.get(o.value) in stored
    ]
    # "Paneer" is contained in "Dairy" — don't show it ticked just because
    # dairy is.
    return {
        o.value for o in ticked
        if not any(set(o.restrictions) < set(other.restrictions) for other in ticked)
    }


def free_text_restrictions(prefs: UserPreferences) -> list:
    """Restrictions not already covered by a ticked checkbox — what the
    free-text field shows. Keywords a ticked option owns stay out of it,
    so unticking that option on a later visit actually removes them."""
    selected = selected_excludes(prefs)
    covered = {r for o in HARD_EXCLUDES.options if o.value in selected for r in o.restrictions}
    return [
        r for r in prefs.dietary_restrictions
        if r.lower() not in covered and r.lower() not in LEGACY_RESTRICTIONS
    ]


def _upsert_known_dish(prefs: UserPreferences, name: str, tags: list, rating: float, today) -> None:
    """Same upsert-by-normalized-name shape register_new_dishes/apply_feedback
    already use. Critically: never overwrites a dish that already has real
    eaten history (times_eaten > 0) — this project has already lost a real
    rating to an overwritten static value once before (see BUILD_LOG.md,
    2026-09-18/19 entry), so an intake-form estimate must never outrank
    actual experience."""
    normalized = normalize_dish_name(name)
    tags = sorted({t.lower() for t in tags})
    for dish in prefs.known_dishes:
        if normalize_dish_name(dish.name) != normalized:
            continue
        if dish.times_eaten > 0:
            return
        dish.rating = rating
        dish.tags = sorted(set(dish.tags) | set(tags))
        dish.last_seen = today
        # A baseline answer is the student stating this directly, so it's
        # explicit signal, not an inference — same confidence tier a real
        # response gets.
        dish.confidence = "confirmed"
        return
    prefs.known_dishes.append(
        KnownDish(name=name, tags=tags, rating=rating, times_eaten=0, last_seen=today, confidence="confirmed")
    )


def apply_answers(prefs: UserPreferences, form) -> UserPreferences:
    """form: anything with .get(key) and .getlist(key) — a Starlette
    FormData works directly. Mutates and returns prefs; caller still calls
    repository.save_preferences exactly as before."""
    today = today_ist()
    comment_lines = []

    for q in QUESTIONS:
        if q.kind == "text":
            value = (form.get(q.id) or "").strip()
            if value:
                comment_lines.append(f"{q.label or 'Note'}: {value}")
            continue

        selected = form.getlist(q.id) if q.kind == "multi" else [v for v in [form.get(q.id)] if v]
        if not selected:
            continue

        # Re-answering a question replaces its earlier answer: drop this
        # question's profile entries that the new answer doesn't seed (e.g.
        # switching breakfast from "light" to "south_indian"), unless real
        # feedback has since touched them.
        chosen = [o for o in q.options if o.value in selected]
        keep = {normalize_dish_name(s.name) for o in chosen for s in o.seeds}
        owned = {normalize_dish_name(s.name) for o in q.options for s in o.seeds} - keep
        prefs.known_dishes = [
            d for d in prefs.known_dishes
            if normalize_dish_name(d.name) not in owned or d.times_eaten > 0
        ]

        for option in chosen:
            for seed in option.seeds:
                _upsert_known_dish(prefs, seed.name, list(seed.tags), seed.rating, today)
            for restriction in option.restrictions:
                if restriction not in prefs.dietary_restrictions:
                    prefs.dietary_restrictions.append(restriction)
            if option.comment_note:
                comment_lines.append(option.comment_note)

    if comment_lines:
        base = (prefs.comment or "").split(COMMENT_MARKER)[0].rstrip()
        block = COMMENT_MARKER + "\n" + "\n".join(comment_lines)
        prefs.comment = (base + "\n\n" + block).strip() if base else block

    return prefs
