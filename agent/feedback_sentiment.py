"""Shared sentiment-classification path for a feedback note — used by the
manual /feedback page, the automated weekly collect_feedback.py job, and
anything else that turns a real response+note into a sentiment word, so a
student's own bring-your-own key (agent/llm_providers.py) is honored the
same way no matter which channel the feedback came through.
"""
from typing import Optional

from agent.llm_providers import classify_note_sentiment
from agent.react_agent import GeminiSkill
from agent.repository import Repository


def classify_sentiment(repo: Repository, user_id, dish_name: str, response: str, note: Optional[str]) -> str:
    if not note:
        return "neutral"
    own_key = repo.get_llm_key(user_id)  # (provider, api_key), or None -> fall back to the shared Gemini key
    try:
        if own_key is not None:
            provider, api_key = own_key
            return classify_note_sentiment(provider, api_key, dish_name, response, note)
        gemini = GeminiSkill()
        if gemini.available:
            return gemini.interpret_feedback_note(dish_name, response, note)
    except Exception:
        pass
    return "neutral"
