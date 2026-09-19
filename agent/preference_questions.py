"""Menu-grounded, diagnostic preference intake.

This is deliberately NOT a second learning system. It's a richer front end
for the same `UserPreferences` object `MenuPreferenceMatchingSkill` and
`compute_tag_weights`/`match_dish`/`apply_feedback` in react_agent.py have
always read — those functions are untouched. Every answer here becomes one
of exactly three things the existing schema already understands:

  - a `dietary_restrictions` entry (checked by
    MenuPreferenceMatchingSkill.violates_dietary_restriction, unchanged), or
  - a seeded `KnownDish` (rating + tags, upserted by normalized name — the
    same shape a real YES/NO response already produces), or
  - a line folded into `comment` (free text the schema has always
    round-tripped without scoring on it — used here for the handful of
    dimensions, like "how often" or "only when...", that genuinely have no
    structural home without inventing new scoring logic, which is out of
    scope for this change).

Every dish name and category below was read directly off the real two-week
board on disk (agent/data/menu_intake_2026-09-15.json through -20.json —
all six dates hold the same one extraction), not invented. Categories are
spelled exactly as `derive_tags_from_category` already splits them (e.g.
"Gravy Veg - Jain" -> tags ["gravy", "veg", "jain"]), so a seeded dish's
tags land in the same tag_weights buckets a real extracted item's tags
would.

Rating tiers reuse the scale react_agent.py already scores on elsewhere
(RATING_MIN/MAX = 0/5, EAT_THRESHOLD = 3.0, RESPONSE_BASELINE
yes/no/maybe = 4.0/1.5/2.5) — an intake answer should read the same way to
compute_tag_weights as a real response would, not a separate scale.
"""
from dataclasses import dataclass, field
from typing import Optional

from agent.react_agent import KnownDish, UserPreferences, derive_tags_from_category, normalize_dish_name
from agent.timezone import today_ist

FAVORITE = 5.0
LIKE = 4.0
NEUTRAL = 3.0
DISLIKE = 2.0
STRONG_DISLIKE = 1.0
DECLINE = 1.5  # matches RESPONSE_BASELINE["no"]

COMMENT_MARKER = "--- intake answers ---"


@dataclass
class Seed:
    name: str
    category: Optional[str]
    rating: float
    extra_tags: tuple = ()


@dataclass
class Option:
    value: str
    label: str
    seeds: tuple = ()
    restriction: Optional[str] = None
    comment_note: Optional[str] = None


@dataclass
class Question:
    id: str
    kind: str  # "single" | "multi" | "text"
    prompt: str
    help: Optional[str] = None
    options: tuple = ()
    label: Optional[str] = None  # short prefix used for "text" kind comment lines


QUESTIONS = [
    Question(
        id="hard_excludes",
        kind="multi",
        prompt="Anything you can't or won't eat at all?",
        help=(
            "This board runs separate Jain versions of several dishes (Gravy Veg - Jain / Dry Veg - Jain "
            "— e.g. Paneer Kadai, Chole Masala, Rajma Masala, Cabbage Masala all have one), so it's a real "
            "fork every day, not a hypothetical."
        ),
        options=(
            Option("jain", "Jain-style — no onion, garlic, or root vegetables", restriction="jain"),
            Option("dairy", "Dairy — curd, buttermilk, paneer, milk-based sweets", restriction="dairy"),
            Option("gluten", "Gluten/wheat — roti, phulka, bread, poori", restriction="gluten"),
        ),
    ),
    Question(
        id="gravy_vs_dry",
        kind="single",
        prompt="Gravy or dry preparation — which do you usually go for?",
        help=(
            'This week pairs a gravy and a dry version of the same vegetable on several days — e.g. '
            '"Chole Masala" (gravy) vs "Cabbage Masala" (dry), or "Rajma Masala" (gravy) vs "Parwal Masala" (dry).'
        ),
        options=(
            Option(
                "mostly_gravy", "Mostly gravy",
                seeds=(
                    Seed("Chole Masala", "Gravy Veg", LIKE),
                    Seed("Rajma Masala", "Gravy Veg", LIKE),
                    Seed("Cabbage Masala", "Dry Veg", DISLIKE),
                ),
            ),
            Option(
                "mostly_dry", "Mostly dry",
                seeds=(
                    Seed("Cabbage Masala", "Dry Veg", LIKE),
                    Seed("Parwal Masala", "Dry Veg", LIKE),
                    Seed("Chole Masala", "Gravy Veg", DISLIKE),
                ),
            ),
            Option("depends", "Depends on the vegetable — no fixed rule"),
        ),
    ),
    Question(
        id="rice_favorite",
        kind="single",
        prompt="Which rice option on this board would you pick first?",
        help="Actual options this week: Jeera Rice, Plain Rice, Brown Onion Rice, Vegetable Garlic Fried Rice.",
        options=(
            Option("jeera", "Jeera Rice", seeds=(Seed("Jeera Rice", "Rice", LIKE),)),
            Option("plain", "Plain Rice", seeds=(Seed("Plain Rice", "Rice", LIKE),)),
            Option("brown_onion", "Brown Onion Rice", seeds=(Seed("Brown Onion Rice", "Rice", LIKE),)),
            Option("fried", "Vegetable Garlic Fried Rice", seeds=(Seed("Vegetable Garlic Fried Rice", "Rice", LIKE, ("fried",)),)),
        ),
    ),
    Question(
        id="rice_skip",
        kind="single",
        prompt="...and which of those would you actively skip?",
        options=(
            Option("jeera", "Jeera Rice", seeds=(Seed("Jeera Rice", "Rice", DECLINE),)),
            Option("plain", "Plain Rice", seeds=(Seed("Plain Rice", "Rice", DECLINE),)),
            Option("brown_onion", "Brown Onion Rice", seeds=(Seed("Brown Onion Rice", "Rice", DECLINE),)),
            Option("fried", "Vegetable Garlic Fried Rice", seeds=(Seed("Vegetable Garlic Fried Rice", "Rice", DECLINE, ("fried",)),)),
            Option("none", "None, I'll eat any of them"),
        ),
    ),
    Question(
        id="dal_enjoy",
        kind="multi",
        prompt="Dal shows up almost every day, in different forms. Which do you actually enjoy?",
        help='Soupy tadka-style ("Dal Tadka"/"Dal Fry"), thicker dhaba-style ("Dal Dhaba"), fried into a snack ("Moong Dal Kachori"), or a thin soup ("Dal Rasam").',
        options=(
            Option("soupy", "Soupy tadka-style (Dal Tadka / Dal Fry)", seeds=(Seed("Dal Tadka", "Dal", LIKE), Seed("Dal Fry", "Dal", LIKE))),
            Option("thick", "Thicker dhaba-style (Dal Dhaba)", seeds=(Seed("Dal Dhaba", "Dal", LIKE),)),
            Option("fried_snack", "Fried dal snack (Moong Dal Kachori)", seeds=(Seed("Moong Dal Kachori", "Side Dish", LIKE, ("dal", "fried")),)),
            Option("soup", "Thin dal soup (Dal Rasam)", seeds=(Seed("Dal Rasam", "Soup", LIKE, ("dal",)),)),
        ),
    ),
    Question(
        id="dal_skip",
        kind="multi",
        prompt="...and which dal styles do you tend to skip?",
        options=(
            Option("soupy", "Soupy tadka-style (Dal Tadka / Dal Fry)", seeds=(Seed("Dal Tadka", "Dal", DECLINE), Seed("Dal Fry", "Dal", DECLINE))),
            Option("thick", "Thicker dhaba-style (Dal Dhaba)", seeds=(Seed("Dal Dhaba", "Dal", DECLINE),)),
            Option("fried_snack", "Fried dal snack (Moong Dal Kachori)", seeds=(Seed("Moong Dal Kachori", "Side Dish", DECLINE, ("dal", "fried")),)),
            Option("soup", "Thin dal soup (Dal Rasam)", seeds=(Seed("Dal Rasam", "Soup", DECLINE, ("dal",)),)),
        ),
    ),
    Question(
        id="paneer_strength",
        kind="single",
        prompt="How do you feel about paneer?",
        help='Shows up both as a creamy gravy ("Paneer Kadai", "Paneer Mutter") and mixed into a rice bowl ("Awadhi Paneer and Vegetable Pulao").',
        options=(
            Option("favorite", "One of my favorites", seeds=(Seed("Paneer Kadai", "Gravy Veg", FAVORITE), Seed("Paneer Mutter", "Gravy Veg", FAVORITE), Seed("Awadhi Paneer and Vegetable Pulao", "Bowl", FAVORITE))),
            Option("like", "I like it", seeds=(Seed("Paneer Kadai", "Gravy Veg", LIKE), Seed("Paneer Mutter", "Gravy Veg", LIKE), Seed("Awadhi Paneer and Vegetable Pulao", "Bowl", LIKE))),
            Option("neutral", "Take it or leave it", seeds=(Seed("Paneer Kadai", "Gravy Veg", NEUTRAL), Seed("Paneer Mutter", "Gravy Veg", NEUTRAL))),
            Option("dislike", "Not really for me", seeds=(Seed("Paneer Kadai", "Gravy Veg", DISLIKE), Seed("Paneer Mutter", "Gravy Veg", DISLIKE))),
            Option("strong_dislike", "Really don't like it", seeds=(Seed("Paneer Kadai", "Gravy Veg", STRONG_DISLIKE), Seed("Paneer Mutter", "Gravy Veg", STRONG_DISLIKE))),
            Option("wont_eat", "Won't eat it at all", restriction="paneer"),
        ),
    ),
    Question(
        id="fried_snacks",
        kind="single",
        prompt="How do you feel about fried starters/snacks?",
        help='Real options this week: "Mix Veg Pakoda", "Batata Wada", "Chinese Samosa".',
        options=(
            Option("love", "Love them", seeds=(Seed("Mix Veg Pakoda", "Side Dish", LIKE, ("fried",)), Seed("Batata Wada", None, LIKE, ("fried", "snack")), Seed("Chinese Samosa", "Side Dish", LIKE, ("fried",)))),
            Option("occasional", "Fine occasionally", seeds=(Seed("Mix Veg Pakoda", "Side Dish", NEUTRAL, ("fried",)), Seed("Batata Wada", None, NEUTRAL, ("fried", "snack")))),
            Option("avoid", "Try to avoid fried food", seeds=(Seed("Mix Veg Pakoda", "Side Dish", DISLIKE, ("fried",)), Seed("Batata Wada", None, DISLIKE, ("fried", "snack")), Seed("Chinese Samosa", "Side Dish", DISLIKE, ("fried",)))),
        ),
    ),
    Question(
        id="breakfast_character",
        kind="single",
        prompt="On a typical morning, what do you actually want?",
        help='Real recurring breakfast items: Cornflakes/Mix Cut Fruit (light & cold), Idly/Veg Upma (soft & steamed), Poori Bhaji/Medu Wada (fried & filling).',
        options=(
            Option("light_cold", "Light & cold (cereal, fruit)", seeds=(Seed("Cornflakes", None, LIKE, ("breakfast", "light")), Seed("Mix Cut Fruit", None, LIKE, ("breakfast", "fruit", "light")))),
            Option("soft_steamed", "Soft & steamed (idly, upma, poha)", seeds=(Seed("Idly", None, LIKE, ("breakfast", "soft", "south-indian")), Seed("Veg Upma", None, LIKE, ("breakfast", "soft")))),
            Option("fried_filling", "Fried & filling (poori, wada)", seeds=(Seed("Poori Bhaji", None, LIKE, ("breakfast", "fried")), Seed("Medu Wada", None, LIKE, ("breakfast", "fried")))),
            Option("no_preference", "No strong preference, whatever's fastest"),
        ),
    ),
    Question(
        id="rice_vs_roti",
        kind="single",
        prompt="For your main starch, rice or roti?",
        help='Real options: "Plain Rice" vs "Phulka".',
        options=(
            Option("rice", "Usually rice", seeds=(Seed("Plain Rice", "Rice", LIKE), Seed("Phulka", "Phulka", DISLIKE))),
            Option("roti", "Usually roti/phulka", seeds=(Seed("Phulka", "Phulka", LIKE), Seed("Plain Rice", "Rice", DISLIKE))),
            Option("both", "Both, no strong lean", seeds=(Seed("Plain Rice", "Rice", LIKE), Seed("Phulka", "Phulka", LIKE))),
        ),
    ),
    Question(
        id="routine_timing",
        kind="text",
        prompt="Any routine timing quirks worth knowing?",
        help='E.g. "always skip breakfast before 9am", "lunch is usually late, after 2pm", "rarely free before dinner". Recorded as a note only — the weekly job already checks your real Calendar for conflicts, so this doesn\'t change scheduling logic, just gives a human reader context.',
        label="Routine timing",
    ),
    Question(
        id="dessert_frequency",
        kind="single",
        prompt="There's a dessert most days here — do you want one every time, or only sometimes?",
        options=(
            Option("every_time", "Every time it's offered", comment_note="Dessert frequency: wants dessert every time it's offered."),
            Option("sometimes", "Only sometimes", comment_note="Dessert frequency: only sometimes — don't over-recommend dessert."),
            Option("rarely", "Rarely / not really into dessert", comment_note="Dessert frequency: rarely wants dessert."),
        ),
    ),
    Question(
        id="dessert_style",
        kind="single",
        prompt="When you do want dessert, traditional or fusion?",
        help='Traditional: "Gulab Jamun", "Rice Kheer". Fusion: "Jalebi Cheesecake", "Coconut and White Chocolate Mousse".',
        options=(
            Option("traditional", "Traditional Indian sweets", seeds=(Seed("Gulab Jamun", "Sweet", LIKE), Seed("Rice Kheer", "Sweet", LIKE))),
            Option("fusion", "Fusion desserts", seeds=(Seed("Jalebi Cheesecake", "Dessert", LIKE), Seed("Coconut and White Chocolate Mousse", "Dessert", LIKE))),
            Option("no_preference", "No real preference"),
        ),
    ),
    Question(
        id="salad_tangy",
        kind="single",
        prompt='Tangy/spicy raw salads — e.g. "Spicy Tangy Cabbage Salad" — your take?',
        options=(
            Option("yes", "I go for these", seeds=(Seed("Spicy Tangy Cabbage Salad", "Salad", LIKE, ("tangy", "spicy")),)),
            Option("sometimes", "Sometimes", seeds=(Seed("Spicy Tangy Cabbage Salad", "Salad", NEUTRAL, ("tangy", "spicy")),)),
            Option("no", "Mostly skip the salad station", seeds=(Seed("Spicy Tangy Cabbage Salad", "Salad", DISLIKE, ("tangy", "spicy")),)),
        ),
    ),
    Question(
        id="curd",
        kind="single",
        prompt="Curd or buttermilk with your meal?",
        options=(
            Option("yes", "Usually yes", seeds=(Seed("Curd", "Curd", LIKE), Seed("Taak", "Curd", LIKE, ("buttermilk",)))),
            Option("no", "Not really", seeds=(Seed("Curd", "Curd", DISLIKE),)),
        ),
    ),
    Question(
        id="variety_vs_familiarity",
        kind="single",
        prompt="When something new shows up on the board, are you likely to try it?",
        help="Recorded as a note only — it doesn't change any dish's score, since that would mean changing how new/unknown dishes get handled, not just what's asked here.",
        options=(
            Option("usually_try", "Usually try it", comment_note="Approach to new dishes: usually tries them."),
            Option("only_if_safe", "Only if it sounds safe", comment_note="Approach to new dishes: only tries ones that sound safe."),
            Option("stick_to_known", "I stick to what I know", comment_note="Approach to new dishes: sticks to known dishes."),
        ),
    ),
    Question(
        id="anything_else",
        kind="text",
        prompt="Anything else worth knowing? (e.g. \"I like X only when it's not too oily\", or a dish you love that isn't on this board)",
        label="Note",
    ),
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
        return
    prefs.known_dishes.append(KnownDish(name=name, tags=tags, rating=rating, times_eaten=0, last_seen=today))


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
        for option in q.options:
            if option.value not in selected:
                continue
            for seed in option.seeds:
                tags = list(derive_tags_from_category(seed.category, seed.name)) + list(seed.extra_tags)
                _upsert_known_dish(prefs, seed.name, tags, seed.rating, today)
            if option.restriction and option.restriction not in prefs.dietary_restrictions:
                prefs.dietary_restrictions.append(option.restriction)
            if option.comment_note:
                comment_lines.append(option.comment_note)

    if comment_lines:
        base = (prefs.comment or "").split(COMMENT_MARKER)[0].rstrip()
        block = COMMENT_MARKER + "\n" + "\n".join(comment_lines)
        prefs.comment = (base + "\n\n" + block).strip() if base else block

    return prefs
