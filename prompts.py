from settings import settings, read


# prompts.json is read on every call, so the prompts can be edited without restarting the bot.
# A prompt is a string or a list of lines. {categories} and {query} are replaced in the prompts that have them.
def get_prompt(name):
    value = read(settings["prompts_file"]).get(name)
    if isinstance(value, list):
        value = "\n".join(value)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{settings['prompts_file']} has no prompt \"{name}\"")
    return value
