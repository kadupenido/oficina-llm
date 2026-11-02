def provisional_title(message: str, max_chars: int = 48) -> str:
    one_line = " ".join(message.split())
    if not one_line:
        return "Nova conversa"
    if len(one_line) <= max_chars:
        return one_line
    return one_line[: max_chars - 1].rstrip() + "…"


def clean_title(raw: str) -> str:
    text = " ".join(raw.replace("\n", " ").split()).strip("\"'`“”‘’ ")
    if len(text) > 80:
        text = text[:79].rstrip() + "…"
    return text


def title_messages(user_message: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "Gere um título curto, com no máximo 6 palavras, no idioma da mensagem. "
                "Responda apenas o título, sem aspas."
            ),
        },
        {"role": "user", "content": user_message[:500]},
    ]
