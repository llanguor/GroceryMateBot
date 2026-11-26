import uuid
from json_io import load_data, update_data
from settings import settings

LISTS_FILE = settings["lists_file"]


def make_item(entry):
    return {"id": uuid.uuid4().hex[:6], "name": entry["name"], "emoji": entry["emoji"], "category": entry["category"], "done": False}


# A product that is already in the list is not duplicated; if it was ticked off, it becomes active again.
def merge_items(shopping_list, entries):
    known = {item["name"].casefold(): item for item in shopping_list["items"]}
    for entry in entries:
        item = known.get(entry["name"].casefold())
        if item is None:
            item = make_item(entry)
            shopping_list["items"].append(item)
            known[entry["name"].casefold()] = item
        item["done"] = False


# mode "today" - add to the list of the given day, "last" - to the newest list whatever its date, "list" - to the
# list list_id, "new" - always start a new list, even if there already is a list of that day.
# A new list is also created when there is nothing to add to. Returns the list and the id of its previous message.
def add_to_list(scope, chat_id, thread_id, entries, day, mode, list_id=None):
    def updater(data):
        lists = data.setdefault(str(scope), [])
        if mode == "new":
            target = None
        elif mode == "list":
            target = next((item for item in lists if item["id"] == list_id), None)
        elif mode == "last":
            target = lists[-1] if lists else None
        else:
            target = next((item for item in reversed(lists) if item["date"] == day), None)
        if target is None:
            target = {"id": uuid.uuid4().hex[:8], "date": day, "chat_id": chat_id, "thread_id": thread_id, "message_id": None, "items": []}
            lists.append(target)
            del lists[:-settings["max_lists"]]
        previous_message_id = target["message_id"]
        merge_items(target, entries)
        return target, previous_message_id

    return update_data(LISTS_FILE, updater)


def get_lists(scope):
    return load_data(LISTS_FILE).get(str(scope), [])


def get_list(scope, list_id):
    return next((item for item in get_lists(scope) if item["id"] == list_id), None)


def set_message_id(scope, list_id, message_id):
    def updater(data):
        for item in data.get(str(scope), []):
            if item["id"] == list_id:
                item["message_id"] = message_id

    update_data(LISTS_FILE, updater)


def set_done(scope, list_id, item_id):
    def updater(data):
        shopping_list = next((item for item in data.get(str(scope), []) if item["id"] == list_id), None)
        if shopping_list is None:
            return None
        for item in shopping_list["items"]:
            if item["id"] == item_id:
                item["done"] = True
        return shopping_list

    return update_data(LISTS_FILE, updater)


def reset_done(scope, list_id):
    def updater(data):
        shopping_list = next((item for item in data.get(str(scope), []) if item["id"] == list_id), None)
        if shopping_list is None:
            return None
        for item in shopping_list["items"]:
            item["done"] = False
        return shopping_list

    return update_data(LISTS_FILE, updater)


def delete_list(scope, list_id):
    def updater(data):
        lists = data.get(str(scope), [])
        for item in lists:
            if item["id"] == list_id:
                lists.remove(item)
                return item
        return None

    return update_data(LISTS_FILE, updater)
