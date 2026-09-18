# SKILL.md — Meal Recommendation Agent (Build Version)

## Overview

This Skill manages a student's daily mess meal recommendation and scheduling, end to end. It's one Skill — Google Drive, Google Calendar, and the Gemini API are tools/actions it uses internally, not separate skills.

**Design direction per professor feedback:** menu intake uses the **Gemini API reading menu images**, not a structured Sheet. Combined with the two strongest pieces from earlier design passes: **tag-based fallback scoring** for genuinely new dishes (instead of leaving them a blank slate), and the richer **YES/NO/MAYBE + note feedback loop** with **calendar-conflict resolution**.

One-line summary: **each morning, Gemini reads a photographed menu image and extracts + stores the day's dishes → match each dish against preferences (exact match, or tag-based estimate for new dishes) → check the student's Calendar against mess timings, resolving conflicts with an alternate slot if needed → schedule the meal and invite the student → student responds YES/NO/MAYBE + optional note → Gemini interprets the response and updates that specific item's preference data → repeat the next day.**

## Interface: Gemini API + Google Drive + Google Calendar

- **Menu source:** a photographed/scanned image of the day's mess menu, stored in a Google Drive folder. The **Gemini API reads the image** each morning and extracts dishes, tagged with which mess (**Rasoi** or **Aahar**), which meal, and which menu category (e.g. "Dal," "Gravy Veg - Jain") they were listed under — see `mess_structure.json` for the exact category labels per mess per meal, since **Rasoi and Aahar have genuinely different menu structures** (Rasoi is rice-bowl style, Aahar is full buffet style).
- **Extracted data must be stored**, not just used transiently — write the extracted dish list to a persistent `MenuIntake` record for that date immediately after extraction, before any matching happens.
- **Calendar:** Google Calendar, checked for existing events during each meal window before scheduling, and used to create the meal event with match results in the description.

## OCR Reliability Safeguard

Reading a real photographed menu is inherently less reliable than a structured data source — extracted dish names can come back slightly different each time (e.g. "Paneer Tikka" vs "Paneer Tkka" vs "paneer tikka"). Since the matching logic depends on **exact name match** against `known_dishes`, an unhandled misread would silently create a duplicate "new" item instead of matching existing preference history.

**Mitigation:** before treating an extracted item as brand-new, run a simple fuzzy/normalized comparison (lowercase, strip whitespace, and a basic string-similarity check) against existing `known_dishes` names. If a close match exists above a similarity threshold, treat it as the same dish rather than creating a duplicate. This is a small, buildable safeguard — not a full OCR-correction system — that keeps the known-dish matching from silently degrading over time.

## Data Sources

1. **Menu Images** — stored in Drive, read via the Gemini API each morning.
2. **Menu Intake Records** — the extracted-and-stored result of that read, one per day, kept even after matching runs (see Data Model).
3. **Preferences File** (`preferences_u001.json`):
   - `dietary_restrictions` — hard excludes, checked first.
   - `skip_meal_slots` — a behavior rule, handled before scoring runs.
   - `known_dishes` — every dish rated by exact (normalized) name, with tags, a running score, and times eaten.
   - `tag_weights` — derived, recomputed from `known_dishes` each run: the average rating across every known dish carrying a given tag. Used as the fallback estimate for dishes not yet in `known_dishes`.
4. **Mess Timings** — fixed meal-slot windows (breakfast/lunch/dinner), used for both matching context and conflict-checking.

## Workflow

### 1. Morning menu intake

- Each morning, the agent retrieves the day's menu image from Drive.
- The Gemini API reads the image and extracts the list of dishes.
- The extracted list is stored as a `MenuIntake` record for that date, before anything else happens.

### 2. Normalize and check behavior rules

- If today's meal slot is in `skip_meal_slots`, skip the entire meal — no scoring, no event.
- For remaining slots, normalize each extracted dish name (per the OCR Reliability Safeguard above) and exclude anything violating `dietary_restrictions`.

### 3. Match menu against preferences

For each remaining dish:
- **Exact (normalized) match in `known_dishes`** → use its stored, running score directly.
- **No match — genuinely new dish** → estimate from `tag_weights`: average the weight of each of its tags. If none of its tags have a recorded weight yet, there's no signal to average — flag it distinctly as "new, no data yet" rather than guessing a neutral score.
- New dishes get added to `known_dishes` with their estimated score, so `tag_weights` self-improves on the next run.

### 4. Check calendar conflicts and schedule

- Identify the student's top-scoring meal options from step 3.
- Check the student's Google Calendar for an existing event during that meal's mess timing window.
- **No conflict** → create the event at the normal mess timing, add the student as invitee.
- **Conflict found** → look for another suitable time within the mess's available slot; schedule there instead if one exists.
- **No alternate slot available either** → flag for the student rather than silently failing (see Open Questions).

### 5. Calendar event

Kept short and useful. Example:

> **Today's mess picks:** Paneer Tikka, Dal Makhani
> Based on your preferences.

Includes the selected meal(s) and the relevant mess timing.

### 6. User feedback

The student responds to the event with:
- **YES** — I'll eat this
- **NO** — I won't eat this
- **MAYBE** — I'm unsure
- **Note** (optional) — free text, e.g. "too spicy," "really liked it"

Gemini interprets the response and note, and updates the preference data for the **specific food item**:
- YES → score increases
- YES + positive note → score increases more
- NO → score decreases
- NO + note (e.g. "too spicy") → score decreases; the note can also inform tag-level signal over time
- MAYBE + note → slight/neutral adjustment

`times_eaten` is tracked alongside score as a **confidence** signal, not a stand-in for sentiment — a single lukewarm response after one meal is genuinely uncertain, not automatically disliked.

## Data Model

```python
class ExtractedDish(BaseModel):
    name: str
    mess: str                        # "Rasoi" | "Aahar"
    meal_slot: str                    # "breakfast" | "lunch" | "evening_snacks" | "dinner" | "sunday_brunch"
    category: str                      # e.g. "Dal", "Gravy Veg - Jain", "Dessert" - the menu section it was listed under

class MenuIntake(BaseModel):
    date: date
    source_image_id: str            # Drive file id for the scanned menu image
    extracted_items: list[ExtractedDish]   # structured extraction, not just raw names
    processed: bool                   # true once matching has run against this record

class KnownDish(BaseModel):
    name: str                         # normalized exact-match key
    tags: list[str]
    score: float                       # running score, adjusted by feedback over time
    times_eaten: int                    # confidence signal, separate from score
    last_response: str | None           # "yes" | "no" | "maybe" | None
    last_note: str | None
    last_seen: date

class UserPreferences(BaseModel):
    user_id: str
    dietary_restrictions: list[str]
    skip_meal_slots: list[str]
    known_dishes: list[KnownDish]
    tag_weights: dict[str, float]        # derived - recompute from known_dishes each run

class MessTiming(BaseModel):
    meal_slot: str                        # "breakfast" | "lunch" | "dinner"
    start_time: time
    end_time: time

class ScoredDish(BaseModel):
    name: str
    score: float
    source: str                            # "known_dish" | "tag_weight_estimate" | "no_data"
    decision: str                           # "eat" | "skip"

class ScheduledMeal(BaseModel):
    date: date
    meal_slot: str
    selected_items: list[str]
    calendar_event_id: str | None
    scheduled_time: time                    # may differ from default mess_timing if conflict resolved
    conflict_resolved: bool

class MealResponse(BaseModel):
    calendar_event_id: str
    response: str                            # "yes" | "no" | "maybe"
    note: str | None
    processed: bool                           # true once known_dishes has been updated from this response
```

## Open Questions — decide before building

1. **Fuzzy-match similarity threshold.** How close does an extracted name need to be to an existing `known_dishes` entry to count as the same dish (vs. a genuinely new one)? Needs a concrete threshold (e.g. edit-distance cutoff), not left as "close enough."
2. **Exact score-adjustment sizes.** Define the actual increments (e.g. YES = +1, YES + positive note = +2) before coding rather than inventing them ad hoc inside the function.
3. **Repeated-conflict fallback.** If step 4's alternate-slot check also finds everything taken, what happens — schedule anyway with a warning, skip the day, or notify the student to resolve manually?
4. **Dish with zero recognized tags.** A new dish whose tags have never appeared before has nothing to average for `tag_weights`. Recommend flagging it explicitly as "new, no data yet" rather than guessing a neutral score.
5. **Does an estimated score ever get replaced by real feedback?** Recommend: the first real YES/NO/MAYBE response replaces the `tag_weight_estimate` outright, since direct signal is stronger than an inference.
6. **Which mess gets scored when both Rasoi and Aahar are options?** With two messes serving the same meal slot at the same time, does the agent score both and recommend the better one, only ever check one specific mess, or let the student pick a default? Not yet decided — needs an answer before the matching logic can run against real dual-mess data.
7. **How does "fortnightly" Sunday brunch actually get detected?** `mess_structure.json` flags this as unresolved — fortnightly requires knowing a reference start date to count from (e.g. "every 2nd Sunday from Sept 1"), which hasn't been defined yet. Needs a concrete rule before the agent can know which Sundays are brunch days vs. normal breakfast/lunch days.

## Sample Data (for testing the matching/scoring logic before wiring up Gemini/Drive/Calendar)

**Preferences** (`preferences_u001.json`) — real file, 17 rated dishes covering fruits, bread items, breakfast items (aloo bhaji, puri, upma, sabudana khichdi, poha), desserts (mousse, slice cake liked; jalebi, custard, payasam disliked), milk (disliked), and staples (chana, curd, rice — liked). `tag_weights` verified computationally against `known_dishes`. Note `skip_meal_slots` is now empty — no meal is being skipped by default.

**Mess Timings** (`mess_timings.json`) — the real meal windows:

| Meal | Window |
|---|---|
| Breakfast | 7:30 AM – 9:30 AM |
| Lunch | 11:30 AM – 3:00 PM |
| Snack | 5:00 PM – 6:00 PM |
| Dinner | 7:30 PM – 10:00 PM |

**Simulated Gemini extraction output** (`menu_intake_sample.json`) — stands in for what the Gemini API would return from reading a real image, including one deliberately "noisy" name to test the fuzzy-match safeguard:

```json
{
  "date": "2026-09-18",
  "source_image_id": "sample_image_001",
  "extracted_items": ["Curd Rice", "Rajma Chawal", "paneer tkka", "Mushroom Risotto"]
}
```

| Extracted name | Expected handling |
|---|---|
| Curd Rice | Exact match → known score |
| Rajma Chawal | Exact match → known score |
| paneer tkka | Should fuzzy-match to "Paneer Tikka" if that's in `known_dishes` — tests Open Question 1 |
| Mushroom Risotto | Genuinely new, zero recognized tags → `no_data` — tests Open Question 4 |

## What to build first, in order

1. Decide the five Open Questions above — write the answers directly into this file before coding.
2. Build the fuzzy-name-matching safeguard as its own small, independently testable function — confirm "paneer tkka" correctly resolves to "Paneer Tikka" using your chosen threshold.
3. Build the matching/scoring logic (steps 2-3) against `menu_intake_sample.json` and the real preferences file — no live Gemini, Drive, or Calendar calls yet. Confirm each sample row scores as the table above predicts.
4. Build the calendar-conflict check and alternate-slot logic (step 4) against a small set of mock calendar events.
5. Build the feedback-processing logic (step 6) — given a sample YES/NO/MAYBE + note, confirm `known_dishes` updates correctly.
6. Only once steps 2-5 are all tested and passing, wire up the real Gemini API image read, real Drive access, and real Calendar API calls.