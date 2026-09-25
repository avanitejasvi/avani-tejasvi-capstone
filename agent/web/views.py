"""Shapes Postgres rows into what the Forkcast screens display — scheduled
meals with their mess tags, and an uploaded menu grouped by day and meal.
Read-only: nothing here scores, schedules or writes anything.
"""
from datetime import date, datetime, timedelta
from typing import Optional

from agent.react_agent import MEAL_DURATION_MINUTES, FilesystemTool, load_mess_timings, normalize_dish_name
from agent.repository import Repository
from agent.timezone import IST, now_ist

SLOT_ORDER = ["breakfast", "lunch", "evening_snacks", "dinner", "sunday_brunch"]
SLOT_LABELS = {
    "breakfast": "Breakfast", "lunch": "Lunch", "evening_snacks": "Evening snacks",
    "dinner": "Dinner", "sunday_brunch": "Sunday brunch",
}
# Placeholders Gemini sometimes reads off an empty board cell.
JUNK_NAMES = {"", "n/a", "na", "-", "--", "—", "none", "nil", "tbd", "tba"}


def is_real_dish(name: str) -> bool:
    return normalize_dish_name(name or "").strip(" .") not in JUNK_NAMES


WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def slot_label(slot: str) -> str:
    return SLOT_LABELS.get(slot, slot.replace("_", " ").capitalize())


def short_date(d: date) -> str:
    return f"{d.strftime('%a')}, {d.day} {d.strftime('%b')}"


def long_date(d: date) -> str:
    return f"{d.strftime('%A')}, {d.day} {d.strftime('%b')}"


def week_label(week_start: date) -> str:
    return f"Week of {week_start.day} {week_start.strftime('%b')}"


def _merge_mess(current: Optional[str], new: Optional[str]) -> Optional[str]:
    """The same dish served at both messes — separately, or tagged "Both" by
    Gemini for a same_for_both_messes meal — shows one "Both" tag."""
    if not current:
        return new
    if not new or current == new:
        return current
    return "Both"


def _items_for(items, weekday: str, slot: Optional[str] = None):
    return [
        item for item in items
        if (item.day is None or item.day.lower() == weekday) and (slot is None or item.meal_slot == slot)
    ]


def group_menu(items, week_start: date) -> list[dict]:
    """[{date, label, count, slots: [{label, dishes: [{name, mess}]}]}] for
    each day of the week that has any dishes. Duplicate names within a meal
    (e.g. the same sabzi listed under "Dry Veg" and "Dry Veg - Jain")
    collapse into one row."""
    days = []
    for offset, weekday in enumerate(WEEKDAYS):
        d = week_start + timedelta(days=offset)
        slots = []
        for slot in SLOT_ORDER + sorted({i.meal_slot for i in items} - set(SLOT_ORDER)):
            dishes: dict = {}
            for item in _items_for(items, weekday, slot):
                if not is_real_dish(item.name):
                    continue
                key = normalize_dish_name(item.name)
                if key in dishes:
                    dishes[key]["mess"] = _merge_mess(dishes[key]["mess"], item.mess)
                else:
                    dishes[key] = {"name": item.name, "mess": item.mess}
            if dishes:
                slots.append({"slot": slot, "label": slot_label(slot), "dishes": list(dishes.values())})
        if slots:
            days.append({
                "date": d, "label": long_date(d),
                "count": sum(len(s["dishes"]) for s in slots), "slots": slots,
            })
    return days


def _mess_lookup(repo: Repository, user_id, event_date: date, slot: str, cache: dict) -> dict:
    # One page lists up to ~30 meals from the same few menu rows — load
    # each (large) row once per render, not once per meal.
    if event_date not in cache:
        cache[event_date] = repo.get_menu_intake(event_date, user_id)
    intake = cache[event_date]
    if intake is None:
        return {}
    lookup: dict = {}
    for item in _items_for(intake.extracted_items, WEEKDAYS[event_date.weekday()], slot):
        key = normalize_dish_name(item.name)
        lookup[key] = _merge_mess(lookup.get(key), item.mess)
    return lookup


def meal_view(repo: Repository, row, timings, now: datetime, cache: Optional[dict] = None) -> dict:
    window = timings.get(row.meal_slot)
    start = row.scheduled_time or (window[0] if window else None)
    if start is not None:
        start_dt = datetime.combine(row.event_date, start, tzinfo=IST)
        end_dt = start_dt + timedelta(minutes=MEAL_DURATION_MINUTES)
        time_label = f"{start_dt:%H:%M}–{end_dt:%H:%M}"
    else:
        end_dt = datetime.combine(row.event_date + timedelta(days=1), datetime.min.time(), tzinfo=IST)
        time_label = ""
    lookup = _mess_lookup(repo, row.user_id, row.event_date, row.meal_slot, cache if cache is not None else {})
    top_picks = list(row.top_picks or [])
    return {
        "id": str(row.id),
        "date": row.event_date,
        "slot": row.meal_slot,
        "slot_label": slot_label(row.meal_slot),
        "day_label": short_date(row.event_date),
        "day_short": row.event_date.strftime("%a"),
        "time_label": time_label,
        # A fuzzy-matched pick is named after the known dish, not the menu's
        # spelling, so its mess can be unknown — shown without a tag then.
        "picks": [{"name": name, "mess": lookup.get(normalize_dish_name(name))} for name in top_picks],
        # Backup (fallback-tier) picks are never "eat" decisions, so they're
        # never in selected_items — same rule the Calendar description uses.
        "backup": bool(top_picks) and not set(top_picks) <= set(row.selected_items or []),
        "past": end_dt <= now,
        "rated": bool(row.feedback_applied),
    }


def week_meals(repo: Repository, user_id, week_start: date) -> list[dict]:
    timings = load_mess_timings(FilesystemTool())
    now = now_ist()
    dates = [week_start + timedelta(days=i) for i in range(7)]
    rows = [r for r in repo.list_scheduled_meals_for_user(user_id, dates) if r.calendar_event_id]
    cache: dict = {}
    meals = [meal_view(repo, r, timings, now, cache) for r in rows]
    meals.sort(key=lambda m: (m["date"], SLOT_ORDER.index(m["slot"]) if m["slot"] in SLOT_ORDER else 99))
    return meals


def meals_to_rate(repo: Repository, user_id, today: date) -> list[dict]:
    """Past meals still waiting for feedback — the same rows the old
    /feedback page listed, minus ones that haven't actually happened yet."""
    timings = load_mess_timings(FilesystemTool())
    now = now_ist()
    rows = [r for r in repo.list_pending_manual_feedback(user_id, today) if r.top_picks]
    cache: dict = {}
    meals = [meal_view(repo, r, timings, now, cache) for r in rows]
    return [m for m in meals if m["past"]]


def missing_days_label(missing: list[date]) -> str:
    """"Thu–Sun" for a contiguous run, else "Thu, Sat"."""
    if not missing:
        return ""
    names = [d.strftime("%a") for d in missing]
    contiguous = all((b - a).days == 1 for a, b in zip(missing, missing[1:]))
    if contiguous and len(missing) > 2:
        return f"{names[0]}–{names[-1]}"
    return ", ".join(names)
