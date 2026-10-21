"""Stable bot identities and isolated chat keys; legacy chats belong to Tun."""

BOT_IDENTITIES = {
    "tun": {"name": "Bot CORS Manager", "owner": "Tun"},
    "tb": {"name": "Bot TB CORS", "owner": "Trường"},
}


def chat_bot_key(chat_id: str) -> str:
    return "tb" if str(chat_id).startswith("tb:") else "tun"


def raw_chat_id(chat_id: str) -> str:
    value = str(chat_id)
    return value[3:] if value.startswith("tb:") else value


def scoped_chat_id(bot_key: str, chat_id: str) -> str:
    value = str(chat_id)
    if bot_key == "tb" and not value.startswith("tb:"):
        return f"tb:{value}"
    return value
