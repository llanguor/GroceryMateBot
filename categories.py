from settings import settings, read


# categories.json is read on every call, so the order, names and emoji can be edited without restarting the bot.
# The last category is the fallback: items whose category is not in the file any more end up there.
def load_categories():
    data = read(settings["categories_file"])
    if not isinstance(data, list) or not data:
        raise ValueError(f"{settings['categories_file']} must be a non-empty list of categories")
    categories = []
    for entry in data:
        name = str(entry.get("name", "")).strip() if isinstance(entry, dict) else ""
        if not name:
            raise ValueError(f"Every category in {settings['categories_file']} needs a non-empty \"name\"")
        categories.append({"name": name, "emoji": str(entry.get("emoji") or "").strip()})
    if len({category["name"].casefold() for category in categories}) != len(categories):
        raise ValueError(f"Category names in {settings['categories_file']} must be unique")
    return categories


def category_index(categories, name):
    wanted = str(name or "").strip().casefold()
    for index, category in enumerate(categories):
        if category["name"].casefold() == wanted:
            return index
    return len(categories) - 1
