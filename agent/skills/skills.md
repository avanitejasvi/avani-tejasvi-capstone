# SKILL.md — Menu Preference Matching (Calendar Interface)

## Overview

This skill answers one question, automatically, without the student opening an app: *"Should I eat this meal?"* Instead of a custom screen, the interface **is Google Calendar** — the skill creates a calendar invite for each upcoming meal, with a description listing which menu items match the student's taste, and the student responds by accepting or declining the invite directly in Calendar.

## Interface: Google Drive + Google Calendar

This skill has **Google Drive access**. The menu lives in a real Google Sheet, not a local file:

- A Google Drive folder/file titled **"meal-menu-match"** contains the Google Sheet with the menu data.
- The skill reads this sheet directly (via Drive access) to get each day's menu items — no manual CSV upload, no re-typing the menu by hand.
- For each dish on the sheet, the skill cross-references it against the user's preferences (exact-name match first, `tag_weights` fallback for new dishes — see Workflow below).
- Once the matching is done, the skill goes to **Google Calendar** and creates an event for that meal, with the results written into the event's description.

So the full chain is: **Drive (read menu) → match against preferences → Calendar (create event with results in description) → wait for accept/decline.**

## Data Sources

Two persistent stores:

1. **Menu Sheet** — a Google Sheet inside the "meal-menu-match" Drive file/folder, containing the running menu (ideally one row per dish per day, with columns for name, tags, and date — matching the `MenuArchiveItem` shape below). Read via Drive access each time the skill runs — always pulling the current sheet, not a cached copy.
2. **Preferences File** (`preferences_u001.json`) — one file per user, containing three distinct things that must NOT be blended together:
   - `dietary_restrictions` — hard excludes, checked before anything else.
   - `skip_meal_slots` — a **behavior rule**, not a taste input (e.g. `["breakfast"]` means never /evaluate breakfast at all — this is handled before scoring runs, not as part of it).
   - `known_dishes` — every dish the user has actually eaten/rated before, by exact name, with its own tags and a 1-5 rating.
   - `tag_weights` — a **derived, not hand-maintained** fallback table, recomputed from `known_dishes` every run: for each tag, the average rating across every known dish carrying that tag. Used only when a dish isn't in `known_dishes` yet.

## Workflow (per meal)

1. **Check `skip_meal_slots` first.** If today's meal slot (e.g. "breakfast") is in this list, skip the entire meal — no scoring, no event, no invite. This is a behavior rule, evaluated before any preference logic runs.
2. **Read today's menu items** for that slot from the Menu Sheet, via Google Drive access to the "meal-menu-match" file.
3. **For each item, check `dietary_restrictions`** — if it violates a hard restriction, exclude it immediately, no further scoring.
4. **Check `known_dishes` by exact name.**
   - **Exact match found** → use that dish's stored `rating` directly. This is what makes "I like this one specific dessert but not desserts generally" work correctly — exact-name lookup always wins over the tag-based fallback.
   - **No exact match (new dish)** → fall back to `tag_weights`: average the weight of each of the dish's tags to estimate a score. If the dish has a tag that's never appeared in `known_dishes` before (no weight exists for it), that specific tag contributes no signal — average only the tags that do have weights, and treat a dish with zero recognized tags as genuinely unrated (see note below).
   - Either way, if this is a brand-new dish, add it to `known_dishes` with the tag-estimated score, so `tag_weights` self-improves the next time it's recomputed.
5. **Build the event description** — list which of today's menu items scored above the eat threshold (e.g. 3+), separating "known favorites" (exact match) from "new, estimated" (tag-weight fallback) so the description is honest about which is a guess.
6. **Create the calendar invite** for that meal, with the description from step 5.
7. **Wait for the student's response:** Accept ("yes") → event stays. Decline ("no") → delete the event (per your original spec — see Open Questions for the tradeoff).

## Data Models

```python
class KnownDish(BaseModel):
    name: str                    # exact-match key
    tags: list[str]
    rating: int                  # 1-5
    last_seen: date

class UserPreferences(BaseModel):
    user_id: str
    dietary_restrictions: list[str]
    skip_meal_slots: list[str]         # e.g. ["breakfast"] - behavior rule, not taste
    known_dishes: list[KnownDish]
    tag_weights: dict[str, float]      # derived - recompute from known_dishes each run, don't hand-edit

class MenuArchiveItem(BaseModel):
    id: str
    name: str
    tags: list[str]
    first_seen_date: date

class ScoredDish(BaseModel):
    name: str
    score: float
    source: str                  # "known_dish" (exact match) | "tag_weight_estimate" (new dish)
    decision: str                # "eat" | "skip"

class MealEvent(BaseModel):
    meal_slot: str
    event_date: date
    scored_items: list[ScoredDish]
    calendar_event_id: str | None
    response: str | None         # "yes" | "no" | None (pending)
```

## Open Questions — what your design already resolves, and what's still open

Your `preferences_u001.json` design already answers two of the three original open questions well:

1. **✅ RESOLVED — New/unrated items.** Your design doesn't guess blindly; it estimates from `tag_weights`, which is itself derived honestly from real ratings. This is better than the three options I originally proposed — it's a real weighted estimate, not a neutral placeholder or a made-up guess. Recommend keeping the `source` field on `ScoredDish` ("known_dish" vs "tag_weight_estimate") so the calendar description can honestly flag which recommendations are confident matches vs. estimates.

2. **✅ MOSTLY RESOLVED — How a rating gets in.** New dishes get an estimated rating the moment they're scored (via `tag_weights`), and `tag_weights` recomputes from `known_dishes` each run — so the system self-improves as more dishes get added, without needing a separate manual-rating step. One thing still worth deciding: does the *estimated* rating on a new dish ever get replaced with a *real* rating later (e.g. after the student actually eats it and reacts), or does it just stay as an estimate forever? If you want it to improve, you'll need some way to capture an actual reaction after the meal, not just the yes/no RSVP.

3. **Still open — dish with a completely unrecognized tag.** If a brand-new dish has tags that have never appeared in `known_dishes` at all (so no `tag_weights` entry exists for any of its tags), there's nothing to average. Decide now: does it get a default neutral score (e.g. 3), get excluded from "will like" entirely until it has at least one data point, or get flagged distinctly as "no signal yet" in the event description? **Recommendation:** treat it like Option C from before — show it in the description as "new dish, no data yet" rather than silently guessing 3, so the recommendation stays honest.

4. **Still open — delete-on-decline vs. keep-and-mark-declined.** Deleting the event on "no" matches your spec, but loses the record that the meal was ever evaluated. Given point 2's open question about whether ratings can later improve, you may want the decline to at least feed back into `known_dishes` (e.g. lower the estimated rating slightly) before the event is deleted, rather than just discarding the signal.

5. **Google Drive read access is confirmed available** — the skill reads the "meal-menu-match" sheet directly, no manual CSV upload step needed. What's still open: does the Calendar side use the same kind of direct access (an available Calendar connector), or does it still need separate OAuth setup? **Recommendation unchanged in spirit:** even with Drive/Calendar access available, build and test the scoring logic against the sample data below first, calling it with data read from the real sheet only once the logic itself is proven correct — don't let live Drive/Calendar calls be your first debugging surface.

## Sample Data

**Preferences** (`preferences_u001.json`) — this is your real file, used as-is. Verified: every `tag_weights` value is exactly the average rating across all `known_dishes` carrying that tag (checked computationally — all 15 tags match).

**Menu Sheet stand-in** (`menu_archive_sample.csv`) — a local file matching the columns your real "meal-menu-match" Google Sheet should have, used for testing the scoring logic before connecting to the real sheet. Built to deliberately exercise all three scoring paths against your real preferences file:

| id | name | tags | first_seen_date | Expected scoring path |
|---|---|---|---|---|
| m001 | Curd Rice | curd-based, rice, light | 2026-09-01 | Exact match → rating 5 |
| m002 | Rajma Chawal | rice, dal, gravy, rice-bowl | 2026-09-01 | Exact match → rating 5 |
| m003 | Kheer | dessert | 2026-09-02 | Exact match → rating 2 |
| m004 | Veg Manchurian Rice Bowl | rice-bowl, fusion, spicy | 2026-09-15 | New dish, tags partially known → average of `rice-bowl` (5.0) + `fusion` (5.0); `spicy` has no weight, contributes nothing |
| m005 | Mushroom Risotto | mushroom, italian | 2026-09-15 | New dish, ZERO recognized tags → Open Question 3: no data to average, needs a decision |

This last row (`m005`) is deliberately included so you have a concrete test case for Open Question 3 before you write the code — you'll immediately hit "what do I do here?" if you don't decide the default behavior first.

## What to build first, in order

1. Save `preferences_u001.json` and `menu_archive_sample.csv` (the local stand-in for the real Sheet) as real files in your repo.
2. Decide Open Questions 3 and 4 (unrecognized-tag default, delete-vs-mark-declined) — write the answer directly into this SKILL.md before writing code, so the logic isn't decided ad-hoc mid-implementation.
3. Build the scoring function using only the local sample files — not the real Drive sheet yet. Confirm all 5 sample rows score the way the table above predicts, including that `m005` behaves exactly as you decided in step 2.
4. Only after that logic is tested and correct, swap the menu source from the local CSV to a real read of the "meal-menu-match" Google Sheet via Drive access.
5. Wire up Calendar event creation (writing the matched results into the description) and response handling (accept/decline) last, once steps 3-4 are both working.