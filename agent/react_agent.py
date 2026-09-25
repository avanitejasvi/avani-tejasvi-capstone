import difflib
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Optional, Protocol

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from agent.timezone import IST

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

EAT_THRESHOLD = 3.0
RATING_MIN, RATING_MAX = 0.0, 5.0
# A dish scoring above this but still below EAT_THRESHOLD isn't confirmed
# disliked either (DISLIKE=2.0 and RESPONSE_BASELINE["no"]=1.5 both land at
# or below it) — only used as a fallback recommendation tier, and only when
# nothing on the menu clears EAT_THRESHOLD at all. See select_fallback_picks.
FALLBACK_THRESHOLD = 2.0

# Open Question 1 (skills.md): fuzzy-match threshold. 0.85 on a normalized
# (lowercase, whitespace-collapsed) difflib ratio cleanly separates OCR noise
# ("paneer tkka" vs "paneer tikka" -> 0.957) from genuinely different dishes
# ("veg manchurian rice bowl" vs "veg manchurian" -> 0.737), verified against
# real dish names before picking the cutoff.
FUZZY_MATCH_THRESHOLD = 0.85

# Open Question 2: concrete score-adjustment sizes, applied as a delta once a
# dish already has real feedback history.
RESPONSE_DELTA = {"yes": 1.0, "no": -1.0, "maybe": 0.0}
NOTE_DELTA_BONUS = {"positive": 0.5, "negative": -0.5, "neutral": 0.0}
# Open Question 5: the first real response a dish ever gets REPLACES its
# tag_weight_estimate outright (direct signal beats an inference). These are
# the baselines that first response sets, before any note bonus on top.
RESPONSE_BASELINE = {"yes": 4.0, "no": 1.5, "maybe": 2.5}

# gemini-3.6-flash was intermittently 503 UNAVAILABLE (Google-side capacity) when this
# was tested; gemini-3.5-flash-lite responded reliably and is cheaper/faster, so it's
# the default — override via GEMINI_MODEL if that changes.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
# The real menu board has 300+ dishes once every mess/meal is included; the default
# output-token limit truncates the JSON array mid-string on a board this size. 16000
# was enough for earlier boards but not every real photo — raised further, with
# _parse_json_array_lenient below as a safety net if a board is bigger still.
GEMINI_EXTRACTION_MAX_OUTPUT_TOKENS = 32000

MEAL_DURATION_MINUTES = 45
SLOT_SEARCH_STEP_MINUTES = 15

# Ingredient/keyword vocabulary derived from dish names — used both as the
# fallback when a dish has no category at all (or Gemini/live calls are
# unavailable, keeping the whole loop runnable offline, matching skills.md's
# own "test before wiring up live calls" order) AND, since it's unioned onto
# a dish's category tags in derive_tags_from_category, as the deterministic
# related-food signal: two dishes in different menu categories that share a
# core ingredient (e.g. "Aloo Paratha" under Breakfast and "Dum Aloo
# Banarasi" under Gravy Veg both containing "aloo") still connect through
# tag_weights, so a confirmed dish's rating estimates a genuinely new
# dish that shares an ingredient with it — not just a shared menu section.
# Every keyword below is grounded in real dish names on the board (see
# agent/data/menu_intake_2026-09-15.json), same discipline
# preference_questions.py already follows for its seeded dishes.
NAIVE_TAG_KEYWORDS = {
    "paneer": ["veg", "jain", "paneer"], "dal": ["dal"], "rice": ["rice"], "chawal": ["rice"],
    "gravy": ["gravy"], "curry": ["gravy"], "masala": ["gravy"], "sabzi": ["dry", "veg"],
    "chole": ["gravy", "veg", "chickpea"], "chana": ["chickpea"], "raita": ["curd-based"], "curd": ["curd-based"],
    "kheer": ["dessert", "sweet"], "halwa": ["sweet"], "ladoo": ["sweet"], "cake": ["sweet"],
    "pastry": ["dessert"], "malpua": ["sweet"], "chicken": ["non-vegetarian"],
    "mutton": ["non-vegetarian"], "fish": ["non-vegetarian"], "egg": ["non-vegetarian"],
    "risotto": ["fusion"], "fusion": ["fusion"], "manchurian": ["fusion", "spicy", "indo-chinese"],
    # Ingredient-level tags — e.g. Aloo Bhaji/Aloo Tomato Rassa/Dum Aloo
    # Banarasi/Potato 65 all share "potato" despite different categories.
    "aloo": ["potato"], "potato": ["potato"], "rajma": ["kidney-bean"],
    "lauki": ["bottle-gourd"], "parwal": ["pointed-gourd"], "turai": ["ridge-gourd"],
    "mushroom": ["mushroom"], "soya": ["soy"],
    # Style tags the general onboarding questions (preference_questions.py)
    # seed profile entries on — so a baseline answer like "avoid fried food"
    # reaches dishes that haven't appeared on any board yet.
    "pakoda": ["fried"], "wada": ["fried"], "samosa": ["fried"],
    "kachori": ["fried"], "poori": ["fried"], "fried": ["fried"], "tempura": ["fried"],
    " 65": ["fried", "spicy"], "spicy": ["spicy"], "chilli": ["spicy"], "chilly": ["spicy"],
    "mirchi": ["spicy"], "schezwan": ["spicy", "indo-chinese"], "manchow": ["indo-chinese"],
    "noodles": ["indo-chinese"], "hakka": ["indo-chinese"],
    "idly": ["south-indian"], "idli": ["south-indian"], "upma": ["south-indian"],
    "uttapam": ["south-indian"], "medu": ["south-indian"], "rasam": ["south-indian"],
    "cornflakes": ["light"], "cut fruit": ["light"], "bread": ["light"],
    "dahi": ["curd-based"], "taak": ["curd-based"], "butter milk": ["curd-based"],
}

# Words a keyword/restriction appears inside of without meaning it — "egg"
# in "Assorted Veggies Tempura" isn't egg. Removed from a dish name before
# substring matching, for tag inference and dietary restrictions alike.
# Substring matching itself stays (so e.g. an allergy restriction "nut" still
# catches "peanuts"/"coconut"); this only carves out known false positives.
SUBSTRING_FALSE_POSITIVES = ("veggie",)


def _name_for_matching(name: str) -> str:
    lowered = name.lower()
    for word in SUBSTRING_FALSE_POSITIVES:
        lowered = lowered.replace(word, " ")
    return lowered


class GoogleAuthUnavailable(Exception):
    pass


class NoMenuAvailable(Exception):
    """Raised by act_menu_intake in live mode when no shared MenuIntake has
    been uploaded yet for a date — a genuine state to surface, never silently
    papered over with the offline fixture in production."""

    def __init__(self, event_date: date):
        self.event_date = event_date
        super().__init__(f"no menu uploaded for {event_date.isoformat()}")


class ExtractedDish(BaseModel):
    name: str
    mess: str  # "Rasoi" | "Aahar" (or "Both" for a same_for_both_messes meal)
    meal_slot: str  # "breakfast" | "lunch" | "evening_snacks" | "dinner" | "sunday_brunch"
    category: Optional[str] = None  # e.g. "Dal", "Gravy Veg - Jain" — the menu section it was listed under
    day: Optional[str] = None  # "monday".."sunday" — the board is a weekly rotating menu, one column per weekday


class MenuIntake(BaseModel):
    date: date
    source_image_id: str
    extracted_items: list[ExtractedDish]


class KnownDish(BaseModel):
    name: str
    tags: list[str]
    rating: float
    times_eaten: int = 0
    last_response: Optional[str] = None
    last_note: Optional[str] = None
    last_seen: date
    # "confirmed" (explicit feedback: a real RSVP, manual /feedback, a
    # check-in answer, a baseline questionnaire answer, or a profile edit),
    # "inferred" (a tag_weight estimate, never confirmed), or "unknown"
    # (no_data — registered so it can be cooldown-tracked for check-in, but
    # carries no real signal). See MenuPreferenceMatchingSkill.match_dish/
    # apply_feedback for how this is set and never silently upgraded.
    confidence: str = "inferred"
    last_checkin_asked: Optional[date] = None


class UserPreferences(BaseModel):
    user_id: str
    dietary_restrictions: list[str]
    skip_meal_slots: list[str]
    known_dishes: list[KnownDish]
    tag_weights: dict[str, float] = Field(default_factory=dict)
    # Free-text notes the user hand-wrote into preferences_u001.json explaining
    # non-obvious tag_weights behavior. Not used by any logic — round-tripped
    # as-is so a live run's write-back doesn't silently erase them.
    comment: Optional[str] = None
    tag_weights_note: Optional[str] = None


class AgentRepository(Protocol):
    """What ReActMealAgent needs from persistence — satisfied structurally by
    agent/repository.py's Postgres-backed Repository (production, web app,
    jobs) and by this module's own FileRepository (local CLI dry-runs). No
    import of agent/repository.py here, on purpose: it imports models from
    this module, and a two-way import would be circular."""

    def load_preferences(self, user_id: str) -> "UserPreferences": ...

    def save_preferences(self, user_id: str, prefs: "UserPreferences") -> None: ...

    def get_menu_intake(self, event_date: date) -> Optional["MenuIntake"]: ...


class ScoredDish(BaseModel):
    name: str
    mess: str
    tags: list[str]
    score: float
    source: str  # "known_dish" | "known_dish_fuzzy" | "tag_weight_estimate" | "no_data"
    decision: str  # "eat" | "skip"
    confidence: str = "unknown"  # "confirmed" | "inferred" | "unknown" — see KnownDish.confidence


class ScheduledMeal(BaseModel):
    date: date
    meal_slot: str
    selected_items: list[str]
    # Whatever's actually named in the event description — normally a subset
    # of selected_items (decision="eat"), but when there's no eat pick at all
    # this holds the fallback picks instead (see act_schedule), which are
    # NOT in selected_items since their decision is still "skip".
    top_picks: list[str] = Field(default_factory=list)
    calendar_event_id: Optional[str] = None
    scheduled_time: Optional[dt_time] = None
    conflict_resolved: bool = False


class MealResponse(BaseModel):
    calendar_event_id: Optional[str]
    response: Optional[str]  # "yes" | "no" | "maybe" | None
    note: Optional[str]
    processed: bool = False


@dataclass
class Trace:
    step: int
    thought: str
    action: str
    action_input: dict
    observation: str


def normalize_dish_name(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def find_fuzzy_match(name: str, known_dishes: list[KnownDish], threshold: float = FUZZY_MATCH_THRESHOLD) -> Optional[KnownDish]:
    """OCR Reliability Safeguard: exact-normalized match first, then a
    similarity check, so a slightly misread name doesn't silently create a
    duplicate 'new' dish instead of matching its real history."""
    normalized = normalize_dish_name(name)
    best_dish, best_ratio = None, 0.0
    for dish in known_dishes:
        candidate = normalize_dish_name(dish.name)
        if candidate == normalized:
            return dish
        ratio = difflib.SequenceMatcher(None, normalized, candidate).ratio()
        if ratio > best_ratio:
            best_dish, best_ratio = dish, ratio
    return best_dish if best_ratio >= threshold else None


def derive_tags_from_category(category: Optional[str], name: str) -> list[str]:
    """A dish's menu category (e.g. 'Gravy Veg - Jain') is a much more
    reliable general tag source than guessing from the name alone, since it
    comes straight from the mess's own menu structure (mess_structure.json)
    — but it only ever describes the menu SECTION, never the ingredient. So
    category tags and name-derived ingredient tags (infer_tags_naive) are
    always unioned, not one-or-the-other: "Aloo Paratha" (Breakfast) and
    "Dum Aloo Banarasi" (Gravy Veg) need to share "potato" despite being in
    completely different sections, for compute_tag_weights/match_dish's
    tag_weight_estimate to connect them as related foods."""
    category_tags = [tag for tag in re.split(r"[\s/\-]+", category.lower()) if tag] if category else []
    tags = list(category_tags)
    for tag in infer_tags_naive(name):
        if tag not in tags:
            tags.append(tag)
    return tags


def infer_tags_naive(name: str) -> list[str]:
    lowered = _name_for_matching(name)
    tags: list[str] = []
    for keyword, keyword_tags in NAIVE_TAG_KEYWORDS.items():
        if keyword in lowered:
            for tag in keyword_tags:
                if tag not in tags:
                    tags.append(tag)
    return tags


def deterministic_event_id(*parts: str) -> str:
    """Google Calendar event ids must be 5-1024 chars of lowercase base32hex
    (0-9, a-v). A stable hash of the identifying parts (user, date, slot)
    means retrying a create after a mid-request crash reuses the same id
    instead of a random one — a 409 from Calendar then means "already
    created", not "new event". A hex digest is already valid: 0-9a-f is a
    subset of 0-9a-v, so no charset remapping is needed."""
    return hashlib.sha256(":".join(parts).encode()).hexdigest()


class FilesystemTool:
    @staticmethod
    def read_json(path: Path) -> dict:
        return json.loads(path.read_text())

    @staticmethod
    def write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, indent=2, default=str))


def load_mess_structure(fs: FilesystemTool = FilesystemTool()) -> dict:
    return fs.read_json(DATA_DIR / "mess_structure.json")


def load_mess_timings(fs: FilesystemTool = FilesystemTool()) -> dict[str, tuple[dt_time, dt_time]]:
    rows = fs.read_json(DATA_DIR / "mess_timings.json")
    return {
        row["meal_slot"]: (dt_time.fromisoformat(row["start_time"]), dt_time.fromisoformat(row["end_time"]))
        for row in rows
    }


class GeminiSkill:
    """Wraps the Gemini API calls the skill relies on: reading a photographed
    menu image (structured by mess/meal/category, per mess_structure.json)
    and interpreting a feedback note's sentiment. Both fall back to a
    deterministic path if unavailable or a call fails — the fixture for
    extraction, a keyword heuristic for sentiment — so the loop stays runnable
    offline."""

    def __init__(self, api_key: Optional[str] = None, model: str = GEMINI_MODEL):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def _client_or_raise(self):
        if not self.available:
            raise RuntimeError("GEMINI_API_KEY is not set")
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def extract_dishes_from_image(self, image_bytes: bytes, mime_type: str, mess_structure: dict) -> list[ExtractedDish]:
        from google.genai import types

        client = self._client_or_raise()
        instructions = mess_structure.get("extraction_instructions_for_gemini", "")
        prompt = (
            "This is a photo of a college mess menu board. It is a WEEKLY ROTATING menu — each "
            "meal section has one column or block per day of the week (Monday through Sunday), not "
            "just one set of dishes. You must identify which weekday column each dish belongs to; "
            "do not flatten the whole week into one undated list. "
            f"{instructions}\n\n"
            f"Mess/meal/category structure to use (JSON): {json.dumps(mess_structure.get('meals', {}))}\n\n"
            "Return ONLY a JSON array of objects, one per dish visible on the board, each shaped "
            'exactly like {"name": str, "mess": "Rasoi"|"Aahar"|"Both", "meal_slot": str, "category": str|null, '
            '"day": "monday"|"tuesday"|"wednesday"|"thursday"|"friday"|"saturday"|"sunday"|null} '
            "(meal_slot must be one of breakfast/lunch/evening_snacks/dinner/sunday_brunch; category is the exact "
            "bold heading the dish appeared under, or null if the meal has no fixed category list yet; day is null "
            "only if the board genuinely has no day-of-week structure for that section). "
            "Fix obvious OCR artifacts in dish names but do not translate or rename them. No prose, no markdown fences."
        )
        response = client.models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), prompt],
            config=types.GenerateContentConfig(max_output_tokens=GEMINI_EXTRACTION_MAX_OUTPUT_TOKENS),
        )
        parsed = _parse_json_array_lenient(_strip_code_fence(response.text))
        return [ExtractedDish(**item) for item in parsed]

    def interpret_feedback_note(self, dish_name: str, response: str, note: Optional[str]) -> str:
        """Called (via agent/feedback_sentiment.py's shared helper, when a
        student hasn't set their own bring-your-own key) from every real
        feedback path: the weekly collect_feedback.py job reading a real
        Calendar RSVP comment, and the manual /feedback page."""
        if not note:
            return "neutral"
        prompt = (
            f"A student was asked about eating '{dish_name}' at the mess, responded "
            f"'{response}', and added this note: \"{note}\". Classify the note's "
            "sentiment toward the dish as exactly one word: positive, negative, or neutral."
        )
        # No internal try/except: a failure here (bad model name, network, quota) must
        # propagate to the caller so it's logged truthfully.
        client = self._client_or_raise()
        result = client.models.generate_content(model=self.model, contents=[prompt])
        word = result.text.strip().lower()
        if word not in {"positive", "negative", "neutral"}:
            raise ValueError(f"Gemini returned an unrecognized sentiment word: {word!r}")
        return word


def _parse_json_array_lenient(text: str) -> list:
    """A big enough real board can still exceed even a raised output-token
    ceiling, cutting Gemini's response off mid-object. Rather than losing the
    whole week's extraction to one broken trailing item, salvage every
    complete object up to the truncation point and close the array there —
    a partial menu the student can re-upload to fill in is far better than a
    hard failure on the whole request."""
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        truncated = text[: exc.pos]
        last_complete = truncated.rfind("}")
        if last_complete == -1:
            raise
        return json.loads(truncated[: last_complete + 1] + "]")


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```$", "", text)
    return text.strip()


class GoogleCalendarSkill:
    """Built from one user's own Credentials (agent/repository.py decrypts
    and refreshes their stored token) — every event this creates lives on
    that user's own calendar. There is no single "the" Drive/Calendar
    account anymore."""

    def __init__(self, credentials: Credentials, calendar_id: str = "primary"):
        self.calendar_id = calendar_id
        self.service = build("calendar", "v3", credentials=credentials, cache_discovery=False)

    def list_events_in_window(self, event_date: date, window: tuple[dt_time, dt_time]) -> list[dict]:
        start_dt = datetime.combine(event_date, window[0], tzinfo=IST)
        end_dt = datetime.combine(event_date, window[1], tzinfo=IST)
        result = (
            self.service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=start_dt.isoformat(),
                timeMax=end_dt.isoformat(),
                singleEvents=True,
            )
            .execute()
        )
        return result.get("items", [])

    def create_event_with_id(
        self, event_id: str, meal_slot: str, event_date: date, start_time: dt_time, description: str,
        flagged: bool = False, attendee_email: Optional[str] = None,
    ) -> str:
        """Uses a deterministic event id (see deterministic_event_id) so a
        retry after a mid-request crash — event created, but the caller's DB
        write never landed — is recovered by treating Calendar's 409 as
        success rather than creating a duplicate.

        attendee_email, when given, adds the student as a real attendee on
        their own event — an event you merely own doesn't get an actionable
        RSVP, but one you're also listed as an attendee on does (this
        project's own single-user history confirms it: real YES/NO/MAYBE +
        comment feedback worked exactly this way before the multi-tenant
        rebuild). Requires sendUpdates="all" so Calendar actually notifies
        the attendee — see get_response for reading the RSVP back."""
        start_dt = datetime.combine(event_date, start_time)
        end_dt = start_dt + timedelta(minutes=MEAL_DURATION_MINUTES)
        summary = f"Mess meal check: {meal_slot.replace('_', ' ').title()}"
        if flagged:
            summary = f"[Conflict] {summary} — please rearrange"
        body = {
            "id": event_id,
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"},
        }
        send_updates = "none"
        if attendee_email:
            body["attendees"] = [{"email": attendee_email}]
            send_updates = "all"
        try:
            created = self.service.events().insert(calendarId=self.calendar_id, body=body, sendUpdates=send_updates).execute()
            return created["id"]
        except HttpError as exc:
            if exc.resp.status == 409:
                return self._recover_from_conflict(event_id, body, send_updates)
            raise

    def _recover_from_conflict(self, event_id: str, body: dict, send_updates: str) -> str:
        """A 409 on insert means this id already exists — either (a) a real
        retry after a mid-request crash, where the event is already exactly
        what we'd create, or (b) this id was used by an event the student
        later deleted from their own Calendar: Google tombstones a deleted
        event's id as status="cancelled" rather than freeing it, so the very
        same (user, date, slot) being scheduled again 409s here too. Case
        (b) needs an explicit revive (update(), not a plain get()) — a
        cancelled event is invisible in the Calendar UI, so silently
        returning its id, as this used to do, looked like a successful
        create while actually leaving a hidden dead event behind."""
        existing = self.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
        if existing.get("status") == "cancelled":
            revived = self.service.events().update(
                calendarId=self.calendar_id, eventId=event_id, body=body, sendUpdates=send_updates
            ).execute()
            return revived["id"]
        return existing["id"]

    def create_reminder_event(self, event_id: str, event_date: date, summary: str, description: str) -> str:
        """Same deterministic-id + 409-recovery pattern as create_event_with_id,
        for the weekly "no menu uploaded yet" reminder — a separate small method
        rather than overloading create_event_with_id's meal-specific summary/
        flagged-conflict logic for an unrelated kind of event."""
        start_dt = datetime.combine(event_date, dt_time(9, 0))
        end_dt = start_dt + timedelta(minutes=15)
        body = {
            "id": event_id,
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"},
        }
        try:
            created = self.service.events().insert(calendarId=self.calendar_id, body=body).execute()
            return created["id"]
        except HttpError as exc:
            if exc.resp.status == 409:
                return self._recover_from_conflict(event_id, body, send_updates="none")
            raise

    def update_description(self, event_id: str, description: str) -> None:
        """Refreshes just an already-scheduled event's description (e.g.
        after preferences changed since it was created) — a targeted patch,
        not create_event_with_id's full create/revive path, so it never
        touches the event's time, attendee, or RSVP state."""
        try:
            self.service.events().patch(calendarId=self.calendar_id, eventId=event_id, body={"description": description}).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                return  # deleted since scheduling — nothing to refresh
            raise

    def get_event_status(self, event_id: str) -> Optional[str]:
        """Returns None if the event no longer exists (404), else its status
        ("confirmed" or "cancelled"). A cheap existence check — get_response
        below is the real feedback signal now that events carry a real
        attendee again; this stays useful on its own for things like the
        weekly menu-reminder event, which has no attendee to RSVP on."""
        try:
            event = self.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                return None
            raise
        return event.get("status")

    def get_response(self, event_id: str) -> tuple[Optional[str], Optional[str]]:
        """Returns (response, note). Requires the event to have been created
        with attendee_email set (see create_event_with_id) — an event you
        only own, with no attendee entry, has nothing here to read. Google
        Calendar lets an attendee add a free-text comment when RSVPing (the
        'comment' field) — that's the note YES/NO/MAYBE travels with.
        response is None if the event is deleted/cancelled -> caller should
        treat that as a decline; also None if the student genuinely hasn't
        responded yet ("needsAction") -> caller should leave it pending."""
        try:
            event = self.service.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as exc:
            if exc.resp.status == 404:
                return None, None
            raise
        if event.get("status") == "cancelled":
            return None, None
        for attendee in event.get("attendees", []):
            if attendee.get("self"):
                status = attendee.get("responseStatus")
                response = {"accepted": "yes", "declined": "no", "tentative": "maybe"}.get(status)
                return response, attendee.get("comment")
        return None, None

    def delete_event(self, event_id: str) -> None:
        try:
            self.service.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as exc:
            if exc.resp.status != 404:
                raise


def _parse_event_dt(value: str) -> datetime:
    """Google returns dateTime with a +05:30-style offset; strip it so it can
    be compared against the naive Asia/Kolkata wall-clock times used everywhere
    else in this module (mixing naive and aware datetimes raises TypeError)."""
    return datetime.fromisoformat(value).replace(tzinfo=None)


def find_free_slot(existing_events: list[dict], event_date: date, window: tuple[dt_time, dt_time]) -> Optional[dt_time]:
    """Open Question 3 half: search the mess's available window in fixed
    increments for a slot the meal's duration doesn't overlap anything."""
    busy = []
    for event in existing_events:
        start = event.get("start", {}).get("dateTime")
        end = event.get("end", {}).get("dateTime")
        if not start or not end:
            continue
        busy.append((_parse_event_dt(start), _parse_event_dt(end)))

    window_start = datetime.combine(event_date, window[0])
    window_end = datetime.combine(event_date, window[1])
    duration = timedelta(minutes=MEAL_DURATION_MINUTES)
    candidate = window_start
    step = timedelta(minutes=SLOT_SEARCH_STEP_MINUTES)
    while candidate + duration <= window_end:
        candidate_end = candidate + duration
        overlaps = any(candidate < b_end and candidate_end > b_start for b_start, b_end in busy)
        if not overlaps:
            return candidate.time()
        candidate += step
    return None


class MenuPreferenceMatchingSkill:
    def compute_tag_weights(self, prefs: UserPreferences) -> dict[str, float]:
        tag_ratings: dict[str, list[float]] = {}
        for dish in prefs.known_dishes:
            for tag in dish.tags:
                tag_ratings.setdefault(tag, []).append(dish.rating)
        return {tag: round(sum(ratings) / len(ratings), 2) for tag, ratings in tag_ratings.items()}

    def is_slot_skipped(self, prefs: UserPreferences, meal_slot: str) -> bool:
        return meal_slot in prefs.skip_meal_slots

    def violates_dietary_restriction(self, name: str, tags: list[str], prefs: UserPreferences) -> bool:
        item_tags = {tag.lower() for tag in tags}
        item_name = _name_for_matching(name)
        return any(
            restriction.lower() in item_tags or restriction.lower() in item_name
            for restriction in prefs.dietary_restrictions
        )

    def match_dish(self, name: str, mess: str, tags: list[str], prefs: UserPreferences) -> ScoredDish:
        normalized = normalize_dish_name(name)
        for dish in prefs.known_dishes:
            if normalize_dish_name(dish.name) == normalized:
                decision = "eat" if dish.rating >= EAT_THRESHOLD else "skip"
                # Confidence is whatever this dish's own history has already
                # earned — a match here must never silently upgrade an
                # unconfirmed estimate to look "settled".
                return ScoredDish(name=name, mess=mess, tags=tags, score=dish.rating, source="known_dish", decision=decision, confidence=dish.confidence)

        fuzzy = find_fuzzy_match(name, prefs.known_dishes)
        if fuzzy is not None:
            decision = "eat" if fuzzy.rating >= EAT_THRESHOLD else "skip"
            return ScoredDish(name=fuzzy.name, mess=mess, tags=fuzzy.tags, score=fuzzy.rating, source="known_dish_fuzzy", decision=decision, confidence=fuzzy.confidence)

        recognized = [prefs.tag_weights[tag] for tag in tags if tag in prefs.tag_weights]
        if not recognized:
            return ScoredDish(name=name, mess=mess, tags=tags, score=0.0, source="no_data", decision="skip", confidence="unknown")
        estimate = sum(recognized) / len(recognized)
        decision = "eat" if estimate >= EAT_THRESHOLD else "skip"
        return ScoredDish(name=name, mess=mess, tags=tags, score=round(estimate, 2), source="tag_weight_estimate", decision=decision, confidence="inferred")

    def register_new_dishes(self, prefs: UserPreferences, scored: list[ScoredDish], event_date: date) -> UserPreferences:
        existing = {normalize_dish_name(dish.name) for dish in prefs.known_dishes}
        for result in scored:
            if normalize_dish_name(result.name) in existing or result.source not in ("tag_weight_estimate", "no_data"):
                continue
            prefs.known_dishes.append(
                KnownDish(
                    name=result.name, tags=result.tags, rating=result.score, times_eaten=0, last_seen=event_date,
                    confidence=result.confidence,
                )
            )
            existing.add(normalize_dish_name(result.name))
        return prefs

    def apply_feedback(self, prefs: UserPreferences, dish_name: str, response: str, note: Optional[str], note_sentiment: str, event_date: date) -> UserPreferences:
        normalized = normalize_dish_name(dish_name)
        for dish in prefs.known_dishes:
            if normalize_dish_name(dish.name) != normalized:
                continue
            if dish.last_response is None:
                # Open Question 5: first real feedback replaces the estimate outright.
                bonus = NOTE_DELTA_BONUS.get(note_sentiment, 0.0) if response in ("yes", "maybe") else 0.0
                dish.rating = RESPONSE_BASELINE[response] + bonus
            else:
                dish.rating += RESPONSE_DELTA[response] + NOTE_DELTA_BONUS.get(note_sentiment, 0.0)
            dish.rating = max(RATING_MIN, min(RATING_MAX, round(dish.rating, 2)))
            dish.times_eaten += 1
            dish.last_response = response
            dish.last_note = note
            dish.last_seen = event_date
            # An explicit response — from any channel — always outranks
            # inference, and one response is enough; never require repeats.
            dish.confidence = "confirmed"
            break
        return prefs

    def select_top_picks(self, scored: list[ScoredDish], top_n: int = 3) -> list[ScoredDish]:
        eat_items = sorted((s for s in scored if s.decision == "eat"), key=lambda s: s.score, reverse=True)
        return eat_items[:top_n]

    def select_fallback_picks(self, scored: list[ScoredDish], top_n: int = 3) -> list[ScoredDish]:
        """Only meant to be called when select_top_picks found nothing —
        a middle tier (FALLBACK_THRESHOLD < score < EAT_THRESHOLD) that
        isn't confirmed disliked, just not confidently "eat" either, so a
        thin-signal day still names plausible options instead of nothing.
        Never a substitute for a real eat pick — strictly a last resort."""
        candidates = sorted(
            (s for s in scored if FALLBACK_THRESHOLD < s.score < EAT_THRESHOLD),
            key=lambda s: s.score, reverse=True,
        )
        return candidates[:top_n]

    def build_event_description(
        self, scored: list[ScoredDish], top_picks: list[ScoredDish],
        fallback_picks: Optional[list[ScoredDish]] = None, conflict_flag: bool = False,
    ) -> str:
        """Kept deliberately compact: a single short line naming the top-scoring
        picks and which mess each is from, not a full category breakdown — a
        RSVP is per-event, so a shorter list keeps YES/NO/MAYBE meaningful.
        Feedback later only ever targets what's named here (top_picks or, when
        there are none, fallback_picks), never the full eat-decision list, so
        a YES/NO/MAYBE can't silently rate dishes the student never actually saw."""
        eat_count = sum(1 for s in scored if s.decision == "eat")
        if top_picks:
            picks = ", ".join(f"{s.name} [{s.mess}] {s.score:.1f}" for s in top_picks)
            line = f"Top picks: {picks}"
            if eat_count > len(top_picks):
                line += f" (+{eat_count - len(top_picks)} more matched)"
        elif fallback_picks:
            picks = ", ".join(f"{s.name} [{s.mess}] {s.score:.1f}" for s in fallback_picks)
            line = f"No confident picks today — closest options: {picks}"
        else:
            line = "No confident picks today — mostly new or no-data dishes."
        if conflict_flag:
            line = "CONFLICT, please rearrange — " + line
        return line


class ReActMealAgent:
    def __init__(
        self,
        user_id: str,
        calendar_skill: Optional[GoogleCalendarSkill],
        gemini_skill: GeminiSkill,
        matcher: MenuPreferenceMatchingSkill,
        fs: FilesystemTool,
        repo: AgentRepository,
        live: bool = False,
        attendee_email: Optional[str] = None,
    ):
        self.user_id = user_id
        self.calendar = calendar_skill
        self.gemini = gemini_skill
        self.matcher = matcher
        self.fs = fs
        self.repo = repo
        self.live = live
        # The student's own email, added as a real attendee on their own
        # event so their Calendar RSVP is a real, actionable one instead of
        # an event they merely own — see GoogleCalendarSkill.create_event_with_id.
        self.attendee_email = attendee_email
        self.trace: list[Trace] = []
        self._step_counter = 0

    def _log(self, thought: str, action: str, action_input: dict, observation: str) -> None:
        self._step_counter += 1
        entry = Trace(self._step_counter, thought, action, action_input, observation)
        self.trace.append(entry)
        print(f"\n--- Step {entry.step} ---")
        print(f"Thought: {entry.thought}")
        print(f"Action: {entry.action}")
        print(f"Action Input: {entry.action_input}")
        print(f"Observation: {entry.observation}")

    def perceive_preferences(self) -> UserPreferences:
        prefs = self.repo.load_preferences(self.user_id)
        self._log(
            "I need the student's stored preferences before judging any dish.",
            "repository.load_preferences",
            {"user_id": self.user_id},
            f"Loaded {len(prefs.known_dishes)} known dish(es), "
            f"{len(prefs.dietary_restrictions)} dietary restriction(s), skip_meal_slots={prefs.skip_meal_slots}.",
        )
        prefs.tag_weights = self.matcher.compute_tag_weights(prefs)
        self._log(
            "tag_weights is derived data recomputed from known_dishes every run, never hand-maintained.",
            "matcher.compute_tag_weights",
            {"known_dishes": len(prefs.known_dishes)},
            f"Recomputed {len(prefs.tag_weights)} tag weight(s).",
        )
        return prefs

    def reason_skip_check(self, prefs: UserPreferences, meal_slot: str) -> bool:
        skip = self.matcher.is_slot_skipped(prefs, meal_slot)
        self._log(
            f"Before scoring anything, check whether '{meal_slot}' is in skip_meal_slots — "
            "that's a behavior rule, not a taste signal.",
            "matcher.is_slot_skipped",
            {"meal_slot": meal_slot},
            f"'{meal_slot}' {'IS' if skip else 'is NOT'} in skip_meal_slots={prefs.skip_meal_slots}.",
        )
        return skip

    def act_menu_intake(self, event_date: date, cached_items: Optional[list[ExtractedDish]] = None) -> MenuIntake:
        if cached_items is not None:
            # scheduling.py's weekly path: it already loaded the shared, once-a-week
            # upload's extraction for this date and passes it straight in — no per-run
            # Gemini call, no per-user re-fetch of the same physical menu.
            intake = MenuIntake(date=event_date, source_image_id="shared-weekly-upload", extracted_items=cached_items)
            self._log(
                "Reusing the shared weekly upload's extraction instead of calling Gemini per run.",
                "workflow.cached_menu_intake",
                {"event_date": str(event_date)},
                f"Using {len(cached_items)} previously-extracted item(s).",
            )
            return intake

        if self.live:
            intake = self.repo.get_menu_intake(event_date)
            if intake is not None:
                self._log(
                    "No menu was passed in for this run — check the shared MenuIntake Postgres table directly.",
                    "repository.get_menu_intake",
                    {"event_date": str(event_date)},
                    f"Loaded {len(intake.extracted_items)} item(s) uploaded for this date.",
                )
                return intake
            self._log(
                "No menu was passed in for this run — check the shared MenuIntake Postgres table directly.",
                "repository.get_menu_intake",
                {"event_date": str(event_date)},
                "No shared menu has been uploaded for this date yet.",
            )
            raise NoMenuAvailable(event_date)

        path = DATA_DIR / "menu_intake_sample.json"
        intake = MenuIntake(**self.fs.read_json(path))
        intake.date = event_date
        self._log(
            "Dry run — local fixture stands in for a real uploaded menu.",
            "filesystem.read_json",
            {"path": str(path)},
            f"Loaded {len(intake.extracted_items)} local item(s).",
        )
        return intake

    def act_filter_and_score(self, prefs: UserPreferences, intake: MenuIntake, meal_slot: str, event_date: date) -> list[ScoredDish]:
        # Open Question 6: the board covers every mess at once, so both Rasoi and
        # Aahar are scored together for the requested meal_slot — neither is
        # dropped, each recommended dish just shows which mess it's from.
        # The board is also a WEEKLY rotating menu (one column per weekday), so
        # meal_slot alone isn't enough — without a day filter, every weekday's
        # lunch/dinner gets flattened into one bloated list.
        weekday = event_date.strftime("%A").lower()
        candidates = [
            item for item in intake.extracted_items
            if item.meal_slot == meal_slot and (item.day is None or item.day.lower() == weekday)
        ]
        self._log(
            f"The photographed board covers every mess/meal/weekday at once — filter down to just "
            f"'{meal_slot}' on '{weekday}' before scoring, keeping both Rasoi and Aahar "
            "(Open Question 6: combine messes, don't pick one).",
            "workflow.filter_by_meal_slot_and_day",
            {"meal_slot": meal_slot, "weekday": weekday, "total_extracted": len(intake.extracted_items)},
            f"{len(candidates)} item(s) belong to '{meal_slot}' on '{weekday}'.",
        )

        scored = []
        for item in candidates:
            tags = derive_tags_from_category(item.category, item.name)
            if self.matcher.violates_dietary_restriction(item.name, tags, prefs):
                self._log(
                    f"Hard-exclude check for '{item.name}' runs before any scoring.",
                    "matcher.violates_dietary_restriction",
                    {"item": item.name, "tags": tags},
                    f"'{item.name}' excluded — violates dietary_restrictions={prefs.dietary_restrictions}.",
                )
                continue
            result = self.matcher.match_dish(item.name, item.mess, tags, prefs)
            self._log(
                f"'{item.name}' ({item.mess}) — exact match, then fuzzy match, then tag_weight fallback, in that order.",
                "matcher.match_dish",
                {"item": item.name, "mess": item.mess, "category": item.category, "tags": tags},
                f"source={result.source} score={result.score} decision={result.decision}",
            )
            scored.append(result)
        return scored

    def act_register_new_dishes(self, prefs: UserPreferences, scored: list[ScoredDish], event_date: date) -> UserPreferences:
        before = len(prefs.known_dishes)
        prefs = self.matcher.register_new_dishes(prefs, scored, event_date)
        self._log(
            "A tag-weight estimate (or a no_data dish) joins known_dishes now so it has a history "
            "to update the first time real feedback arrives.",
            "matcher.register_new_dishes",
            {"scored_count": len(scored)},
            f"Added {len(prefs.known_dishes) - before} new dish(es) to known_dishes.",
        )
        return prefs

    def act_schedule(self, meal_slot: str, event_date: date, scored: list[ScoredDish]) -> ScheduledMeal:
        timings = load_mess_timings(self.fs)
        window = timings[meal_slot]
        default_start = window[0]
        selected = [s.name for s in scored if s.decision == "eat"]
        top_picks = self.matcher.select_top_picks(scored)
        # Only computed/named when there's no real eat pick at all — a
        # last-resort suggestion, never a second recommendation tier.
        fallback_picks = [] if top_picks else self.matcher.select_fallback_picks(scored)
        top_pick_names = [s.name for s in (top_picks or fallback_picks)]

        if not self.live:
            description = self.matcher.build_event_description(scored, top_picks, fallback_picks)
            self._log(
                "Dry run — computing the schedule without touching live Calendar.",
                "workflow.dry_run_schedule",
                {"meal_slot": meal_slot, "window": [str(window[0]), str(window[1])]},
                f"Would create event at {default_start} with: {description.replace(chr(10), ' | ')}",
            )
            return ScheduledMeal(date=event_date, meal_slot=meal_slot, selected_items=selected, top_picks=top_pick_names, scheduled_time=default_start, conflict_resolved=True)

        try:
            existing = self.calendar.list_events_in_window(event_date, window)
            self._log(
                "Check the mess timing window for existing events before scheduling.",
                "google_calendar.list_events_in_window",
                {"meal_slot": meal_slot, "window": [str(window[0]), str(window[1])]},
                f"Found {len(existing)} existing event(s) in the window.",
            )
        except (GoogleAuthUnavailable, HttpError) as exc:
            self._log(
                "Check the mess timing window for existing events before scheduling.",
                "google_calendar.list_events_in_window",
                {"meal_slot": meal_slot},
                f"Calendar unavailable ({exc}); recommendation computed but not pushed live this run.",
            )
            return ScheduledMeal(date=event_date, meal_slot=meal_slot, selected_items=selected, top_picks=top_pick_names, scheduled_time=None, conflict_resolved=False)

        default_start_dt = datetime.combine(event_date, default_start)
        conflict = any(
            default_start_dt < _parse_event_dt(e["end"]["dateTime"])
            and default_start_dt + timedelta(minutes=MEAL_DURATION_MINUTES) > _parse_event_dt(e["start"]["dateTime"])
            for e in existing if e.get("start", {}).get("dateTime")
        )

        scheduled_time = default_start
        conflict_resolved = True
        flagged = False
        if conflict:
            alternate = find_free_slot(existing, event_date, window)
            if alternate is not None:
                scheduled_time = alternate
                self._log(
                    "Default slot is busy — search the rest of the mess window for a free one.",
                    "workflow.find_free_slot",
                    {"meal_slot": meal_slot},
                    f"Found alternate slot at {alternate}.",
                )
            else:
                # Open Question 3: no alternate slot either -> flag the student
                # rather than silently failing or skipping the day.
                conflict_resolved = False
                flagged = True
                self._log(
                    "No alternate slot exists either — flag the student instead of silently failing.",
                    "workflow.find_free_slot",
                    {"meal_slot": meal_slot},
                    "No free slot in the entire window; scheduling at default time with a conflict flag.",
                )

        description = self.matcher.build_event_description(scored, top_picks, fallback_picks, conflict_flag=flagged)
        event_id = self.act_create_event(meal_slot, event_date, scheduled_time, description, flagged)
        return ScheduledMeal(
            date=event_date, meal_slot=meal_slot, selected_items=selected, top_picks=top_pick_names,
            calendar_event_id=event_id, scheduled_time=scheduled_time, conflict_resolved=conflict_resolved,
        )

    def act_create_event(self, meal_slot: str, event_date: date, start_time: dt_time, description: str, flagged: bool) -> Optional[str]:
        # Deterministic, not random: a retry after a mid-request crash (event created,
        # claim row never updated) reuses this exact id, so Calendar's 409 recovers it
        # instead of creating a duplicate — see GoogleCalendarSkill.create_event_with_id.
        event_id = deterministic_event_id(self.user_id, event_date.isoformat(), meal_slot)
        action_input = {"meal_slot": meal_slot, "event_date": str(event_date), "start_time": str(start_time), "event_id": event_id}
        try:
            created_id = self.calendar.create_event_with_id(
                event_id, meal_slot, event_date, start_time, description, flagged, attendee_email=self.attendee_email
            )
            self._log(
                "Push the result into this user's own Calendar as the actual interface, per SKILL.md.",
                "google_calendar.create_event_with_id",
                action_input,
                f"Created calendar event {created_id}.",
            )
            return created_id
        except (GoogleAuthUnavailable, HttpError) as exc:
            self._log(
                "Push the result into this user's own Calendar as the actual interface, per SKILL.md.",
                "google_calendar.create_event_with_id",
                action_input,
                f"Calendar unavailable ({exc}); recommendation is computed but not pushed to a live calendar this run.",
            )
            return None

    def act_persist_preferences(self, prefs: UserPreferences) -> None:
        self.repo.save_preferences(self.user_id, prefs)
        self._log(
            "known_dishes may have changed this run; tag_weights is never persisted since it is "
            "derived fresh every run.",
            "repository.save_preferences",
            {"user_id": self.user_id},
            f"Wrote {len(prefs.known_dishes)} known dish(es) back via the repository.",
        )

    def run(self, meal_slot: str, event_date: date, cached_items: Optional[list[ExtractedDish]] = None) -> ScheduledMeal:
        prefs = self.perceive_preferences()

        if self.reason_skip_check(prefs, meal_slot):
            self._log("Skip rule matched — halt before any Gemini/Calendar calls.", "workflow.halt", {"meal_slot": meal_slot}, "No event created; this meal slot is excluded by user preference.")
            return ScheduledMeal(date=event_date, meal_slot=meal_slot, selected_items=[])

        intake = self.act_menu_intake(event_date, cached_items=cached_items)
        scored = self.act_filter_and_score(prefs, intake, meal_slot, event_date)
        prefs = self.act_register_new_dishes(prefs, scored, event_date)
        self.act_persist_preferences(prefs)
        scheduled = self.act_schedule(meal_slot, event_date, scored)
        return scheduled


class FileRepository:
    """Local-JSON-backed Repository, used only by main()'s CLI dry-run —
    the real Postgres-backed Repository (agent/repository.py) is what the
    web app and the weekly job use in production. Keeps the project's
    long-standing "test against fixtures before touching anything live"
    workflow working without a database."""

    def __init__(self, fs: FilesystemTool, user_id: str):
        self.fs = fs
        self.user_id = user_id

    def _preferences_path(self) -> Path:
        return DATA_DIR / f"preferences_{self.user_id}.json"

    def load_preferences(self, user_id: str) -> UserPreferences:
        return UserPreferences(**self.fs.read_json(self._preferences_path()))

    def save_preferences(self, user_id: str, prefs: UserPreferences) -> None:
        payload = json.loads(prefs.model_dump_json())
        payload["tag_weights"] = {}
        self.fs.write_json(self._preferences_path(), payload)

    def get_menu_intake(self, event_date: date) -> Optional[MenuIntake]:
        path = DATA_DIR / "menu_intake_sample.json"
        if not path.exists():
            return None
        intake = MenuIntake(**self.fs.read_json(path))
        intake.date = event_date
        return intake


def main() -> None:
    load_dotenv(BASE_DIR.parent / ".env")

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    live = "--live" in sys.argv[1:]

    meal_slot = args[0] if len(args) > 0 else "lunch"
    event_date = date.fromisoformat(args[1]) if len(args) > 1 else date(2026, 9, 18)
    user_id = args[2] if len(args) > 2 else "u001"

    if not live:
        print("Running in DRY RUN mode (default) — no live Calendar calls; preferences/menu are")
        print("read from the local agent/data/*.json fixtures, not Postgres.")
        print("Pass --live to run against a real DB-backed user's Calendar, e.g.:")
        print("  python3 -m agent.react_agent lunch 2026-09-18 <user-uuid> --live\n")

    calendar_skill: Optional[GoogleCalendarSkill]
    if live:
        from agent.db import get_sessionmaker
        from agent.repository import Repository

        db = get_sessionmaker()()
        repo: AgentRepository = Repository(db)
        credentials = repo.build_user_credentials(user_id)  # type: ignore[attr-defined]
        calendar_skill = GoogleCalendarSkill(credentials=credentials)
    else:
        repo = FileRepository(FilesystemTool(), user_id)
        calendar_skill = None  # the dry-run branch of act_schedule never touches self.calendar

    agent = ReActMealAgent(
        user_id=user_id,
        calendar_skill=calendar_skill,
        gemini_skill=GeminiSkill(),
        matcher=MenuPreferenceMatchingSkill(),
        fs=FilesystemTool(),
        repo=repo,
        live=live,
    )

    scheduled = agent.run(meal_slot, event_date)

    print("\n=== Final ScheduledMeal ===")
    print(scheduled.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
