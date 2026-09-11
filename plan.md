initial idea:
issue
College students living in dormitories face continuous challenges in managing their daily meals, budgets, and social schedules:
Mess Monotony & Overspending: Repetitive mess food leads students to skip planned dining hall meals, causing them to overspend on last-minute commercial cafe options.
Grocery Spoilage: Without access to dorm refrigeration, groceries like milk, bread, and fruits spoil quickly, resulting in financial and food waste.
Social Scheduling Friction: Coordinating meal times with peers is difficult due to conflicting class schedules and academic commitments.

idea: an agent that is able to analyse weekly meals to predict which meals you will eat at the mess based on your preference. for meals not had in the mess, help you decide whether to spend on groceries or eat at campus cafes. also helps reduce cognitive load around meal times by prompting you about who could eat with based on shared free time.

additional context after planning with claude: 

## 9. Agent Orchestration Layer (core agentic value)

**Triggers** (no background cron for a local demo — explicit events instead):
- App load / "Refresh Plan" button → full pipeline run.
- Any data mutation (mark a meal skip, add/remove a perishable, edit availability) → invalidate cached recommendations in session state, re-run on next view.

**Pipeline** (`orchestrator/engine.py`):
1. Load today's context from `data/db.py` (menu items, preferences, budget state, perishables, availabilities).
2. `scored = score_menu_items(...)` → derive `skipped` items.
3. `allocation = allocate_budget(skipped, budget_state, perishables)`.
4. `overlaps = find_overlaps(availabilities, min_duration_minutes=45)`.
5. Run the composition rule table (`orchestrator/rules.py`) — an ordered list of `(condition_fn, build_recommendation_fn)` pairs, top to bottom, each rule able to suppress a lower-priority rule covering the same data:

| Priority | Condition | Composed recommendation | Urgency |
|---|---|---|---|
| A | Expiring perishable (≤1 day) + overlap window before expiry + freed budget > 0 | "Cook with [friend] using [perishable] before it expires — you've got $N freed up." | NOW |
| B | Meal skipped + overlap exists, no urgent perishable | "You freed $N by skipping [meal] — [friend] is free [window], grab food together?" | TODAY |
| C | Meal skipped, no overlap, no urgent perishable | Plain budget allocation: cafe/grocery split | TODAY |
| D | Urgent perishable, no overlap, no skip | "Use it or lose it" reminder | NOW |
| E | Overlap exists, nothing else fired | Plain social invite draft | THIS_WEEK |

6. Output: `list[Recommendation]`, sorted by urgency, capped to top 3 for the dashboard.

Rules live as data in `rules.py`, not an if/elif chain — keeps each rule independently testable and avoids building a generic rule-engine framework that isn't needed.

## 10. Data Models (code-level contracts)

```python
# skills/menu_preference/models.py
class MealSlot(str, Enum):
    BREAKFAST = "breakfast"; LUNCH = "lunch"; DINNER = "dinner"

class MenuItem(BaseModel):
    id: str; name: str; menu_date: date; meal_slot: MealSlot; tags: list[str]

class UserPreference(BaseModel):
    user_id: str; liked_tags: list[str]; disliked_tags: list[str]
    dietary_restrictions: list[str]; tag_weights: dict[str, float]

class Decision(str, Enum):
    EAT = "eat"; SKIP = "skip"

class ScoredMenuItem(BaseModel):
    menu_item: MenuItem; score: int; decision: Decision; reason: str
```

```python
# skills/budget_perishable/models.py
class BudgetState(BaseModel):
    user_id: str; weekly_budget: float; spent_so_far: float

class SkippedMeal(BaseModel):
    menu_item_id: str; meal_slot: str; estimated_savings: float

class PerishableItem(BaseModel):
    id: str; name: str; purchase_date: date; shelf_life_days: int
    quantity: float; unit: str

class AllocationResult(BaseModel):
    cafe_budget: float; grocery_budget: float
    expiring_soon: list[PerishableItem]; reason: str
```

```python
# skills/calendar_overlap/models.py
class FriendAvailability(BaseModel):
    user_id: str; day: date; start_time: time; end_time: time

class OverlapWindow(BaseModel):
    participants: list[str]; day: date; start_time: time
    end_time: time; duration_minutes: int

class InviteStatus(str, Enum):
    DRAFT = "draft"; SENT = "sent"; ACCEPTED = "accepted"

class Invite(BaseModel):
    id: str; window: OverlapWindow; proposed_meal_slot: str
    message: str; status: InviteStatus
```

```python
# orchestrator/models.py
class RecommendationType(str, Enum):
    MEAL_DECISION = "meal_decision"; BUDGET = "budget"
    SOCIAL = "social"; COMPOSED = "composed"

class Urgency(str, Enum):
    NOW = "now"; TODAY = "today"; THIS_WEEK = "this_week"

class Recommendation(BaseModel):
    type: RecommendationType; urgency: Urgency
    text: str; supporting_data: dict
```

## 11. Build Milestones (one per sitting, in order)

1. **Scaffold** — folder structure, requirements.txt, empty modules, README stub, git init + first commit.
2. **Schemas + DB** — Pydantic models across all 3 skills, schema.sql, db init, seed CSVs.
3. **Skill 1 logic** — matcher.py scoring/eat-skip, unit tests across preference profiles including hard-excludes.
4. **Skill 1 UI** — Streamlit page: today's menu with scores/eat-skip badges, preference form.
5. **Skill 2 logic** — allocator.py savings math, shelf-life calc, cafe/grocery split; edge-case tests (zero-skip, all-expired, negative-budget-guard).
6. **Skill 2 UI** — budget dashboard + perishable add/remove/expiry-countdown view.
7. **Skill 3 logic** — scheduler.py overlap-finding across N friends, invite drafting; tests for no-overlap, partial, multi-friend.
8. **Skill 3 UI** — availability entry form, overlap results, draft-invite notification.
9. **Orchestrator core** — engine.py pipeline + rules.py (rules A–E) + Recommendation model, tests hitting every rule branch including suppression. **Highest-risk milestone — budget extra time here.**
10. **Orchestrator UI** — "Today's Plan" dashboard, wired to refresh trigger and session-state invalidation.
11. **End-to-end scenario pass** — 2–3 seed-data scenarios hitting rules A, B, D/E; basic input validation for edge cases.
12. **Polish + README** — UI cleanup, setup/run instructions, architecture write-up for submission. Buffer for slippage.
13. **Stretch (optional)** — LLM-explanation layer behind a feature flag; demo must work identically with it off.
14. **Buffer + demo rehearsal** — full live walkthrough, backup screenshots/recording.

## 12. Risks and De-risking

- **Milestone 9 (composition/suppression rules)** is most likely to run over — it's a small rules-engine design problem disguised as glue code. De-risk: write the priority table as data before writing code, cap at ~5 rules, avoid building a generic rule engine.
- **Streamlit's rerun-on-every-interaction model** can cause unexpected re-runs or stale session state. De-risk: keep the orchestrator a pure function called explicitly, never relying on implicit reactivity.
- **Shelf-life/expiry date math** is a classic off-by-one/timezone trap. De-risk: write edge-case tests in Milestone 5, before UI integration.
- **SQLite schema churn** mid-build causes friction. De-risk: nail `schema.sql` fully in Milestone 2 using the models above as source of truth.
- **Temptation to build the LLM stretch early** because it "sounds more AI." De-risk: scheduled last, behind a flag — the deterministic rules engine is the actual graded deliverable.
- **Manually re-typing test data** wastes time. De-risk: build seed CSVs once in Milestone 2 with enough variety to exercise every rule.

## 13. Success Metrics

- % of planned mess meals actually eaten vs. skipped, tracked over the demo period.
- User-reported (self-tested) reduction in per-week food spend.
- Number of composed recommendations correctly fired per scenario during the end-to-end pass (Milestone 11).