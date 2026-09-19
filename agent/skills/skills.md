# SKILL.md — Meal Recommendation Agent (Multi-Tenant Build)

## Overview

This Skill manages a student's mess meal recommendation and scheduling, end to end, for **any number of students independently**. It's still one Skill — Gemini, Google Calendar, and Postgres are tools/actions it uses internally, not separate skills — but it now runs multi-tenant: any `@flame.edu.in` student signs in with their own Google account, gets their own Calendar scheduled, and builds up their own separate learned preferences.

One-line summary: **once a week, any student uploads a photo of the mess's weekly menu board → Gemini reads it once and stores the extraction, shared → every active student's own preferences get matched against it → each student's own Calendar is checked against mess timings, resolving conflicts with an alternate slot if needed → each meal is scheduled on that student's own calendar → the student rates it later (or the system notices if they cancelled it) → the specific dish's preference data updates → repeat next week, automatically, with no one having to ask.**

## What changed from the single-user build, and why

The project started as a single-user CLI (one Google account, one Drive folder, one `preferences_u001.json`, run manually per day). Turning it into a hosted, multi-student product required three real changes to the design, not just a bigger deployment:

1. **Menu source: upload, not Drive.** Every student saw the same physical menu board anyway — there was never a reason for each of them to connect their own Drive. Any logged-in student uploads the week's photo through the web app; Gemini extracts it **once**, and that becomes one shared `MenuIntake` every student's own scheduling run reads.
2. **Cadence: weekly, not daily.** The mess posts one photo covering the whole week, so scheduling now runs once a week (Monday 9:30am IST, via a Railway Cron job) instead of needing a person to invoke it once a day.
3. **Feedback: two channels, not one.** The old design invited a single hardcoded `STUDENT_EMAIL` as an attendee and read their RSVP. That doesn't work once every student has their own calendar — the calendar *owner's own* RSVP on their own event carries no information (it's effectively always "accepted"). Two channels replace it: an automated one that can only ever detect a **decline** (the student cancelled the event), and a manual **"rate this meal"** page that's the only remaining way a rating can go *up* from real experience. See "Feedback" below — this distinction matters and is easy to miss.

## Interface: Gemini API + Google Calendar (per-user) + Postgres

- **Menu source:** a photo of the week's mess menu board, uploaded through the web app by any signed-in student (`/menu/upload`). Gemini reads it once and extracts dishes, tagged with mess (**Rasoi** or **Aahar**), meal slot, weekday, and menu category — see `mess_structure.json`.
- **Extracted data is stored before anything else happens** — a two-step upload flow (extract → preview → explicit confirm) writes the shared `menu_intake` Postgres row for each of the week's 7 dates only once confirmed, so one bad photo can't silently clobber the whole college's menu.
- **Calendar:** each student's own Google Calendar, accessed via their own OAuth-granted, encrypted-at-rest refresh token — never a shared account. Checked for existing events during each meal window before scheduling, and used to create the meal event with match results in the description.
- **Identity:** Google Sign-In doubles as both login and Calendar-access consent, gated to verified `@flame.edu.in` accounts (checked against the ID token's `email_verified` and `hd`/email-suffix claims server-side, not just Google's account-chooser hint).
- **Storage:** Postgres, one `preferences` row per student (JSONB columns, same shape as the old per-file `known_dishes`/`tag_weights`), replacing one JSON file per person.

## OCR Reliability Safeguard

Unchanged from the single-user build — still worth restating, since it's still exactly how new-vs-known dishes are told apart. Reading a real photographed menu is inherently less reliable than a structured data source — extracted dish names can come back slightly different each time (e.g. "Paneer Tikka" vs "Paneer Tkka" vs "paneer tikka"). Since matching depends on **exact name match** against `known_dishes`, an unhandled misread would silently create a duplicate "new" item instead of matching existing preference history.

**Mitigation:** before treating an extracted item as brand-new, run a normalized comparison (lowercase, whitespace-collapsed) and a string-similarity check against existing `known_dishes` names. Above the threshold, it's the same dish, not a duplicate.

## Data Sources

1. **Menu Images** — never stored; extracted in memory at upload time, only a `sha256` hash is kept (`menu_intake.source_image_id`) for reference.
2. **Menu Intake** (`menu_intake` table) — the shared, once-a-week extraction, one row per calendar date, read fresh by every student's own scheduling run. A date "has a menu" iff a row exists for it — no separate processed flag.
3. **Preferences** (`preferences` table, one row per student):
   - `dietary_restrictions` — hard excludes, checked first.
   - `skip_meal_slots` — a behavior rule, handled before scoring runs.
   - `known_dishes` — every dish this specific student has a rating for, by exact (normalized) name, with tags, a running `rating`, and `times_eaten`.
   - `tag_weights` — derived, recomputed from that student's `known_dishes` every run; never persisted.
4. **Mess Structure** (`mess_structure.json`) — the two messes, their per-meal category labels, and the Gemini extraction prompt. Structural, shared, not personal taste.
5. **Mess Timings** (`mess_timings.json`) — the real meal-slot windows, used for both matching context and conflict-checking.

## Workflow

### 1. Weekly menu upload (any signed-in student, any day)

- Upload a photo → Gemini extracts it once → a preview shows extracted counts and a name sample, with an explicit **overwrite** checkbox if any of the week's dates already have a menu → confirming writes one `menu_intake` row per date and triggers scheduling for that week immediately (see "Catch-up scheduling" below) rather than waiting for the next Monday.

### 2. Weekly scheduling (Railway Cron, Monday 9:30am IST, `agent/jobs/run_all.py`)

For the current week:
- **No menu uploaded at all** → schedule nothing; put one reminder event on each active student's own calendar ("Upload this week's mess menu," linking to `/menu/upload`) and show a dashboard banner. Both use a **deterministic event id**, so a re-run of the job never creates a second reminder.
- **Partial menu** (some dates missing) → schedule the days that do have a menu; flag the rest in the run log and the dashboard banner.
- For every `(date, meal_slot)` that has a menu, for every active student with a valid Calendar token: check `skip_meal_slots`, then a per-student, per-date, per-slot **claim row** (`scheduled_meals`, a unique constraint) — only the run that wins the claim touches that student's Calendar, so two overlapping runs (the cron job and a late-upload catch-up trigger) can never double-schedule the same meal.
- A student whose Google token has actually expired/been revoked is skipped (flagged `needs_reauth`, with a "Reconnect Google" dashboard banner) rather than failing the whole batch — **one broken student's token never blocks anyone else's meals.**

### 3. Match menu against preferences (per student — unchanged logic, just run once per active student instead of once total)

- **Exact (normalized) match in `known_dishes`** → use that student's stored, running score directly.
- **No match** → estimate from that student's own `tag_weights`; no recognized tags at all → `no_data`, skip rather than guess.
- New dishes get added to that student's `known_dishes` with the estimate, so their own `tag_weights` self-improve on their next run — this is per-student, since two students can have completely different tastes for the same dish.

### 4. Check calendar conflicts and schedule (per student's own calendar)

- **No conflict** → create the event at the normal mess timing, on that student's own calendar, using a deterministic event id (`sha256(user_id:date:meal_slot)`, already a valid Calendar-id charset).
- **Conflict** → search the rest of the mess's window for a free slot; found → schedule there.
- **No alternate slot either** → still create the event (Calendar stays the single interface), titled `[Conflict] ... — please rearrange`, `conflict_resolved: false`, so the student sees it rather than the meal silently vanishing.
- A retry after a mid-request crash (event created, claim row never updated) is recovered by treating Calendar's `409` on the same deterministic id as success, not a duplicate.

### 5. Calendar event

Kept short, per SKILL.md's original design principle — Calendar is the actual interface, not a separate screen. Example:

> **Top picks: Paneer Tikka [Aahar] 4.2, Dal Makhani [Rasoi] 4.0**

### 6. Feedback — two channels, deliberately asymmetric

This is the one place the multi-tenant rebuild genuinely changed the *shape* of the design, not just its scale, so it's spelled out precisely:

- **Automated, negative-only** (`agent/jobs/collect_feedback.py`, runs before each Monday's scheduling): for every unresolved past meal, check whether the Calendar event still exists. Deleted/cancelled → apply a decline to its `top_picks`. Still there → **no signal at all** — a student's own RSVP on their own calendar doesn't mean anything, so silence is never read as "liked it."
- **Manual, both directions** (`/feedback` page): the student can rate a specific past meal — ate it and liked it, ate it and was unsure, or skipped it — with an optional note that Gemini classifies for sentiment (`GeminiSkill.interpret_feedback_note`, the same call the single-user build used, now driven by an explicit page visit instead of an RSVP). **This is the only path that can raise a rating from real experience** — without it, a student who loves every dish the agent recommends would never see that reflected, because the automated channel can only ever push a rating down.
- Both channels write through the same `MenuPreferenceMatchingSkill.apply_feedback` — the same first-feedback-replaces-estimate, then-delta-adjusts logic as the single-user build, completely unchanged. Only *how* a response reaches that function changed.

`times_eaten` is still tracked alongside `rating` as a **confidence** signal, not a stand-in for sentiment — a single lukewarm response after one meal is genuinely uncertain, not automatically disliked.

## Data Model

```python
class ExtractedDish(BaseModel):
    name: str
    mess: str                         # "Rasoi" | "Aahar" | "Both"
    meal_slot: str                     # "breakfast" | "lunch" | "evening_snacks" | "dinner" | "sunday_brunch"
    category: Optional[str]            # the menu section it was listed under
    day: Optional[str]                 # "monday".."sunday" — the board is a weekly rotating menu

class MenuIntake(BaseModel):
    date: date
    source_image_id: str              # sha256 of the uploaded photo — the photo itself is never stored
    extracted_items: list[ExtractedDish]

class KnownDish(BaseModel):
    name: str
    tags: list[str]
    rating: float
    times_eaten: int
    last_response: Optional[str]
    last_note: Optional[str]
    last_seen: date

class UserPreferences(BaseModel):
    user_id: str                       # a Postgres users.id (UUID), not a hardcoded slug
    dietary_restrictions: list[str]
    skip_meal_slots: list[str]
    known_dishes: list[KnownDish]
    tag_weights: dict[str, float]       # derived — never persisted

class ScoredDish(BaseModel):
    name: str; mess: str; tags: list[str]; score: float
    source: str                          # "known_dish" | "known_dish_fuzzy" | "tag_weight_estimate" | "no_data"
    decision: str                        # "eat" | "skip"

class ScheduledMeal(BaseModel):
    date: date; meal_slot: str
    selected_items: list[str]
    top_picks: list[str]                  # subset actually named in the event description
    calendar_event_id: Optional[str]
    scheduled_time: Optional[time]
    conflict_resolved: bool
```

Persisted in Postgres now, not per-user JSON files — see `agent/models_db.py` for the exact table shapes (`users`, `oauth_tokens`, `preferences`, `menu_intake`, `pending_menu_uploads`, `scheduled_meals`) and `agent/repository.py` for the only code that touches them.

## Multi-tenancy notes

- **Identity/access:** Google Sign-In (`agent/web/auth.py`) with PKCE + CSRF state, verifying the ID token's `email_verified`/`hd` claims and that Calendar scope was actually granted — not just checking the email string client-side.
- **Isolation:** every table keyed by `user_id` except the deliberately shared `menu_intake`; `MenuPreferenceMatchingSkill` still only ever operates on the one `UserPreferences` object it's handed, so there's no code path where one student's data can leak into another's scoring.
- **Idempotency:** the `scheduled_meals` unique constraint plus deterministic Calendar event ids are what make "the cron job ran, and someone also just confirmed a late upload for the same week" safe — both can only ever produce one event per student/date/slot.
- **Refresh tokens** are encrypted at rest (`agent/crypto.py`, `MultiFernet`) — a database dump doesn't hand over live Calendar access to the whole college.

## Design decisions — resolved (single-user build, still true, unchanged by the rebuild)

1. **Fuzzy-match threshold: 0.85** on normalized `difflib.SequenceMatcher`, exact match checked first.
2. **Score deltas:** YES +1.0 (first feedback ever: baseline 4.0) · NO −1.0 (baseline 1.5) · MAYBE +0.0 (baseline 2.5), with a ±0.5 note-sentiment bonus on top, clamped to `[0.0, 5.0]`.
3. **No alternate slot either** → still create the event, flagged, rather than silently failing.
4. **Zero recognized tags** → `no_data`, score 0.0, decision `skip` — no guessing.
5. **First real feedback replaces the tag-weight estimate outright**; later feedback nudges by the deltas above.
6. **Both messes scored together** for a given slot, never one picked over the other — each `ScoredDish` carries which mess it's from.
7. **`meal_slot` is passed explicitly**, never auto-derived from the calendar weekday — `weekly_run.py`'s per-date/per-slot loop supplies it from what's actually in that date's `menu_intake`, so nothing has to guess which weeks are `sunday_brunch` weeks.

## What Makes This Still Agentic (per `plan.md` §7)

`plan.md`'s original CampusBite plan defined five properties that separate an agentic system from "an app with a few screens." The build here only ever implemented the menu-matching + calendar-scheduling piece of that plan (the budget/perishable and social-invite skills were out of scope from the start — an accepted, pre-existing scope reduction, not something the multi-tenant rebuild changed). Within that narrower scope, here's how each property held up through the rebuild:

1. **Reasons across signals together, not one at a time.** Every scheduling decision still combines a hard dietary exclude, a taste score, a calendar-conflict check, and a skip-slot behavior rule into *one* outcome — schedule, schedule-with-a-flag, or don't. The rebuild added a second layer of this at the week level: `schedule_week()` combines "which dates have a menu," "does this student's token still work," and "did this student opt out of this slot" into one of *missing / partial / scheduled* per student, not three separate checks the student has to piece together.
2. **Makes a judgment call, not a lookup.** The conflict-resolution priority order (default slot → alternate slot → flag) and the eat/skip threshold are untouched. The missing/partial/full menu classification is a new instance of the same pattern — a priority-ordered decision, not a fixed script.
3. **Works toward a goal, not just reacting to a click.** This is *stronger* after the rebuild, not just preserved: the single-user build required someone to run a CLI command per meal, per day. Now a Railway Cron job schedules every active student's whole week with no one asking it to, every Monday, and a late menu upload triggers the same logic in the background without waiting for the next cron tick.
4. **Keeps its parts separate on purpose.** `MenuPreferenceMatchingSkill` is still completely pure — every method takes a `UserPreferences` object explicitly, touches no file, no Calendar, no database. `GoogleCalendarSkill`, `GeminiSkill`, and the new `Repository` are all injected into the orchestrator (`ReActMealAgent`), which only ever calls each one's final-answer method, never reaches into how any of them work internally. This is enforced by the actual code structure, not just claimed in this document.
5. **Updates itself.** This is the property the rebuild put at real risk, worth stating honestly: removing the single-attendee RSVP (necessary for multi-tenancy) initially left only a negative signal (`collect_feedback.py`'s cancellation check) — a preference model that could only ever get *more pessimistic* over time, never actually learn what a student liked. The `/feedback` page (see "Feedback" above) is what restores the positive direction, and it's the only production caller left of `GeminiSkill.interpret_feedback_note`. Without it, this property would only be half-true.
