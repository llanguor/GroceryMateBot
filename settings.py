import json
import os

BASE_FILE = "appsettings.json"
LOCAL_FILE = "appsettings.local.json"
ENVIRONMENT_VARIABLE = "APP_ENVIRONMENT"


def read(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value


def load_settings():
    result = read(BASE_FILE)
    overrides = []
    environment = os.environ.get(ENVIRONMENT_VARIABLE)
    if environment:
        overrides.append(f"appsettings.{environment}.json")
    overrides.append(LOCAL_FILE)
    for path in overrides:
        if os.path.exists(path):
            merge(result, read(path))
    return result


settings = load_settings()
