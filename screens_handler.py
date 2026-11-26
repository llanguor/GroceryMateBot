from json_io import load_data, update_data
from settings import settings

SCREENS_FILE = settings["screens_file"]
MAX_SCREENS = 50


# Screens are the messages of the bot that are alive in a chat: the list messages and the start menu. They are
# remembered per scope so that they can be cleaned up. "empty" are the start menus that say there are no lists yet.
def entry_of(data, scope):
    entry = data.get(str(scope))
    if not isinstance(entry, dict):
        entry = data[str(scope)] = {"live": entry if isinstance(entry, list) else [], "empty": []}
    return entry


def add_screen(scope, message_id):
    def updater(data):
        live = entry_of(data, scope)["live"]
        if message_id not in live:
            live.append(message_id)
        del live[:-MAX_SCREENS]

    update_data(SCREENS_FILE, updater)


# Returns True if the message was a live screen.
def remove_screen(scope, message_id):
    def updater(data):
        entry = entry_of(data, scope)
        if message_id in entry["empty"]:
            entry["empty"].remove(message_id)
        if message_id in entry["live"]:
            entry["live"].remove(message_id)
            return True
        return False

    return update_data(SCREENS_FILE, updater)


# Makes message_id (and the extra messages, e.g. the /start of the user) the only screens of the scope and returns
# the screens they replaced.
def replace_screens(scope, message_id, empty=False, extra=()):
    def updater(data):
        entry = entry_of(data, scope)
        current = [message_id, *extra]
        old = [screen for screen in entry["live"] if screen not in current]
        entry["live"] = current
        entry["empty"] = [message_id] if empty else []
        return old

    return update_data(SCREENS_FILE, updater)


def mark_empty(scope, message_id, empty):
    def updater(data):
        entry = entry_of(data, scope)
        if message_id in entry["empty"]:
            entry["empty"].remove(message_id)
        if empty:
            entry["empty"].append(message_id)
            if message_id not in entry["live"]:
                entry["live"].append(message_id)

    update_data(SCREENS_FILE, updater)


# Takes the "no lists" screens out of the scope and returns them.
def pop_empty(scope):
    def updater(data):
        entry = entry_of(data, scope)
        old = entry["empty"]
        entry["empty"] = []
        entry["live"] = [screen for screen in entry["live"] if screen not in old]
        return old

    return update_data(SCREENS_FILE, updater)
