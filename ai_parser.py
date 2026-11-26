import json
import re
import time
from google import genai
from google.genai import types
from settings import settings
from categories import category_index
from logger import get_logger

logger = get_logger()
_client = None


class AiError(Exception):
    def __init__(self, message, retryable=True):
        super().__init__(message)
        self.retryable = retryable


# Errors of the request itself (a wrong key, a bad request) will not go away by repeating, unlike "the model is
# unavailable" and the like.
def request_failed(e):
    code = getattr(e, "code", None)
    retryable = not (isinstance(code, int) and 400 <= code < 500 and code not in (408, 429))
    return AiError(f"The request to the model failed: {e}", retryable)


# Repeats the whole attempt up to "ai_retries" more times when it fails.
def with_retries(attempt):
    attempts = 1 + max(0, int(settings["ai_retries"]))
    for number in range(1, attempts + 1):
        try:
            return attempt()
        except AiError as e:
            if not e.retryable or number == attempts:
                raise
            logger.warning(f"Attempt {number} of {attempts} failed: {e}. Trying again")
            time.sleep(settings["ai_retry_delay"])


def get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings["gemini_api_key"], http_options=types.HttpOptions(timeout=int(settings["gemini_timeout"] * 1000)))
    return _client


def build_prompt(template, query, categories):
    names = ", ".join(category["name"] for category in categories)
    return "\n".join(settings[template]).replace("{categories}", names).replace("{query}", query)


def strip_fences(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text.strip()


# The model sometimes wraps the JSON into a text when it has tools enabled, so the array is cut out of the answer.
def extract_json(raw):
    text = strip_fences(raw)
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        pass
    start, end = text.find("["), text.rfind("]")
    try:
        if start == -1 or end < start:
            raise ValueError("no JSON array in the answer")
        return json.loads(text[start:end + 1])
    except ValueError as e:
        raise AiError(f"The model returned invalid JSON: {e}") from e


# Turns the answer of the model into clean items: {"name", "emoji", "category"}. The category is always one of the
# configured ones, the emoji is never empty.
def normalize_items(raw, categories):
    data = extract_json(raw)
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


def ask_model(prompt, categories, url_context=False, timeout=None):
    config = {}
    if url_context:
        # Pages are opened by the model itself. The JSON mode cannot be combined with tools, so it is left out.
        config["tools"] = [types.Tool(url_context=types.UrlContext())]
    else:
        config["response_mime_type"] = "application/json"
    if timeout:
        config["http_options"] = types.HttpOptions(timeout=int(timeout * 1000))

    def attempt():
        started = time.perf_counter()
        try:
            response = get_client().models.generate_content(model=settings["gemini_model"], contents=prompt, config=types.GenerateContentConfig(**config))
        except Exception as e:
            raise request_failed(e) from e
        logger.info(f"[{settings['gemini_model']}] {time.perf_counter() - started:.2f} s")
        return normalize_items(response.text, categories)

    return with_retries(attempt)


# Splits the message of the user into products.
def parse_shopping_list(query, categories):
    return ask_model(build_prompt("ai_prompt", query, categories), categories)


# Makes up products for a free request: a recipe, a link to a page with a recipe, an idea, an event...
def generate_products(query, categories):
    has_url = re.search(r"https?://\S+", query) is not None
    return ask_model(build_prompt("generate_prompt", query, categories), categories, url_context=has_url, timeout=settings["generate_timeout"])


# Speech to text. Returns an empty string if there is no speech in the audio.
def transcribe_audio(data, mime_type):
    def attempt():
        started = time.perf_counter()
        try:
            response = get_client().models.generate_content(
                model=settings["gemini_model"],
                contents=[types.Part.from_bytes(data=data, mime_type=mime_type), settings["transcribe_prompt"]],
                config=types.GenerateContentConfig(http_options=types.HttpOptions(timeout=int(settings["generate_timeout"] * 1000))))
        except Exception as e:
            raise request_failed(e) from e
        logger.info(f"[{settings['gemini_model']}] transcription {time.perf_counter() - started:.2f} s")
        return (response.text or "").strip()

    return with_retries(attempt)
