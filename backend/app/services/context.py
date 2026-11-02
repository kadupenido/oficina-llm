import math

from app.errors import MessageTooLong


def estimate_tokens(text: str, chars_per_token: float) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / chars_per_token))


def message_cost(text: str, chars_per_token: float, overhead: int) -> int:
    return estimate_tokens(text, chars_per_token) + max(0, overhead)


def prompt_token_estimate(
    messages: list[dict[str, str]],
    chars_per_token: float,
    message_overhead: int,
) -> int:
    """Soma o custo das mensagens que entram no prompt."""
    return sum(
        message_cost(item.get("content") or "", chars_per_token, message_overhead)
        for item in messages
    )


def prompt_messages(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Só pares user/assistant com a resposta complete.

    interrupted e error ficam no banco e na tela, mas não entram no modelo.
    Um user sem resposta complete depois dele também fica de fora, para o
    prompt não ter dois user seguidos.
    """
    kept: list[dict[str, str]] = []
    index = 0
    while index < len(history):
        current = history[index]
        nxt = history[index + 1] if index + 1 < len(history) else None
        status = nxt.get("status", "complete") if nxt is not None else ""
        if (
            current.get("role") == "user"
            and nxt is not None
            and nxt.get("role") == "assistant"
            and status == "complete"
        ):
            kept.append({"role": "user", "content": current["content"]})
            kept.append({"role": "assistant", "content": nxt["content"]})
            index += 2
            continue
        index += 1
    return kept


def build_context(
    *,
    system_prompt: str,
    history: list[dict[str, str]],
    current_user: str,
    max_tokens: int,
    ctx_size: int,
    reserve: int,
    chars_per_token: float,
    message_overhead: int = 4,
) -> tuple[list[dict[str, str]], bool]:
    """Monta o prompt do turno.

    O system prompt e a mensagem atual sempre entram. O histórico é
    percorrido do mais novo para o mais antigo, em pares user/assistant.
    O primeiro par que não cabe interrompe a inclusão: turnos mais antigos
    também ficam de fora, para não abrir um buraco no meio da conversa.
    """
    budget = ctx_size - max_tokens - reserve
    if budget <= 0:
        raise MessageTooLong()

    system_cost = message_cost(system_prompt, chars_per_token, message_overhead)
    current_cost = message_cost(current_user, chars_per_token, message_overhead)
    if system_cost + current_cost > budget:
        raise MessageTooLong()

    remaining = budget - system_cost - current_cost
    kept: list[dict[str, str]] = []
    truncated = False
    index = len(history) - 1
    while index >= 0:
        if (
            index >= 1
            and history[index - 1]["role"] == "user"
            and history[index]["role"] == "assistant"
        ):
            chunk = [history[index - 1], history[index]]
            step = 2
        else:
            chunk = [history[index]]
            step = 1
        cost = sum(
            message_cost(item["content"], chars_per_token, message_overhead) for item in chunk
        )
        if cost > remaining:
            truncated = True
            break
        kept = chunk + kept
        remaining -= cost
        index -= step

    if index >= 0:
        truncated = True

    messages = [
        {"role": "system", "content": system_prompt},
        *kept,
        {"role": "user", "content": current_user},
    ]
    return messages, truncated
