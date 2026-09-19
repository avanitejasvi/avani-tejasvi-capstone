"""Feedback-note sentiment classification, across whichever provider a
student has supplied their own API key for.

Deliberately narrow in scope: only /feedback's sentiment call supports
bring-your-own-key. Menu-photo extraction (agent/react_agent.py's
GeminiSkill.extract_dishes_from_image) always stays on the shared
GEMINI_API_KEY — it's a once-a-week shared action, not a personal one, and
needs a vision-capable model with the app's own mess_structure.json prompt,
not a plain text classification task like this one is.
"""
SUPPORTED_PROVIDERS = {"gemini", "anthropic", "openai", "groq"}

_DEFAULT_MODEL = {
    "gemini": "gemini-3.5-flash-lite",
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
}


def _prompt(dish_name: str, response: str, note: str) -> str:
    return (
        f"A student was asked about eating '{dish_name}' at the mess, responded "
        f"'{response}', and added this note: \"{note}\". Classify the note's "
        "sentiment toward the dish as exactly one word: positive, negative, or neutral."
    )


def _parse_sentiment(word: str) -> str:
    parsed = word.strip().lower()
    if parsed not in {"positive", "negative", "neutral"}:
        raise ValueError(f"unrecognized sentiment word: {word!r}")
    return parsed


def classify_note_sentiment(provider: str, api_key: str, dish_name: str, response: str, note: str) -> str:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"unsupported provider: {provider!r} (supported: {sorted(SUPPORTED_PROVIDERS)})")
    prompt = _prompt(dish_name, response, note)
    model = _DEFAULT_MODEL[provider]

    if provider == "gemini":
        from google import genai

        client = genai.Client(api_key=api_key)
        result = client.models.generate_content(model=model, contents=[prompt])
        return _parse_sentiment(result.text)

    if provider == "anthropic":
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
        result = client.messages.create(model=model, max_tokens=8, messages=[{"role": "user", "content": prompt}])
        return _parse_sentiment(result.content[0].text)

    # OpenAI and Groq share the same chat-completions request shape — Groq's
    # API is OpenAI-compatible, so the `openai` SDK works for both, just
    # pointed at a different base_url.
    import openai

    base_url = "https://api.groq.com/openai/v1" if provider == "groq" else None
    client = openai.OpenAI(api_key=api_key, base_url=base_url)
    result = client.chat.completions.create(model=model, max_tokens=8, messages=[{"role": "user", "content": prompt}])
    return _parse_sentiment(result.choices[0].message.content)
