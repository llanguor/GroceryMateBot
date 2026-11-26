import json
import time
from google import genai
from google.genai import types
from settings import settings
from categories import category_index
from logger import get_logger

logger = get_logger()
_client = None


class AiError(Exception):
    pass


def get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings["gemini_api_key"], http_options=types.HttpOptions(timeout=int(settings["gemini_timeout"] * 1000)))
    return _client


def build_prompt(query, categories):
    names = ", ".join(category["name"] for category in categories)
    return "\n".join(settings["ai_prompt"]).replace("{categories}", names).replace("{query}", query)


def strip_fences(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


# Turns the answer of the model into clean items: {"name", "emoji", "category"}. The category is always one of the
# configured ones, the emoji is never empty.
def normalize_items(raw, categories):
    try:
        data = json.loads(strip_fences(raw))
    except (TypeError, ValueError) as e:
        raise AiError(f"The model returned invalid JSON: {e}") from e
    if isinstance(data, dict):
        data = next((value for value in data.values() if isinstance(value, list)), None)
    if not isinstance(data, list):
        raise AiError("The model returned JSON that is not a list")
    items = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()[:settings["max_item_length"]]
        if not name:
            continue
        emoji = str(entry.get("emoji") or "").strip()
        if not emoji or len(emoji) > 8:
            emoji = settings["default_item_emoji"]
        category = categories[category_index(categories, entry.get("category"))]["name"]
        items.append({"name": name, "emoji": emoji, "category": category})
    return items


def parse_shopping_list(query, categories):
    started = time.perf_counter()
    try:
        response = get_client().models.generate_content(
            model=settings["gemini_model"],
            contents=build_prompt(query, categories),
            config=types.GenerateContentConfig(response_mime_type="application/json"))
    except Exception as e:
        raise AiError(f"The request to the model failed: {e}") from e
    logger.info(f"[{settings['gemini_model']}] {time.perf_counter() - started:.2f} s")
    return normalize_items(response.text, categories)
