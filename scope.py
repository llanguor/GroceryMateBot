def scope_key(chat_id, thread_id=None):
    return str(chat_id) if thread_id is None else f"{chat_id}:{thread_id}"
