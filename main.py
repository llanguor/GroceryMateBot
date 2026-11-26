import functools
import re
import time
import telebot
from telebot import types
from telebot.apihelper import ApiTelegramException
from settings import settings
from ai_parser import AiError, generate_products, parse_shopping_list, transcribe_audio
from categories import category_index, load_categories
from clock import format_date, today
from list_formatter import category_label, group_items, list_text
from lists_handler import add_to_list, delete_list, get_list, get_lists, reset_done, set_done, set_message_id
from logger import get_logger
from scope import scope_key
from screens_handler import add_screen, mark_empty, pop_empty, remove_screen, replace_screens

logger = get_logger()
messages = settings["messages"]
buttons = settings["buttons"]

# Views of a list. The callback data of a list screen carries the view it is showing:
# "m" - the menu with categories, "a" - all products, a number - the category with that index.
MENU, ALL = "m", "a"


def thread_of(message):
    return getattr(message, "message_thread_id", None) if getattr(message, "is_topic_message", None) else None


def message_context(message):
    return message.chat.id, message.from_user.id, thread_of(message)


def call_context(call):
    return call.message.chat.id, call.from_user.id, thread_of(call.message)


def update_context(update):
    return call_context(update) if isinstance(update, types.CallbackQuery) else message_context(update)


# Data belongs to a chat or, in a group with topics, to a topic - not to a user.
def scope_of(ctx):
    return scope_key(ctx[0], ctx[2])


def icon_button(icon, callback_data):
    return types.InlineKeyboardButton(text=buttons[icon], callback_data=callback_data)


def with_note(note, screen):
    text, keyboard = screen
    return f"{note.rstrip()}\n{text}", keyboard


def confirm_screen(text, yes_data, no_data):
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.row(types.InlineKeyboardButton(text=buttons["confirm_no"], callback_data=no_data),
                 types.InlineKeyboardButton(text=buttons["confirm_yes"], callback_data=yes_data))
    return text, keyboard


def start_screen(scope):
    shopping_lists = get_lists(scope)
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    row = []
    if settings["show_add_in_start_menu"]:
        row.append(icon_button("add_icon", "new"))
    if settings["show_generate_in_start_menu"]:
        row.append(icon_button("generate_icon", "gen_new"))
    if row:
        keyboard.row(*row)
    if not shopping_lists:
        return messages["start_empty"], keyboard
    for shopping_list in shopping_lists:
        keyboard.add(types.InlineKeyboardButton(text=format_date(shopping_list["date"]), callback_data=f"v_{shopping_list['id']}_{MENU}"))
    return messages["start"], keyboard


def normalize_view(view, categories):
    if not settings["show_category_menu"]:
        return ALL
    if view == ALL or (view.isdigit() and int(view) < len(categories)):
        return view
    return MENU


def active_items(shopping_list, categories, view=ALL):
    items = [item for item in shopping_list["items"] if not item["done"]]
    if view.isdigit():
        items = [item for item in items if category_index(categories, item["category"]) == int(view)]
    return sorted(items, key=lambda item: category_index(categories, item["category"]))


# A category whose last product was just ticked off is of no use any more - back to the menu.
def settle_view(shopping_list, categories, view):
    view = normalize_view(view, categories)
    return MENU if view.isdigit() and not active_items(shopping_list, categories, view) else view


# The text of a list is always the whole list; only the buttons depend on the view.
def list_screen(shopping_list, view=MENU):
    categories = load_categories()
    view = normalize_view(view, categories)
    list_id = shopping_list["id"]
    clear_button = icon_button("clear_icon", f"c_{list_id}_{view}")
    back = "nav_start" if view == MENU or not settings["show_category_menu"] else f"v_{list_id}_{MENU}"
    # The first row: back, reset, add, generate, clear
    row = [icon_button("back_icon", back)]
    if settings["show_reset_in_list_menu"]:
        row.append(icon_button("reset_icon", f"r_{list_id}_{view}"))
    if settings["show_add_in_list_menu"]:
        row.append(icon_button("add_icon", f"add_{list_id}"))
    if settings["show_generate_in_list_menu"]:
        row.append(icon_button("generate_icon", f"gen_{list_id}"))
    row.append(clear_button)
    keyboard = types.InlineKeyboardMarkup(row_width=1)
    if view == MENU:
        keyboard.row(*row)
        if active_items(shopping_list, categories):
            keyboard.add(types.InlineKeyboardButton(text=buttons["all_items"], callback_data=f"v_{list_id}_{ALL}"))
        remaining = {index for index, _ in group_items(active_items(shopping_list, categories), categories)}
        for index, category in enumerate(categories):
            if index in remaining:
                keyboard.add(types.InlineKeyboardButton(text=category_label(category), callback_data=f"v_{list_id}_{index}"))
    else:
        keyboard.row(*row)
        for item in active_items(shopping_list, categories, view)[:settings["max_item_buttons"]]:
            keyboard.add(types.InlineKeyboardButton(text=f"{item['emoji']} {item['name']}", callback_data=f"d_{list_id}_{view}_{item['id']}"))
    return list_text(shopping_list, categories), keyboard


# "clear_without_confirm": false - always ask, "checked" - ask only while more than one product is not ticked off,
# true - never ask.
def needs_confirm(shopping_list):
    mode = settings["clear_without_confirm"]
    if mode is True:
        return False
    if mode == "checked":
        return sum(not item["done"] for item in shopping_list["items"]) > 1
    return True


def register_handlers(bot, username, bot_id):
    pending = {}
    prompts = {}  # the question of the bot that waits for an answer, by context
    mention = re.compile(rf"@{re.escape(username)}(?![A-Za-z0-9_])", re.IGNORECASE)

    def send(ctx, text, keyboard=None, silent=True):
        chat_id, _, thread_id = ctx
        return bot.send_message(chat_id, text, reply_markup=keyboard, message_thread_id=thread_id, disable_notification=silent, parse_mode="HTML")

    def edit(message, text, keyboard=None):
        try:
            bot.edit_message_text(text, message.chat.id, message.message_id, reply_markup=keyboard, parse_mode="HTML")
        except ApiTelegramException as e:
            if "message is not modified" not in str(e):
                raise

    def remove_message(chat_id, message_id):
        if message_id is None:
            return
        try:
            bot.delete_message(chat_id, message_id)
        except ApiTelegramException as e:
            logger.error(f"Failed to delete the message {message_id} in chat {chat_id}: {e}")

    def safe_handler(func):
        @functools.wraps(func)
        def wrapper(update):
            try:
                logger.info(f"User with id {update.from_user.id} called {func.__name__}")
                func(update)
            except Exception as e:
                logger.error(f"Error in the {func.__name__} function for the user with ID {update.from_user.id}: {e}")
                if settings["send_error_message"]:
                    send(update_context(update), messages["error"])
        return wrapper

    # Forgets what the bot is waiting for and removes its question from the chat.
    def clear_prompt(ctx):
        pending.pop(ctx, None)
        remove_message(ctx[0], prompts.pop(ctx, None))

    def begin(call):
        bot.answer_callback_query(call.id)
        ctx = call_context(call)
        clear_prompt(ctx)
        return ctx

    def expect(ctx, handler, *args):
        pending[ctx] = (handler, args)

    # Shows the start menu in place of the message of the call and remembers whether it says "no lists".
    def show_start(call, scope, note=None):
        screen = start_screen(scope)
        edit(call.message, *(with_note(note, screen) if note else screen))
        mark_empty(scope, call.message.message_id, not get_lists(scope))

    def stale(call, scope):
        show_start(call, scope, messages["list_not_found"])

    # The only place where products enter a list. The list message is sent anew (the old one is removed), so the
    # updated list shows up at the bottom of the chat.
    # mode: "today" - the list of today, "last" - the newest list, "new" - always a new list, "list" - the list list_id.
    # generate - the text is a request to the model (a recipe, a link, an idea) and not a list of products.
    def submit(ctx, text, mode, source=None, list_id=None, generate=False):
        text = text.strip()
        if not text:
            send(ctx, messages["empty_text"])
            return
        if len(text) > settings["max_input_length"]:
            send(ctx, messages["too_long"].format(max_length=settings["max_input_length"]))
            return
        trigger = settings["generate_trigger"].strip().casefold()
        if trigger and text.casefold().startswith(trigger):
            generate = True
        chat_id, _, thread_id = ctx
        try:
            bot.send_chat_action(chat_id, "typing", message_thread_id=thread_id)
        except ApiTelegramException:
            pass
        try:
            entries = (generate_products if generate else parse_shopping_list)(text, load_categories())
        except AiError as e:
            logger.error(f"Failed to {'generate' if generate else 'parse'} the shopping list in chat {chat_id}: {e}")
            send(ctx, messages["generate_failed" if generate else "ai_failed"])
            return
        if not entries:
            send(ctx, messages["generate_nothing" if generate else "nothing_found"])
            return
        scope = scope_of(ctx)
        shopping_list, previous_message_id = add_to_list(scope, chat_id, thread_id, entries, today(), mode, list_id)
        sent = send(ctx, *list_screen(shopping_list), silent=settings["silent_list_messages"])
        set_message_id(scope, shopping_list["id"], sent.message_id)
        add_screen(scope, sent.message_id)
        for message_id in pop_empty(scope):
            if settings["delete_old_messages_on_start"]:
                remove_message(chat_id, message_id)
        if previous_message_id is not None and remove_screen(scope, previous_message_id):
            remove_message(chat_id, previous_message_id)
        if source is not None and settings["delete_user_message"]:
            remove_message(chat_id, source.message_id)

    # A voice message is turned into text and then goes the same way as a written request.
    def submit_audio(message, mode, list_id=None, generate=True):
        ctx = message_context(message)
        audio = message.voice or message.audio
        if audio.duration and audio.duration > settings["max_voice_seconds"]:
            send(ctx, messages["voice_too_long"].format(max_seconds=settings["max_voice_seconds"]))
            return
        try:
            bot.send_chat_action(ctx[0], "typing", message_thread_id=ctx[2])
        except ApiTelegramException:
            pass
        try:
            data = bot.download_file(bot.get_file(audio.file_id).file_path)
            text = transcribe_audio(data, getattr(audio, "mime_type", None) or "audio/ogg")
        except (AiError, ApiTelegramException) as e:
            logger.error(f"Failed to transcribe the audio in chat {ctx[0]}: {e}")
            send(ctx, messages["voice_failed"])
            return
        if not text:
            send(ctx, messages["voice_empty"])
            return
        submit(ctx, text, mode, message, list_id, generate)

    def receive_items(message, mode, list_id, generate):
        if message.voice or message.audio:
            if settings["accept_voice"]:
                submit_audio(message, mode, list_id, generate)
            return
        submit(message_context(message), mention.sub(" ", message.text or ""), mode, message, list_id, generate)

    # Asks for products and listens to the next message of the user, with or without the mention of the bot.
    # For a command the question is a reply to it and forces the reply on the user, so that in a group with the
    # privacy mode on the answer reaches the bot too.
    def ask_items(ctx, mode, list_id=None, message=None, generate=False):
        clear_prompt(ctx)
        keyboard = types.ForceReply(selective=True) if message is not None and message.chat.type != "private" else None
        reply = types.ReplyParameters(message.message_id, allow_sending_without_reply=True) if message is not None else None
        text = messages["generate_question"] if generate else messages["new_prompt"] if mode == "new" else messages["append_prompt"]
        prompt = bot.send_message(ctx[0], text, reply_markup=keyboard, message_thread_id=ctx[2],
                                  disable_notification=settings["silent_setup_messages"], reply_parameters=reply)
        prompts[ctx] = prompt.message_id
        expect(ctx, receive_items, mode, list_id, generate)

    # /new and /append: with the products in the same message they are used at once, otherwise the bot asks for them.
    def command_items(message, mode, generate=False):
        ctx = message_context(message)
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) > 1:
            submit(ctx, parts[1], mode, message, generate=generate)
        else:
            ask_items(ctx, mode, message=message, generate=generate)

    @bot.message_handler(content_types=['text', 'voice', 'audio'], func=lambda message: message.from_user is not None and message_context(message) in pending)
    @safe_handler
    def handle_pending(message):
        ctx = message_context(message)
        entry = pending.pop(ctx, None)
        if entry is None:
            return
        remove_message(ctx[0], prompts.pop(ctx, None))
        if message.text and message.text.startswith("/"):
            bot.process_new_messages([message])
            return
        handler, args = entry
        handler(message, *args)

    @bot.message_handler(commands=['start'])
    @safe_handler
    def start(message):
        ctx = message_context(message)
        scope = scope_of(ctx)
        sent = send(ctx, *start_screen(scope), silent=settings["silent_info_messages"])
        old_screens = replace_screens(scope, sent.message_id, empty=not get_lists(scope), extra=[message.message_id])
        if settings["delete_old_messages_on_start"]:
            for message_id in old_screens:
                remove_message(ctx[0], message_id)

    @bot.message_handler(commands=['append'])
    @safe_handler
    def append(message):
        command_items(message, "last")

    @bot.message_handler(commands=['gen'])
    @safe_handler
    def gen(message):
        command_items(message, "today", generate=True)

    @bot.message_handler(commands=['new'])
    @safe_handler
    def new(message):
        command_items(message, "new")

    @bot.message_handler(func=lambda message: message.from_user is not None and bool(message.text) and not message.text.startswith("/")
                         and (bool(mention.search(message.text)) or (message.chat.type == "private" and settings["answer_in_private_without_mention"])))
    @safe_handler
    def handle_mention(message):
        submit(message_context(message), mention.sub(" ", message.text), "today", message)

    def replies_to_bot(message):
        reply = message.reply_to_message
        return reply is not None and reply.from_user is not None and reply.from_user.id == bot_id

    # A voice message is a request to the generative function. In a private chat it is accepted always, in a group
    # only as a reply to the bot (the others do not reach it when the privacy mode is on).
    @bot.message_handler(content_types=['voice', 'audio'], func=lambda message: settings["accept_voice"] and message.from_user is not None
                         and (message.chat.type == "private" or replies_to_bot(message)))
    @safe_handler
    def handle_audio(message):
        submit_audio(message, "today")

    @bot.callback_query_handler(func=lambda call: call.data == "nav_start")
    @safe_handler
    def handle_nav_start(call):
        ctx = begin(call)
        show_start(call, scope_of(ctx))

    @bot.callback_query_handler(func=lambda call: call.data == "new")
    @safe_handler
    def handle_new(call):
        ask_items(begin(call), "new")

    @bot.callback_query_handler(func=lambda call: call.data.startswith("add_"))
    @safe_handler
    def handle_add(call):
        ctx = begin(call)
        list_id = call.data[len("add_"):]
        if get_list(scope_of(ctx), list_id) is None:
            stale(call, scope_of(ctx))
            return
        ask_items(ctx, "list", list_id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("gen_"))
    @safe_handler
    def handle_generate(call):
        ctx = begin(call)
        target = call.data[len("gen_"):]
        if target == "new":
            ask_items(ctx, "new", generate=True)
        elif get_list(scope_of(ctx), target) is None:
            stale(call, scope_of(ctx))
        else:
            ask_items(ctx, "list", target, generate=True)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("v_"))
    @safe_handler
    def handle_view(call):
        ctx = begin(call)
        _, list_id, view = call.data.split("_", 2)
        shopping_list = get_list(scope_of(ctx), list_id)
        if shopping_list is None:
            stale(call, scope_of(ctx))
            return
        edit(call.message, *list_screen(shopping_list, view))

    @bot.callback_query_handler(func=lambda call: call.data.startswith("d_"))
    @safe_handler
    def handle_done(call):
        ctx = begin(call)
        _, list_id, view, item_id = call.data.split("_", 3)
        shopping_list = set_done(scope_of(ctx), list_id, item_id)
        if shopping_list is None:
            stale(call, scope_of(ctx))
            return
        edit(call.message, *list_screen(shopping_list, settle_view(shopping_list, load_categories(), view)))

    # The message that holds the list is turned into the start menu; another message of the same list is removed.
    @bot.callback_query_handler(func=lambda call: call.data.startswith("r_"))
    @safe_handler
    def handle_reset(call):
        ctx = begin(call)
        _, list_id, view = call.data.split("_", 2)
        shopping_list = reset_done(scope_of(ctx), list_id)
        if shopping_list is None:
            stale(call, scope_of(ctx))
            return
        edit(call.message, *list_screen(shopping_list, view))

    def clear_list(call, scope, list_id):
        removed = delete_list(scope, list_id)
        if removed and removed["message_id"] != call.message.message_id and remove_screen(scope, removed["message_id"]):
            remove_message(call.message.chat.id, removed["message_id"])
        show_start(call, scope, messages["list_cleared"])

    @bot.callback_query_handler(func=lambda call: call.data.startswith("c_"))
    @safe_handler
    def handle_clear(call):
        ctx = begin(call)
        scope = scope_of(ctx)
        _, list_id, view = call.data.split("_", 2)
        shopping_list = get_list(scope, list_id)
        if shopping_list is None:
            stale(call, scope)
        elif needs_confirm(shopping_list):
            edit(call.message, *confirm_screen(messages["clear_confirm"], f"cy_{list_id}", f"v_{list_id}_{view}"))
        else:
            clear_list(call, scope, list_id)

    @bot.callback_query_handler(func=lambda call: call.data.startswith("cy_"))
    @safe_handler
    def handle_clear_confirmed(call):
        ctx = begin(call)
        clear_list(call, scope_of(ctx), call.data[len("cy_"):])


def run_polling(bot):
    while True:
        try:
            bot.polling(non_stop=True, interval=settings["polling_interval"])
        except Exception as e:
            logger.error(f"Exception in polling: {e}")
            time.sleep(settings["polling_retry_delay"])


if __name__ == "__main__":
    for key in ("tg_token", "gemini_api_key"):
        if not settings.get(key) or str(settings[key]).startswith("PUT_"):
            logger.error(f"{key} is not set. Add it to appsettings.local.json")
            raise SystemExit(1)
    load_categories()
    bot = telebot.TeleBot(settings["tg_token"], num_threads=settings["worker_threads"])
    me = bot.get_me()
    register_handlers(bot, me.username, me.id)
    logger.info("Bot started")
    run_polling(bot)
