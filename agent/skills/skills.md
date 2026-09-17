In one sentence: tells a dorm student whether to eat mess food or skip it, what to do with the money and groceries if they skip, and when to eat with friends — and one "brain" section combines all three into a single useful message instead of showing three separate screens.
Current build focus: just the Menu Preference Matching skill, running automatically every time the app opens — see Section 4 for exactly what that covers, and Section 14 for the Phase 1 / Phase 2 milestone split.
What's in each section:
Section
What's there
1–2 Problem / Solution
Why this app exists.
3 How AI Will Be Used
What Claude is doing on this project vs. what I'm doing myself.
4 MVP Scope
Exactly what's getting built in the 2-week timeline — nothing more.
5 Stretch / Final Goals
What's being left out on purpose, for later.
6 Known Limitations (MVP)
The honest downsides of typing everything in by hand, and why that's OK for now.
7 What Makes This Agentic
Why this counts as "smart," not just an app with three screens.
8 Skills
The three small programs that do the actual work (menu, budget, calendar).
9 Agent Classification
What memory type, agent structure, and human-oversight level this system actually is, with reasoning.
10 Tech Stack
The tools being used to build it, and why.
11 Folder Structure
How the code files are organized.
12 Agent Orchestration Layer
The "brain" — the rules that decide what to actually show the student.
13 Data Models
The exact shape of the information the app tracks.
14 Milestones
The build order, step by step.
15 Risks
What's likely to go wrong, and the plan to avoid it.
16 Success Metrics
How to know if it's actually working.
Words used below that might be unfamiliar:
Orchestrator — the "brain" that looks at all three skills' answers and decides what to tell the user.
Skill — one of the three small programs (menu matching, budget/perishables, calendar overlap).
Python — the programming language the app is written in.
Streamlit — a tool that turns Python code into a simple app screen, with very little extra work.
SQLite — a lightweight database that's just a single file — no server or setup needed.
Pydantic — a tool that keeps data in the right shape (e.g. makes sure a budget is always a number, never text by accident).
pytest — a tool for automatically checking the code actually works.
MVP — the smallest working version of the app.
Feature flag — an on/off switch for an optional feature, so it can be turned off without breaking anything else.
Composition rule table — the ordered list of 5 rules inside the orchestrator that decide which message to show.
1. Problem
College students living in dorms struggle to manage meals, budgets, and social dining:
Mess monotony and overspending — the same dining hall food over and over means students skip meals and spend extra money at cafes instead.
Grocery spoilage — no fridge in the dorm means food like milk, bread, and fruit goes bad before it gets used.
Social scheduling friction — coordinating meals with friends is hard when everyone's class schedule is different.
2. Solution
CampusBite is a meal and budget planner that's agentic — meaning it doesn't just show information, it makes a judgment call. It looks at a student's food preferences, money situation, groceries, and friends' availability, and decides what's actually worth telling them, instead of showing three unconnected screens they'd have to piece together themselves.
3. How AI Will Be Used
What Claude is actually doing: writing the parts of the code that are mostly mechanical — the data structures, the database setup, the app screens, and the tests — under my direction, and I review everything before it's committed. I'm the one deciding how the app makes decisions: the rule table in Section 12 (which message wins when, and why) is something I'm designing and directing Claude to build, not something I'm handing off.
Why this split makes sense: the parts of this project where a small mistake isn't a big deal (data structures, forms, database setup) are exactly where letting AI move fast is fine, since I'm checking the work anyway. The part that actually decides whether this project is "smart" instead of just three separate tools glued together is the decision logic — so that's the part I'm staying hands-on with rather than letting AI design it on its own. If I let AI design the rules too, the core idea of the project wouldn't really be mine. If I insisted on writing everything myself, I'd waste the 2-week timeline on typing out things that don't need my judgment.
4. MVP Scope
Current focus: just the Menu Preference Matching skill, running automatically every time the app opens. This is a deliberate narrowing from the original 3-skill plan — building and proving out one complete, working slice before adding the other two. Here's exactly what's in this first build:
Typing in (or uploading a spreadsheet of) today's mess menu by hand — no scanning photos of menus
Rating dishes 1–5 and marking dietary needs, entered once and reused as long-term preference data
The app automatically loads today's menu and your saved preferences the moment it opens — no button click needed to see results
For each dish: check hard dietary rules first (e.g. skip anything non-vegan for a vegan student), then score the rest against liked/disliked tags
A clear eat-or-skip flag per dish, with a short reason shown on screen
Everything runs locally, for one person, with no hosting or login system
This slice is fully self-contained and demo-able on its own: open the app, see today's menu already scored, done. It proves the core "agent reasoning" pattern (structured memory lookup + rule-based decision + automatic trigger on open) without needing budget or calendar data yet.
Phase 2 (after this slice works): add the Budget & Perishable skill and the Calendar Overlap skill, then connect all three through the orchestrator's 5-rule decision table (Section 12) — this is the original full MVP scope, just sequenced to come after the menu skill is solid.
5. Stretch / Final Goals
Everything below is being skipped on purpose for now — not forgotten, just not needed to prove the idea works:
Reading a photo or scan of a real menu (this is a genuinely hard problem on its own — not realistic in 2 weeks)
Connecting to a real Google Calendar instead of typing availability in by hand
Actually sending invites through WhatsApp or text instead of just showing them on screen
Having AI rewrite the app's messages to sound friendlier (only attempted if there's time left over — never required for the demo to work)
Supporting more than one user, or putting the app online for others to use
6. Known Limitations (MVP)
These are honest tradeoffs from typing everything in by hand, stated clearly instead of left unsaid:
The budget isn't tracked in real time. Skipping a meal adds a fixed, estimated amount of savings — there's no bank or card connection. Good enough to show the logic works, not a real expense tracker.
Groceries have to be entered by hand. The app has no way to know what's been bought unless someone types it in. No scanning, no camera, no automatic detection.
Friend availability doesn't update itself. If someone's plans change last minute, the app won't know unless they go back in and edit their entry. This is the biggest gap that comes from not using a real calendar.
In everyday use, this amount of manual typing would get old fast — and that's expected. The point of this MVP is to prove the decision-making logic works, not to prove the app is effortless to use every single day.
This is exactly why the Stretch Goals exist. A real calendar connection would fix the availability problem; automatic grocery tracking and real budget syncing would be natural next steps if the project kept going after this.
7. What Makes This Agentic
It reasons across all three parts together, not just one at a time. The app looks at the menu result, the budget result, and the calendar result all at once, and decides on ONE useful thing to say — like "cook with a friend before this food goes bad" — instead of showing three separate, repetitive messages.
It makes a judgment call, not just a lookup. The rules are checked in priority order, and a higher-priority message can cancel out a lower-priority one covering the same situation — that's a real decision about what's worth saying, not a fixed script.
It's working toward a goal, not just reacting to a click. The whole point is to reduce wasted food and money while keeping the student social — across lots of small decisions over the week, not a single button press.
It keeps its parts separate on purpose. The "brain" never reaches into how a skill works internally — it only uses each skill's final answer. This is built into how the code is organized (Section 11), not just something claimed in writing.
It updates itself. Any time something changes — a skipped meal, a new grocery item, an edited availability — the app knows to recheck and refresh its recommendation, rather than waiting to be asked again.
The real value here isn't any one of the three skills by itself — it's the rule table deciding which combined message is actually worth showing, and skipping the repetitive ones.
8. Skills
A "skill" is a small, self-contained piece of code — it takes some information in, does one specific job, and hands back an answer, like a calculator. It doesn't know or care about anything else happening in the app. None of the three skills below know about each other; the menu skill doesn't know about money, the budget skill doesn't know about friends. That separation is what makes each one easy to build and test completely on its own.
Each skill has its own data definitions, its own logic, and its own tests. The orchestrator only ever calls a skill's final function — it never reaches inside.
8.1 Menu Preference Matching
What it does: takes today's menu plus what the student likes and needs dietarily. For each dish, it checks: does this break a hard rule (like non-vegan food for a vegan student)? If not, how well does it match their taste? It returns an eat-or-skip decision with a short reason.
Function: score_menu_items(items: list[MenuItem], prefs: UserPreference) -> list[ScoredMenuItem]
8.2 Budget & Perishable Allocation
What it does: takes which meals got skipped (so how much money that frees up) plus the groceries currently in the room. It splits the freed money between cafe and grocery spending, and separately flags anything close to spoiling.
Function: allocate_budget(skipped: list[SkippedMeal], budget: BudgetState, perishables: list[PerishableItem]) -> AllocationResult
8.3 Calendar Overlap & Invite
What it does: takes the student's free time windows and their friends' free time windows (typed in by hand, not from a real calendar). It finds overlapping windows and drafts a simple invite message.
Functions: find_overlaps(availabilities: list[FriendAvailability], min_duration_minutes: int) -> list[OverlapWindow], draft_invite(window: OverlapWindow, context: dict) -> Invite
9. Agent Classification
In plain terms: this section pins down, precisely, what kind of agent each skill and the overall system is — what memory it uses, how it's structured (single/sequential/parallel), and how much control stays with the human user. Stating this explicitly avoids vague or incorrect agent-architecture claims.
9.1 Memory Type
Neither RAG (retrieval-augmented generation) memory nor graph memory applies here, and forcing a fit would be technically inaccurate:
RAG memory is built for searching large amounts of unstructured text (documents, past conversations) when it's not known in advance which pieces are relevant. There's no such search problem in CampusBite — the exact records needed are already known.
Graph memory is built for reasoning over relationships between entities (e.g. multi-hop connections between people). Nothing in this project needs relationship traversal.
What CampusBite actually uses is structured state retrieval — direct database lookups by key, split into two roles with different lifespans:
Long-term / persistent memory: the UserPreference table (and BudgetState, friend lists). Entered once, reused every day, only changes when the student edits it.
Short-term / working memory: today's MenuItem list and that day's availability entries. Fresh input for a single run, replaced the next day, not carried forward.
The menu-matching skill combines both: it reads the student's long-term preferences and this run's short-term menu data, and produces a decision. No embeddings, similarity search, or retrieval ranking are involved anywhere — every lookup is a direct, known query.
9.2 Agent Structure: Single, Sequential, or Parallel
Each individual skill, on its own, is a single agent/function. It takes one input, applies one deterministic pass of logic, returns one output — no sub-agents, no chaining, no back-and-forth.
Across the whole system, the structure is mostly sequential with one parallel branch. Menu matching runs first; its output (which meals were skipped) feeds directly into the budget/perishable skill, so that step depends on the one before it. The calendar-overlap skill doesn't depend on either of the other two, so it can run independently — in parallel — rather than waiting in line. All three results converge at the orchestrator's rule table (Section 12), which is where they're combined into one final recommendation.
9.3 Human Oversight Level: Human-on-the-Loop
CampusBite is human-on-the-loop, not human-in-the-loop or human-out-of-loop:
Human-in-the-loop would mean the app pauses and waits for approval before doing anything — not the case here; recommendations are generated automatically, with nothing to approve first.
Human-out-of-loop would mean the system acts fully on its own with no person involved at any point — also not the case; the app never takes a real-world action.
Human-on-the-loop fits because the system decides and displays a recommendation autonomously, but a person is always watching and stays fully in control of any actual action. CampusBite never orders food, never spends money, and never sends a real invite on the student's behalf — it only ever suggests. The student reads the eat/skip flag, the budget split, or the invite draft, and decides for themselves whether to act on it.
This matters for the MVP specifically: since nothing in this build autonomously executes an action (no auto-send, no auto-purchase), staying human-on-the-loop isn't just a safe design choice — it's actually simpler to build and test, since the app's only job is to produce a correct recommendation, not to safely execute real-world actions.
10. Tech Stack
|---|---|---| | Language | Python | Matches my actual skill level — no time to learn a new language in 2 weeks. | | App screens | Streamlit | Turns Python code into a working app screen with very little extra setup or learning. | | Behind the scenes | Just plain Python files — no separate server | Doesn't need a server for a demo that only runs on one laptop; makes each skill easy to test on its own. | | Database | SQLite | A single file, no setup or install needed, easy to open and check. | | Data checking | Pydantic | Automatically catches mistakes in data (e.g. text where a number should be). | | Testing | pytest | Simple way to check each skill actually works correctly on its own. | | Optional, only if time allows | Anthropic API (Claude), behind an on/off switch | Only attempted after everything else works — never required for the demo. |
To run it, a grader just needs: pip install -r requirements.txt && streamlit run app.py — no login, no account, no extra setup.
11. Folder Structure
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

12. Agent Orchestration Layer (the "brain")
This is the piece that takes the answers from all three skills and decides which single message is worth showing the student — like a person reading three reports and picking the one thing worth mentioning right now, instead of handing over all three at once. It works like a checklist checked top to bottom: the first rule that matches wins, and only one message gets shown.
When it runs:
When the app loads, or the student hits "Refresh Plan."
Whenever something changes (a skipped meal, an added grocery item, an edited availability) — the old recommendation gets thrown out and recalculated.
What it does, step by step:
Loads today's information (menu, preferences, budget, groceries, availability).
Runs the menu skill to see what's being eaten or skipped.
Runs the budget skill using that result.
Runs the calendar skill to check for overlapping free time.
Checks the 5 rules below, in order, and stops at the first one that matches:
Rule
When it applies
What gets shown
How urgent
A
Food is about to expire, AND a friend is free before it expires, AND there's extra money
"Cook with [friend] using [food] before it expires — you've got $N freed up."
Right now
B
A meal was skipped, AND a friend is free (nothing urgent expiring)
"You freed up $N by skipping [meal] — [friend] is free [time], want to grab food together?"
Today
C
A meal was skipped, but no friend is free
Just the plain budget split
Today
D
Food is about to expire, but no friend is free
A simple "use this soon" reminder
Right now
E
A friend is free, but nothing else stands out
Just the plain invite
This week
Shows up to 3 of the highest-priority messages, so the screen doesn't get cluttered.
The rules are stored as a simple list of data, not buried in a long chain of code — that keeps each one easy to test on its own, and keeps the whole thing from turning into something more complicated than it needs to be. This is also why only one message shows for a given situation: Rule A solves three problems at once and gets checked first, so it takes priority over the simpler rules covering the same information.
13. Data Models (the exact shape of the information)
A "data model" is just a definition of what pieces of information go together, so the app never gets confused about what a piece of data means. Think of it like a form with fixed blank fields — for example, "a menu item always has: a name, a date, which meal it's for, and a few tags like 'vegan' or 'spicy.'" You don't need to read every line of code below — just know each block below defines one of these "forms."
# skills/menu_preference/models.py
class MealSlot(str, Enum):
    BREAKFAST = "breakfast"; LUNCH = "lunch"; DINNER = "dinner"

class MenuItem(BaseModel):
    id: str; na