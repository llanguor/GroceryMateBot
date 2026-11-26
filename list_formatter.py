import html
from settings import settings
from categories import category_index


def category_label(category):
    if settings["show_category_emoji"] and category["emoji"]:
        return f"{category['emoji']} {category['name']}"
    return category["name"]


def item_label(item):
    return f"{settings['done_mark'] if item['done'] else item['emoji']} {item['name']}"


# Groups items by category, in the order of categories.json. Empty categories are left out.
def group_items(items, categories):
    groups = {}
    for item in items:
        groups.setdefault(category_index(categories, item["category"]), []).append(item)
    return [(index, groups[index]) for index in sorted(groups)]


BLANK = "\u2800"  # Braille blank: invisible, but Telegram does not trim it like ordinary spaces


# Telegram makes the bubble (and the buttons under it) as wide as its longest line. When all lines are shorter than
# "min_message_width", the empty lines between the categories are filled with invisible blanks up to that width.
# With a single category there is no such line, so the blanks go after its title.
def list_text(shopping_list, categories):
    blocks = [[category_label(categories[index])] + [item_label(item) for item in items]
              for index, items in group_items(shopping_list["items"], categories)]
    padding = max(0, settings["min_message_width"] - max(len(line) for block in blocks for line in block))
    if padding and len(blocks) == 1:
        blocks[0][0] += BLANK * padding
    rendered = ["\n".join([f"<b>{html.escape(block[0].rstrip(BLANK))}</b>{block[0][len(block[0].rstrip(BLANK)):]}"] + [html.escape(line) for line in block[1:]]) for block in blocks]
    separator = f"\n{BLANK * settings['min_message_width']}\n" if padding else "\n\n"
    return separator.join(rendered)
