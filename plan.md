initial idea:
issue
College students living in dormitories face continuous challenges in managing their daily meals, budgets, and social schedules:
Mess Monotony & Overspending: Repetitive mess food leads students to skip planned dining hall meals, causing them to overspend on last-minute commercial cafe options.
Grocery Spoilage: Without access to dorm refrigeration, groceries like milk, bread, and fruits spoil quickly, resulting in financial and food waste.
Social Scheduling Friction: Coordinating meal times with peers is difficult due to conflicting class schedules and academic commitments.

idea: an agent that is able to analyse weekly meals to predict which meals you will eat at the mess based on your preference. for meals not had in the mess, help you decide whether to spend on groceries or eat at campus cafes. also helps reduce cognitive load around meal times by prompting you about who could eat with based on shared free time.

additional context after planning with claude: 

# CampusBite — Product & Agent Plan

## How to Read This Document

**In one sentence:** CampusBite tells a dorm student whether to eat mess food or skip it, what to do with the money and groceries if they skip, and when to eat with friends — and one "brain" section combines all three into a single useful message instead of showing three separate screens.

**What's in each section:**

| Section | What's there |
|---|---|
| 1–2 Problem / Solution | Why this app exists. |
| 3 How AI Will Be Used | What Claude is doing on this project vs. what I'm doing myself. |
| 4 MVP Scope | Exactly what's getting built in the 2-week timeline — nothing more. |
| 5 Stretch / Final Goals | What's being left out on purpose, for later. |
| 6 Known Limitations (MVP) | The honest downsides of typing everything in by hand, and why that's OK for now. |
| 7 What Makes This Agentic | Why this counts as "smart," not just an app with three screens. |
| 8 Skills | The three small programs that do the actual work (menu, budget, calendar). |
| 9 Tech Stack | The tools being used to build it, and why. |
| 10 Folder Structure | How the code files are organized. |
| 11 Agent Orchestration Layer | The "brain" — the rules that decide what to actually show the student. |
| 12 Data Models | The exact shape of the information the app tracks. |
| 13 Milestones | The build order, step by step. |
| 14 Risks | What's likely to go wrong, and the plan to avoid it. |
| 15 Success Metrics | How to know if it's actually working. |

**Words used below that might be unfamiliar:**

- **Orchestrator** — the "brain" that looks at all three skills' answers and decides what to tell the user.
- **Skill** — one of the three small programs (menu matching, budget/perishables, calendar overlap).
- **Python** — the programming language the app is written in.
- **Streamlit** — a tool that turns Python code into a simple app screen, with very little extra work.
- **SQLite** — a lightweight database that's just a single file — no server or setup needed.
- **Pydantic** — a tool that keeps data in the right shape (e.g. makes sure a budget is always a number, never text by accident).
- **pytest** — a tool for automatically checking the code actually works.
- **MVP** — the smallest working version of the app.
- **Feature flag** — an on/off switch for an optional feature, so it can be turned off without breaking anything else.
- **Composition rule table** — the ordered list of 5 rules inside the orchestrator that decide which message to show.

## 1. Problem

College students living in dorms struggle to manage meals, budgets, and social dining:

- **Mess monotony and overspending** — the same dining hall food over and over means students skip meals and spend extra money at cafes instead.
- **Grocery spoilage** — no fridge in the dorm means food like milk, bread, and fruit goes bad before it gets used.
- **Social scheduling friction** — coordinating meals with friends is hard when everyone's class schedule is different.

## 2. Solution

CampusBite is a meal and budget planner that's **agentic** — meaning it doesn't just show information, it makes a judgment call. It looks at a student's food preferences, money situation, groceries, and friends' availability, and decides what's actually worth telling them, instead of showing three unconnected screens they'd have to piece together themselves.

## 3. How AI Will Be Used

**What Claude is actually doing:** writing the parts of the code that are mostly mechanical — the data structures, the database setup, the app screens, and the tests — under my direction, and I review everything before it's committed. I'm the one deciding how the app makes decisions: the rule table in Section 11 (which message wins when, and why) is something I'm designing and directing Claude to build, not something I'm handing off.

**Why this split makes sense:** the parts of this project where a small mistake isn't a big deal (data structures, forms, database setup) are exactly where letting AI move fast is fine, since I'm checking the work anyway. The part that actually decides whether this project is "smart" instead of just three separate tools glued together is the decision logic — so that's the part I'm staying hands-on with rather than letting AI design it on its own. If I let AI design the rules too, the core idea of the project wouldn't really be mine. If I insisted on writing everything myself, I'd waste the 2-week timeline on typing out things that don't need my judgment.

## 4. MVP Scope

This is a **2-week solo build**, meant to run on my own laptop as a demo — not a real product yet. Here's everything that's actually getting built:

- Typing in (or uploading a spreadsheet of) the mess menu by hand — no scanning photos of menus
- Rating dishes 1–5 and marking dietary needs, so the app can flag each dish as eat-or-skip
- A simple calculator that splits freed-up money between cafe spending and groceries, using fixed rules (not AI guessing)
- A list that tracks how long groceries last and warns before they go bad
- Typing in when you and your friends are free (not connected to a real calendar)
- Checking for overlapping free time among friends
- Showing a simple invite message on screen (it doesn't actually send anything)
- The 5-rule "brain" that ties all three parts together and decides what to show
- Everything runs locally, for one person, with no hosting or login system

The 5-rule decision-making part is the actual thing being graded here — it's what makes this "smart" instead of a basic app, and it can be fully built and tested without needing the internet or any outside service.

## 5. Stretch / Final Goals

Everything below is being skipped on purpose for now — not forgotten, just not needed to prove the idea works:

- Reading a photo or scan of a real menu (this is a genuinely hard problem on its own — not realistic in 2 weeks)
- Connecting to a real Google Calendar instead of typing availability in by hand
- Actually sending invites through WhatsApp or text instead of just showing them on screen
- Having AI rewrite the app's messages to sound friendlier (only attempted if there's time left over — never required for the demo to work)
- Supporting more than one user, or putting the app online for others to use

## 6. Known Limitations (MVP)

These are honest tradeoffs from typing everything in by hand, stated clearly instead of left unsaid:

- **The budget isn't tracked in real time.** Skipping a meal adds a fixed, estimated amount of savings — there's no bank or card connection. Good enough to show the logic works, not a real expense tracker.
- **Groceries have to be entered by hand.** The app has no way to know what's been bought unless someone types it in. No scanning, no camera, no automatic detection.
- **Friend availability doesn't update itself.** If someone's plans change last minute, the app won't know unless they go back in and edit their entry. This is the biggest gap that comes from not using a real calendar.
- **In everyday use, this amount of manual typing would get old fast** — and that's expected. The point of this MVP is to prove the decision-making logic works, not to prove the app is effortless to use every single day.
- **This is exactly why the Stretch Goals exist.** A real calendar connection would fix the availability problem; automatic grocery tracking and real budget syncing would be natural next steps if the project kept going after this.

## 7. What Makes This Agentic

- **It reasons across all three parts together, not just one at a time.** The app looks at the menu result, the budget result, and the calendar result all at once, and decides on ONE useful thing to say — like "cook with a friend before this food goes bad" — instead of showing three separate, repetitive messages.
- **It makes a judgment call, not just a lookup.** The rules are checked in priority order, and a higher-priority message can cancel out a lower-priority one covering the same situation — that's a real decision about what's worth saying, not a fixed script.
- **It's working toward a goal, not just reacting to a click.** The whole point is to reduce wasted food and money while keeping the student social — across lots of small decisions over the week, not a single button press.
- **It keeps its parts separate on purpose.** The "brain" never reaches into how a skill works internally — it only uses each skill's final answer. This is built into how the code is organized (Section 10), not just something claimed in writing.
- **It updates itself.** Any time something changes — a skipped meal, a new grocery item, an edited availability — the app knows to recheck and refresh its recommendation, rather than waiting to be asked again.

**The real value here isn't any one of the three skills by itself — it's the rule table deciding which combined message is actually worth showing, and skipping the repetitive ones.**

## 8. Skills

A "skill" is a small, self-contained piece of code — it takes some information in, does one specific job, and hands back an answer, like a calculator. It doesn't know or care about anything else happening in the app. None of the three skills below know about each other; the menu skill doesn't know about money, the budget skill doesn't know about friends. That separation is what makes each one easy to build and test completely on its own.

Each skill has its own data definitions, its own logic, and its own tests. The orchestrator only ever calls a skill's final function — it never reaches inside.

### 8.1 Menu Preference Matching
- **What it does:** takes today's menu plus what the student likes and needs dietarily. For each dish, it checks: does this break a hard rule (like non-vegan food for a vegan student)? If not, how well does it match their taste? It returns an eat-or-skip decision with a short reason.
- **Function:** `score_menu_items(items: list[MenuItem], prefs: UserPreference) -> list[ScoredMenuItem]`

### 8.2 Budget & Perishable Allocation
- **What it does:** takes which meals got skipped (so how much money that frees up) plus the groceries currently in the room. It splits the freed money between cafe and grocery spending, and separately flags anything close to spoiling.
- **Function:** `allocate_budget(skipped: list[SkippedMeal], budget: BudgetState, perishables: list[PerishableItem]) -> AllocationResult`

### 8.3 Calendar Overlap & Invite
- **What it does:** takes the student's free time windows and their friends' free time windows (typed in by hand, not from a real calendar). It finds overlapping windows and drafts a simple invite message.
- **Functions:** `find_overlaps(availabilities: list[FriendAvailability], min_duration_minutes: int) -> list[OverlapWindow]`, `draft_invite(window: OverlapWindow, context: dict) -> Invite`

## 9. Tech Stack

| Part | What's used | Why |
|---|---|---|
| Language | Python | Matches my actual skill level — no time to learn a new language in 2 weeks. |
| App screens | Streamlit | Turns Python code into a working app screen with very little extra setup or learning. |
| Behind the scenes | Just plain Python files — no separate server | Doesn't need a server for a demo that only runs on one laptop; makes each skill easy to test on its own. |
| Database | SQLite | A single file, no setup or install needed, easy to open and check. |
| Data checking | Pydantic | Automatically catches mistakes in data (e.g. text where a number should be). |
| Testing | pytest | Simple way to check each skill actually works correctly on its own. |
| Optional, only if time allows | Anthropic API (Claude), behind an on/off switch | Only attempted after everything else works — never required for the demo. |

To run it, a grader just needs: `pip install -r requirements.txt && streamlit run app.py` — no login, no account, no extra setup.

## 10. Folder Structure

```
campusbite/
├── app.py                        # main app screen — connects the UI to the orchestrator
├── requirements.txt
├── README.md                     # setup/run instructions
│
├── skills/
│   ├── menu_preference/
│   │   ├── models.py             # the "shape" of menu items and preferences
│   │   ├── matcher.py            # the eat/skip decision logic
│   │   └── tests/test_matcher.py
│   ├── budget_perishable/
│   │   ├── models.py             # the "shape" of budget and grocery info
│   │   ├── allocator.py          # the budget-split and expiry logic
│   │   └── tests/test_allocator.py
│   └── calendar_overlap/
│       ├── models.py             # the "shape" of availability and invites
│       ├── scheduler.py          # the overlap-finding logic
│       └── tests/test_scheduler.py
│
├── orchestrator/
│   ├── models.py                 # the "shape" of a final recommendation
│   ├── rules.py                  # the 5-rule decision table
│   └── engine.py                 # runs everything and produces the final message
│
├── data/
│   ├── db.py                     # sets up and connects to the database
│   ├── schema.sql
│   └── seed/                     # sample menu/availability/grocery files for testing
│
├── storage/                      # where the actual database file lives
│
└── tests/
    ├── conftest.py                # shared test setup
    └── test_orchestrator.py       # tests for the decision-making rules
```

## 11. Agent Orchestration Layer (the "brain")

This is the piece that takes the answers from all three skills and decides which single message is worth showing the student — like a person reading three reports and picking the one thing worth mentioning right now, instead of handing over all three at once. It works like a checklist checked top to bottom: the first rule that matches wins, and only one message gets shown.

**When it runs:**
- When the app loads, or the student hits "Refresh Plan."
- Whenever something changes (a skipped meal, an added grocery item, an edited availability) — the old recommendation gets thrown out and recalculated.

**What it does, step by step:**
1. Loads today's information (menu, preferences, budget, groceries, availability).
2. Runs the menu skill to see what's being eaten or skipped.
3. Runs the budget skill using that result.
4. Runs the calendar skill to check for overlapping free time.
5. Checks the 5 rules below, in order, and stops at the first one that matches:

| Rule | When it applies | What gets shown | How urgent |
|---|---|---|---|
| A | Food is about to expire, AND a friend is free before it expires, AND there's extra money | "Cook with [friend] using [food] before it expires — you've got $N freed up." | Right now |
| B | A meal was skipped, AND a friend is free (nothing urgent expiring) | "You freed up $N by skipping [meal] — [friend] is free [time], want to grab food together?" | Today |
| C | A meal was skipped, but no friend is free | Just the plain budget split | Today |
| D | Food is about to expire, but no friend is free | A simple "use this soon" reminder | Right now |
| E | A friend is free, but nothing else stands out | Just the plain invite | This week |

6. Shows up to 3 of the highest-priority messages, so the screen doesn't get cluttered.

The rules are stored as a simple list of data, not buried in a long chain of code — that keeps each one easy to test on its own, and keeps the whole thing from turning into something more complicated than it needs to be. This is also why only one message shows for a given situation: Rule A solves three problems at once and gets checked first, so it takes priority over the simpler rules covering the same information.

## 12. Data Models (the exact shape of the information)

A "data model" is just a definition of what pieces of information go together, so the app never gets confused about what a piece of data means. Think of it like a form with fixed blank fields — for example, "a menu item always has: a name, a date, which meal it's for, and a few tags like 'vegan' or 'spicy.'" You don't need to read every line of code below — just know each block below defines one of these "forms."

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

## 13. Build Milestones (one per sitting, in order)

Build and test the simplest, most separate pieces first, then connect them at the end. Each skill gets built and checked on its own before the "brain" ever tries to combine them — that way, if something breaks, it's easy to tell exactly which piece caused it.

1. **Set up the project** — folders, empty files, a starter README, first commit.
2. **Define the data and database** — the "shapes" of information for all 3 skills, plus sample test files (menu, groceries, friend availability).
3. **Build the menu skill's logic** — the eat/skip scoring, tested against a few different taste profiles including hard dietary rules.
4. **Build the menu skill's screen** — today's menu with scores and eat/skip badges, plus a preference form.
5. **Build the budget skill's logic** — the savings math, expiry math, cafe/grocery split, tested against edge cases (nothing skipped, everything expired, etc).
6. **Build the budget skill's screen** — a dashboard for budget and groceries with an expiry countdown.
7. **Build the calendar skill's logic** — the overlap-finding and invite drafting, tested with no-overlap, partial-overlap, and multi-friend cases.
8. **Build the calendar skill's screen** — a form for entering availability and viewing overlaps.
9. **Build the "brain"** — the 5-rule decision table, tested against every rule to make sure each one fires correctly. **This is the hardest step — budget extra time here.**
10. **Build the main "Today's Plan" screen** — shows what the brain decided, refreshing whenever something changes.
11. **Run through full test scenarios** — a few made-up situations designed to trigger each rule, checking the whole app works start to finish. Add basic checks for bad input (empty forms, negative numbers).
12. **Polish and write instructions** — clean up the screens, write a clear README, prepare a short write-up. Leave buffer time here in case earlier steps ran long.
13. **Optional stretch** — the "friendlier message" AI writing feature, only if time is left, kept fully optional so the app works the same with it off.
14. **Final rehearsal** — walk through the whole demo start to finish, and record a backup video/screenshots in case something goes wrong live.

## 14. Risks and De-risking

Honest warnings about what's likely to go wrong, each paired with a plan decided on ahead of time — the common mistakes people make on projects like this, planned around instead of discovered halfway through.

- **The "brain" (step 9) will probably take longer than it looks.** It's a small decision-design problem disguised as simple glue code. Fix: write out the 5 rules on paper first, before touching any code, and don't add more than 5.
- **The app-screen tool can sometimes re-run things unexpectedly.** Fix: only run the "brain" logic when something specific triggers it (like a button click), never relying on it happening automatically.
- **Date math for expiry can easily be off by one day.** Fix: test this specific part early and carefully, with obvious examples like "bought today, lasts 3 days — is it expired on day 4?"
- **Changing the data "shapes" after test data already exists causes annoying rework.** Fix: nail down the data shapes fully in step 2, before anything else depends on them.
- **It'll be tempting to build the "fancier AI" feature first because it sounds impressive.** Fix: resist — it's saved for last on purpose, since it's not the part actually being graded.
- **Manually retyping test data over and over wastes time.** Fix: build a few sample files once in step 2, and reuse them instead of retyping.

## 15. Success Metrics

- The percentage of planned meals actually eaten vs. skipped, tracked over the demo period.
- Self-reported reduction in weekly food spending.
- The number of combined recommendations that correctly fire during the full test-scenario pass (step 11).