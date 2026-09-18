import difflib
import io
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, time as dt_time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DRIVE_FOLDER_NAME = "meal-menu-match"
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar",
]

EAT_THRESHOLD = 3.0
SCORE_MIN, SCORE_MAX = 0.0, 5.0

# Open Question 1 (skills.md): fuzzy-match threshold. 0.85 on a normalized
# (lowercase, whitespace-collapsed) difflib ratio cleanly separates OCR noise
# ("paneer tkka" vs "paneer tikka" -> 0.957) from genuinely different dishes
# ("veg manchurian rice bowl" vs "veg manchurian" -> 0.737), verified against
# the real known_dishes list before picking the cutoff.
FUZZY_MATCH_THRESHOLD = 0.85

# Open Question 2: concrete score-adjustment sizes.
# Applied as a delta once a dish already has real feedback history.
RESPONSE_DELTA = {"yes": 1.0, "no": -1.0, "maybe": 0.0}
NOTE_DELTA_BONUS = {"positive": 0.5, "negative": -0.5, "neutral": 0.0}
# Open Question 5: the first real response a dish ever gets REPLACES its
# tag_weight_estimate outright (direct signal beats an inference) instead of
# nudging it. These are the baselines that first response sets, before any
# note bonus is added on top.
RESPONSE_BASELINE = {"yes": 4.0, "no": 1.5, "maybe": 2.5}

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

# Open Question 3: mess timing windows, used both as the default schedule
# slot and as the search range when resolving a conflict. Kept as simple
# constants since there is no separate MessTiming data file yet.
MESS_TIMING_WINDOWS = {
    "breakfast": (dt_time(8, 0), dt_time(9, 30)),
    "lunch": (dt_time(12, 30), dt_time(14, 30)),
    "dinner": (dt_time(19, 30), dt_time(21, 30)),
}
DEFAULT_MEAL_START = {"breakfast": dt_time(8, 0), "lunch": dt_time(13, 0), "dinner": dt_time(20, 0)}
MEAL_DURATION_MINUTES = 45
SLOT_SEARCH_STEP_MINUTES = 15

# Naive fallback vocabulary for tag inference and note-sentiment, used only
# when Gemini is unavailable or a call fails — keeps the whole loop runnable
# offline, matching skills.md's own "test before wiring up live calls" order.
NAIVE_TAG_KEYWORDS = {
    "paneer": ["veg", "jain"], "dal": ["dal"], "rice": ["rice"], "chawal": ["rice"],
    "gravy": ["gravy"], "curry": ["gravy"], "masala": ["gravy"], "sabzi": ["dry", "veg"],
    "chole": ["gravy", "veg"], "raita": ["curd-based"], "curd": ["curd-based"],
    "kheer": ["dessert", "sweet"], "halwa": ["sweet"], "ladoo": ["sweet"], "cake": ["sweet"],
    "pastry": ["dessert"], "malpua": ["sweet"], "chicken": ["non-vegetarian"],
    "mutton": ["non-vegetarian"], "fish": ["non-vegetarian"], "egg": ["non-vegetarian"],
    "risotto": ["fusion"], "fusion": ["fusion"], "manchurian": ["fusion", "spicy"],
}
NAIVE_POSITIVE_WORDS = {"good", "great", "loved", "love", "liked", "like", "yum", "yummy", "tasty", "amazing", "favorite"}
NAIVE_NEGATIVE_WORDS = {"bad", "hated", "hate", "disliked", "dislike", "bland", "spicy", "soggy", "cold", "gross", "too"}


class GoogleAuthUnavailable(Exception):
    pass


class MenuIntake(BaseModel):
    date: date
    source_image_id: str
    extracted_items: list[str]
    processed: bool = False


class KnownDish(BaseModel):
    name: str
    tags: list[str]
    score: float
    times_eaten: int = 0
    last_response: Optional[str] = None
    last_note: Optional[str] = None
    last_seen: date


class UserPreferences(BaseModel):
    user_id: str
    dietary_restrictions: list[str]
    skip_meal_slots: list[str]
    known_dishes: list[KnownDish]
    tag_weights: dict[str, float] = Field(default_factory=dict)


class ScoredDish(BaseModel):
    name: str
    tags: list[str]
    score: float
    source: str  # "known_dish" | "known_dish_fuzzy" | "tag_weight_estimate" | "no_data"
    decision: str  # "eat" | "skip"


class ScheduledMeal(BaseModel):
    date: date
    meal_slot: str
    selected_items: list[str]
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


def infer_tags_naive(name: str) -> list[str]:
    lowered = name.lower()
    tags: list[str] = []
    for keyword, keyword_tags in NAIVE_TAG_KEYWORDS.items():
        if keyword in lowered:
            for tag in keyword_tags:
                if tag not in tags:
                    tags.append(tag)
    return tags


def interpret_note_sentiment_naive(note: Optional[str]) -> str:
    if not note:
        return "neutral"
    words = set(re.findall(r"[a-z']+", note.lower()))
    if words & NAIVE_NEGATIVE_WORDS:
        return "negative"
    if words & NAIVE_POSITIVE_WORDS:
        return "positive"
    return "neutral"


def _load_google_credentials() -> Credentials:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")
    if not (client_id and client_secret and refresh_token):
        raise GoogleAuthUnavailable(
            "Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN "
            "in the environment; run authorize_google.py once before live Drive/Calendar calls will work."
        )
    return Credentials(
        token=None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=GOOGLE_SCOPES,
    )


def build_drive_service():
    return build("drive", "v3", credentials=_load_google_credentials(), cache_discovery=False)


def build_calendar_service():
    return build("calendar", "v3", credentials=_load_google_credentials(), cache_discovery=False)


class FilesystemTool:
    @staticmethod
    def read_json(path: Path) -> dict:
        return json.loads(path.read_text())

    @staticmethod
    def write_json(path: Path, payload: dict) -> None:
        path.write_text(json.dumps(payload, indent=2, default=str))


class GeminiSkill:
    """Wraps the Gemini API calls the skill relies on: reading a photographed
    menu image, guessing tags for dishes with no history, and interpreting a
    feedback note's sentiment. Every method degrades to a deterministic
    fallback (see NAIVE_* above) if no API key is configured or the call
    fails, so the rest of the agent never has to know which path ran."""

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

    def extract_dishes_from_image(self, image_bytes: bytes, mime_type: str) -> list[str]:
        from google.genai import types

        client = self._client_or_raise()
        prompt = (
            "This is a photo of a college mess/dining hall menu board. Read every dish "
            "name you can see and return ONLY a JSON array of strings, one per dish, "
            "exactly as written (fix obvious OCR artifacts like stray characters, but "
            "do not translate or rename dishes). No prose, no markdown fences."
        )
        response = client.models.generate_content(
            model=self.model,
            contents=[types.Part.from_bytes(data=image_bytes, mime_type=mime_type), prompt],
        )
        return _parse_json_list(response.text)

    def infer_tags(self, names: list[str]) -> dict[str, list[str]]:
        if not names:
            return {}
        prompt = (
            "For each dish name below, guess 1-4 short lowercase category tags "
            "(e.g. veg, non-vegetarian, gravy, dry, rice, dal, sweet, dessert, spicy, "
            "curd-based, fusion, jain) based only on the name. Return ONLY a JSON "
            f"object mapping each dish name to a list of tags. Dishes: {json.dumps(names)}"
        )
        # No internal try/except: a failure here (bad model name, network, quota) must
        # propagate to the caller so it's logged truthfully instead of silently
        # masquerading as a successful live call — see act_infer_tags.
        client = self._client_or_raise()
        response = client.models.generate_content(model=self.model, contents=[prompt])
        parsed = _parse_json_object(response.text)
        return {name: [str(t).lower() for t in parsed.get(name, [])] or infer_tags_naive(name) for name in names}

    def interpret_feedback_note(self, dish_name: str, response: str, note: Optional[str]) -> str:
        if not note:
            return "neutral"
        prompt = (
            f"A student was asked about eating '{dish_name}' at the mess, responded "
            f"'{response}', and added this note: \"{note}\". Classify the note's "
            "sentiment toward the dish as exactly one word: positive, negative, or neutral."
        )
        # No internal try/except — see infer_tags above; act_apply_feedback handles the fallback.
        client = self._client_or_raise()
        result = client.models.generate_content(model=self.model, contents=[prompt])
        word = result.text.strip().lower()
        if word not in {"positive", "negative", "neutral"}:
            raise ValueError(f"Gemini returned an unrecognized sentiment word: {word!r}")
        return word


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"```$", "", text)
    return text.strip()


def _parse_json_list(text: str) -> list[str]:
    parsed = json.loads(_strip_code_fence(text))
    return [str(item) for item in parsed]


def _parse_json_object(text: str) -> dict:
    return json.loads(_strip_code_fence(text))


class GoogleDriveMenuImageSkill:
    def __init__(self, folder_name: str = DRIVE_FOLDER_NAME):
        self.folder_name = folder_name

    def _find_folder_id(self, service) -> str:
        results = (
            service.files()
            .list(
                q=f"name='{self.folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
                fields="files(id,name)",
            )
            .execute()
        )
        folders = results.get("files", [])
        if not folders:
            raise FileNotFoundError(f"No Drive folder named '{self.folder_name}' found")
        return folders[0]["id"]

    def fetch_latest_menu_image(self) -> tuple[str, bytes, str]:
        service = build_drive_service()
        folder_id = self._find_folder_id(service)
        results = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed=false and mimeType contains 'image/'",
                fields="files(id,name,mimeType,modifiedTime)",
                orderBy="modifiedTime desc",
                pageSize=1,
            )
            .execute()
        )
        files = results.get("files", [])
        if not files:
            raise FileNotFoundError(f"No image file found in Drive folder '{self.folder_name}'")
        file_id, mime_type = files[0]["id"], files[0]["mimeType"]

        buffer = io.BytesIO()
        downloader = MediaIoBaseDownload(buffer, service.files().get_media(fileId=file_id))
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buffer.seek(0)
        return file_id, buffer.read(), mime_type


class GoogleCalendarSkill:
    def __init__(self, calendar_id: str = "primary"):
        self.calendar_id = calendar_id

    def list_events_in_window(self, event_date: date, window: tuple[dt_time, dt_time]) -> list[dict]:
        service = build_calendar_service()
        start_dt = datetime.combine(event_date, window[0])
        end_dt = datetime.combine(event_date, window[1])
        result = (
            service.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=start_dt.isoformat() + "+05:30",
                timeMax=end_dt.isoformat() + "+05:30",
                singleEvents=True,
            )
            .execute()
        )
        return result.get("items", [])

    def create_meal_event(self, meal_slot: str, event_date: date, start_time: dt_time, description: str, flagged: bool = False) -> str:
        service = build_calendar_service()
        start_dt = datetime.combine(event_date, start_time)
        end_dt = start_dt + timedelta(minutes=MEAL_DURATION_MINUTES)
        summary = f"Mess meal check: {meal_slot.title()}"
        if flagged:
            summary = f"[Conflict] {summary} — please rearrange"
        attendees = []
        student_email = os.getenv("STUDENT_EMAIL")
        if student_email:
            attendees.append({"email": student_email})
        body = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"},
        }
        if attendees:
            body["attendees"] = attendees
        created = service.events().insert(calendarId=self.calendar_id, body=body, sendUpdates="all" if attendees else "none").execute()
        return created["id"]

    def get_response(self, event_id: str) -> Optional[str]:
        service = build_calendar_service()
        event = service.events().get(calendarId=self.calendar_id, eventId=event_id).execute()
        for attendee in event.get("attendees", []):
            if attendee.get("self"):
                status = attendee.get("responseStatus")
                return {"accepted": "yes", "declined": "no", "tentative": "maybe"}.get(status)
        return "no" if event.get("status") == "cancelled" else None

    def delete_event(self, event_id: str) -> None:
        build_calendar_service().events().delete(calendarId=self.calendar_id, eventId=event_id).execute()


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
        tag_scores: dict[str, list[float]] = {}
        for dish in prefs.known_dishes:
            for tag in dish.tags:
                tag_scores.setdefault(tag, []).append(dish.score)
        return {tag: sum(scores) / len(scores) for tag, scores in tag_scores.items()}

    def is_slot_skipped(self, prefs: UserPreferences, meal_slot: str) -> bool:
        return meal_slot in prefs.skip_meal_slots

    def violates_dietary_restriction(self, name: str, tags: list[str], prefs: UserPreferences) -> bool:
        item_tags = {tag.lower() for tag in tags}
        item_name = name.lower()
        return any(
            restriction.lower() in item_tags or restriction.lower() in item_name
            for restriction in prefs.dietary_restrictions
        )

    def match_dish(self, name: str, tags: list[str], prefs: UserPreferences) -> ScoredDish:
        normalized = normalize_dish_name(name)
        for dish in prefs.known_dishes:
            if normalize_dish_name(dish.name) == normalized:
                decision = "eat" if dish.score >= EAT_THRESHOLD else "skip"
                return ScoredDish(name=name, tags=tags, score=dish.score, source="known_dish", decision=decision)

        fuzzy = find_fuzzy_match(name, prefs.known_dishes)
        if fuzzy is not None:
            decision = "eat" if fuzzy.score >= EAT_THRESHOLD else "skip"
            return ScoredDish(name=fuzzy.name, tags=fuzzy.tags, score=fuzzy.score, source="known_dish_fuzzy", decision=decision)

        recognized = [prefs.tag_weights[tag] for tag in tags if tag in prefs.tag_weights]
        if not recognized:
            return ScoredDish(name=name, tags=tags, score=0.0, source="no_data", decision="skip")
        estimate = sum(recognized) / len(recognized)
        decision = "eat" if estimate >= EAT_THRESHOLD else "skip"
        return ScoredDish(name=name, tags=tags, score=round(estimate, 2), source="tag_weight_estimate", decision=decision)

    def register_new_dishes(self, prefs: UserPreferences, scored: list[ScoredDish], event_date: date) -> UserPreferences:
        existing = {normalize_dish_name(dish.name) for dish in prefs.known_dishes}
        for result in scored:
            if normalize_dish_name(result.name) in existing or result.source not in ("tag_weight_estimate", "no_data"):
                continue
            prefs.known_dishes.append(
                KnownDish(name=result.name, tags=result.tags, score=result.score, times_eaten=0, last_seen=event_date)
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
                dish.score = RESPONSE_BASELINE[response] + bonus
            else:
                dish.score += RESPONSE_DELTA[response] + NOTE_DELTA_BONUS.get(note_sentiment, 0.0)
            dish.score = max(SCORE_MIN, min(SCORE_MAX, round(dish.score, 2)))
            dish.times_eaten += 1
            dish.last_response = response
            dish.last_note = note
            dish.last_seen = event_date
            break
        return prefs

    def build_event_description(self, scored: list[ScoredDish], conflict_flag: bool = False) -> str:
        known_favorites = [s for s in scored if s.source in ("known_dish", "known_dish_fuzzy") and s.decision == "eat"]
        new_estimated = [s for s in scored if s.source == "tag_weight_estimate" and s.decision == "eat"]
        no_data = [s for s in scored if s.source == "no_data"]
        skipped = [s for s in scored if s.decision == "skip" and s.source != "no_data"]
        lines = []
        if conflict_flag:
            lines.append("SCHEDULING CONFLICT — no free slot found in this meal's window; please rearrange manually.")
        if known_favorites:
            lines.append("Known favorites: " + ", ".join(f"{s.name} ({s.score:.1f})" for s in known_favorites))
        if new_estimated:
            lines.append("New, estimated matches: " + ", ".join(f"{s.name} (~{s.score:.1f})" for s in new_estimated))
        if no_data:
            lines.append("New dish, no data yet: " + ", ".join(s.name for s in no_data))
        if skipped:
            lines.append("Not worth it today: " + ", ".join(s.name for s in skipped))
        if not lines:
            lines.append("No menu items available to evaluate.")
        return "\n".join(lines)


class ReActMealAgent:
    def __init__(
        self,
        user_id: str,
        drive_skill: GoogleDriveMenuImageSkill,
        calendar_skill: GoogleCalendarSkill,
        gemini_skill: GeminiSkill,
        matcher: MenuPreferenceMatchingSkill,
        fs: FilesystemTool,
        live: bool = False,
    ):
        self.user_id = user_id
        self.drive = drive_skill
        self.calendar = calendar_skill
        self.gemini = gemini_skill
        self.matcher = matcher
        self.fs = fs
        self.live = live
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

    def _preferences_path(self) -> Path:
        return DATA_DIR / f"preferences_{self.user_id}.json"

    def _menu_intake_path(self, event_date: date) -> Path:
        return DATA_DIR / f"menu_intake_{event_date.isoformat()}.json"

    def perceive_preferences(self) -> UserPreferences:
        path = self._preferences_path()
        prefs = UserPreferences(**self.fs.read_json(path))
        self._log(
            "I need the student's stored preferences before judging any dish.",
            "filesystem.read_json",
            {"path": str(path)},
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

    def act_menu_intake(self, event_date: date) -> MenuIntake:
        if self.live:
            try:
                file_id, image_bytes, mime_type = self.drive.fetch_latest_menu_image()
                items = self.gemini.extract_dishes_from_image(image_bytes, mime_type)
                intake = MenuIntake(date=event_date, source_image_id=file_id, extracted_items=items)
                self._log(
                    "Gemini reads the photographed menu image — SKILL.md's real intake source.",
                    "gemini.extract_dishes_from_image",
                    {"folder": self.drive.folder_name},
                    f"Extracted {len(items)} item(s): {items}",
                )
                self.fs.write_json(self._menu_intake_path(event_date), json.loads(intake.model_dump_json()))
                return intake
            except Exception as exc:
                # Broad on purpose: Drive auth, a missing image, a network hiccup, or Gemini
                # returning malformed JSON should all fall back to the fixture, never crash the run.
                self._log(
                    "Gemini reads the photographed menu image — SKILL.md's real intake source.",
                    "gemini.extract_dishes_from_image",
                    {"folder": self.drive.folder_name},
                    f"Live intake failed ({exc}); falling back to the local sample menu, "
                    "matching SKILL.md's test-before-connecting build order.",
                )

        path = DATA_DIR / "menu_intake_sample.json"
        intake = MenuIntake(**self.fs.read_json(path))
        intake.date = event_date
        self._log(
            "Local fixture stands in for a real photographed menu until Drive has one.",
            "filesystem.read_json",
            {"path": str(path)},
            f"Loaded {len(intake.extracted_items)} local item(s): {intake.extracted_items}",
        )
        self.fs.write_json(self._menu_intake_path(event_date), json.loads(intake.model_dump_json()))
        self._log(
            "Extracted data is stored before any matching happens, so it survives a bad later step.",
            "filesystem.write_json",
            {"path": str(self._menu_intake_path(event_date))},
            "MenuIntake record persisted (processed=False).",
        )
        return intake

    def act_infer_tags(self, names: list[str]) -> dict[str, list[str]]:
        source = "naive fallback (offline or no GEMINI_API_KEY)"
        tags_by_name = None
        if self.live and self.gemini.available:
            try:
                tags_by_name = self.gemini.infer_tags(names)
                source = "gemini.infer_tags (live)"
            except Exception as exc:
                source = f"naive fallback (live call failed: {exc})"
        if tags_by_name is None:
            tags_by_name = {name: infer_tags_naive(name) for name in names}
        self._log(
            "A dish with no tags has nothing for tag_weights to average, so tags are guessed "
            "from the name before matching — the raw MenuIntake record stays untouched.",
            "gemini.infer_tags",
            {"names": names},
            f"[{source}] {tags_by_name}",
        )
        return tags_by_name

    def act_filter_and_score(self, prefs: UserPreferences, intake: MenuIntake, meal_slot: str, tags_by_name: dict[str, list[str]]) -> list[ScoredDish]:
        scored = []
        for name in intake.extracted_items:
            tags = tags_by_name.get(name, [])
            if self.matcher.violates_dietary_restriction(name, tags, prefs):
                self._log(
                    f"Hard-exclude check for '{name}' runs before any scoring.",
                    "matcher.violates_dietary_restriction",
                    {"item": name, "tags": tags},
                    f"'{name}' excluded — violates dietary_restrictions={prefs.dietary_restrictions}.",
                )
                continue
            result = self.matcher.match_dish(name, tags, prefs)
            self._log(
                f"'{name}' — exact match, then fuzzy match, then tag_weight fallback, in that order.",
                "matcher.match_dish",
                {"item": name, "tags": tags},
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
        window = MESS_TIMING_WINDOWS[meal_slot]
        default_start = DEFAULT_MEAL_START[meal_slot]
        selected = [s.name for s in scored if s.decision == "eat"]

        if not self.live:
            description = self.matcher.build_event_description(scored)
            self._log(
                "Dry run — computing the schedule without touching live Calendar.",
                "workflow.dry_run_schedule",
                {"meal_slot": meal_slot, "window": [str(window[0]), str(window[1])]},
                f"Would create event at {default_start} with: {description.replace(chr(10), ' | ')}",
            )
            return ScheduledMeal(date=event_date, meal_slot=meal_slot, selected_items=selected, scheduled_time=default_start, conflict_resolved=True)

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
            return ScheduledMeal(date=event_date, meal_slot=meal_slot, selected_items=selected, scheduled_time=None, conflict_resolved=False)

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

        description = self.matcher.build_event_description(scored, conflict_flag=flagged)
        event_id = self.act_create_event(meal_slot, event_date, scheduled_time, description, flagged)
        return ScheduledMeal(
            date=event_date, meal_slot=meal_slot, selected_items=selected,
            calendar_event_id=event_id, scheduled_time=scheduled_time, conflict_resolved=conflict_resolved,
        )

    def act_create_event(self, meal_slot: str, event_date: date, start_time: dt_time, description: str, flagged: bool) -> Optional[str]:
        action_input = {"meal_slot": meal_slot, "event_date": str(event_date), "start_time": str(start_time)}
        try:
            event_id = self.calendar.create_meal_event(meal_slot, event_date, start_time, description, flagged)
            self._log(
                "Push the result into Calendar as the actual interface, per SKILL.md — no separate custom screen.",
                "google_calendar.create_meal_event",
                action_input,
                f"Created calendar event {event_id}.",
            )
            return event_id
        except (GoogleAuthUnavailable, HttpError) as exc:
            self._log(
                "Push the result into Calendar as the actual interface, per SKILL.md — no separate custom screen.",
                "google_calendar.create_meal_event",
                action_input,
                f"Calendar unavailable ({exc}); recommendation is computed but not pushed to a live calendar this run.",
            )
            return None

    def act_collect_response(self, event_id: Optional[str], simulated_response: Optional[str], simulated_note: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        if simulated_response is not None:
            self._log(
                "A concrete RSVP was supplied for this run instead of polling Calendar — "
                "useful for exercising the feedback path without waiting on a live invite.",
                "cli.simulated_response",
                {"response": simulated_response, "note": simulated_note},
                f"Treating RSVP as '{simulated_response}'" + (f" with note '{simulated_note}'." if simulated_note else "."),
            )
            return simulated_response, simulated_note

        if event_id is None or not self.live:
            self._log("No live event exists to poll.", "google_calendar.get_response", {"event_id": event_id}, "Skipped — nothing to check.")
            return None, None
        try:
            response = self.calendar.get_response(event_id)
        except (GoogleAuthUnavailable, HttpError) as exc:
            self._log("Ask Calendar whether the student accepted, declined, or was tentative.", "google_calendar.get_response", {"event_id": event_id}, f"Could not read RSVP ({exc}); treating as pending.")
            return None, None
        self._log("Ask Calendar whether the student accepted, declined, or was tentative.", "google_calendar.get_response", {"event_id": event_id}, f"RSVP status: {response or 'pending'}.")
        return response, None

    def act_apply_feedback(self, prefs: UserPreferences, scored: list[ScoredDish], response: Optional[str], note: Optional[str], event_date: date) -> UserPreferences:
        if response is None:
            return prefs
        eaten = [s for s in scored if s.decision == "eat"] or scored
        for dish in eaten:
            sentiment = None
            if self.live and self.gemini.available:
                try:
                    sentiment = self.gemini.interpret_feedback_note(dish.name, response, note)
                except Exception:
                    sentiment = None
            if sentiment is None:
                sentiment = interpret_note_sentiment_naive(note)
            prefs = self.matcher.apply_feedback(prefs, dish.name, response, note, sentiment, event_date)
            self._log(
                f"'{response.upper()}' for '{dish.name}' — first real feedback replaces an estimate "
                "outright; later feedback nudges the running score by a fixed delta.",
                "matcher.apply_feedback",
                {"dish": dish.name, "response": response, "note": note, "note_sentiment": sentiment},
                "known_dishes updated for this specific item.",
            )
        return prefs

    def act_persist_preferences(self, prefs: UserPreferences) -> None:
        path = self._preferences_path()
        payload = json.loads(prefs.model_dump_json())
        payload["tag_weights"] = {}
        self.fs.write_json(path, payload)
        self._log(
            "known_dishes may have changed this run; tag_weights is never persisted since it is "
            "derived fresh every run.",
            "filesystem.write_json",
            {"path": str(path)},
            f"Wrote {len(prefs.known_dishes)} known dish(es) back to disk.",
        )

    def act_mark_intake_processed(self, event_date: date) -> None:
        path = self._menu_intake_path(event_date)
        record = self.fs.read_json(path)
        record["processed"] = True
        self.fs.write_json(path, record)

    def run(self, meal_slot: str, event_date: date, simulated_response: Optional[str] = None, simulated_note: Optional[str] = None) -> tuple[ScheduledMeal, Optional[str]]:
        prefs = self.perceive_preferences()

        if self.reason_skip_check(prefs, meal_slot):
            self._log("Skip rule matched — halt before any Drive/Gemini/Calendar calls.", "workflow.halt", {"meal_slot": meal_slot}, "No event created; this meal slot is excluded by user preference.")
            return ScheduledMeal(date=event_date, meal_slot=meal_slot, selected_items=[]), None

        intake = self.act_menu_intake(event_date)
        tags_by_name = self.act_infer_tags(intake.extracted_items)
        scored = self.act_filter_and_score(prefs, intake, meal_slot, tags_by_name)
        prefs = self.act_register_new_dishes(prefs, scored, event_date)
        scheduled = self.act_schedule(meal_slot, event_date, scored)

        response, note = self.act_collect_response(scheduled.calendar_event_id, simulated_response, simulated_note)
        prefs = self.act_apply_feedback(prefs, scored, response, note, event_date)

        self.act_persist_preferences(prefs)
        self.act_mark_intake_processed(event_date)
        return scheduled, response


def main() -> None:
    load_dotenv(BASE_DIR.parent / ".env")

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    live = "--live" in sys.argv[1:]

    meal_slot = args[0] if len(args) > 0 else "lunch"
    event_date = date.fromisoformat(args[1]) if len(args) > 1 else date(2026, 9, 18)
    simulated_response = args[2] if len(args) > 2 else None
    simulated_note = args[3] if len(args) > 3 else None

    if not live:
        print("Running in DRY RUN mode (default) — no live Drive/Gemini/Calendar calls will be made.")
        print("Pass --live to hit the real APIs, e.g.: python3 react_agent.py lunch 2026-09-18 yes --live\n")

    agent = ReActMealAgent(
        user_id="u001",
        drive_skill=GoogleDriveMenuImageSkill(),
        calendar_skill=GoogleCalendarSkill(),
        gemini_skill=GeminiSkill(),
        matcher=MenuPreferenceMatchingSkill(),
        fs=FilesystemTool(),
        live=live,
    )

    scheduled, response = agent.run(meal_slot, event_date, simulated_response=simulated_response, simulated_note=simulated_note)

    print("\n=== Final ScheduledMeal ===")
    print(scheduled.model_dump_json(indent=2))
    print(f"response: {response}")


if __name__ == "__main__":
    main()
