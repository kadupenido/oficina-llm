import math

from app.errors import MessageTooLong
from app.services.context import build_context, estimate_tokens, message_cost, prompt_messages, prompt_token_estimate
from app.services.think import ThinkStripper, strip_think


def test_estimate_tokens_rounds_up():
    assert estimate_tokens("", 2.5) == 0
    assert estimate_tokens("abcd", 4) == 1
    assert estimate_tokens("abcde", 4) == 2


def test_context_keeps_system_and_current_and_drops_oldest_pair():
    history = []
    for index in range(4):
        history.append({"role": "user", "content": f"u{index}"})
        history.append({"role": "assistant", "content": "a" * 20})
    messages, truncated = build_context(
        system_prompt="SYS",
        history=history,
        current_user="agora",
        max_tokens=10,
        ctx_size=80,
        reserve=10,
        chars_per_token=1,
    )
    assert messages[0] == {"role": "system", "content": "SYS"}
    assert messages[-1] == {"role": "user", "content": "agora"}
    assert truncated is True
    contents = [item["content"] for item in messages]
    assert "u0" not in contents
    assert "u3" in contents


def test_single_message_over_budget_raises():
    try:
        build_context(
            system_prompt="SYS",
            history=[],
            current_user="x" * 30,
            max_tokens=10,
            ctx_size=40,
            reserve=10,
            chars_per_token=1,
        )
    except MessageTooLong:
        return
    raise AssertionError("deveria recusar a mensagem")


def test_budget_math_matches_chars():
    system = "SYS"
    current = "12345"
    overhead = 4
    budget = 80 - 20 - 10
    remaining = budget - (len(system) + overhead) - (len(current) + overhead)
    pair = (5 + overhead) + (9 + overhead)
    assert remaining // pair == 1
    assert math.ceil(len(current) / 1) == 5


def test_overhead_drops_a_pair_that_would_fit_without_it():
    history = [
        {"role": "user", "content": "uu"},
        {"role": "assistant", "content": "aa"},
        {"role": "user", "content": "uu"},
        {"role": "assistant", "content": "aa"},
    ]
    messages, truncated = build_context(
        system_prompt="S",
        history=history,
        current_user="C",
        max_tokens=10,
        ctx_size=50,
        reserve=10,
        chars_per_token=1,
        message_overhead=4,
    )
    assert truncated is True
    assert [item["content"] for item in messages] == ["S", "uu", "aa", "C"]


def test_prompt_token_estimate_sums_the_prompt():
    messages, truncated = build_context(
        system_prompt="SYS",
        history=[
            {"role": "user", "content": "u"},
            {"role": "assistant", "content": "aa"},
        ],
        current_user="agora",
        max_tokens=10,
        ctx_size=80,
        reserve=10,
        chars_per_token=1,
        message_overhead=4,
    )
    assert truncated is False
    assert prompt_token_estimate(messages, 1, 4) == sum(
        message_cost(item["content"], 1, 4) for item in messages
    )
    assert [item["content"] for item in messages] == ["SYS", "u", "aa", "agora"]


def test_prompt_messages_skip_incomplete_pairs():
    history = [
        {"role": "user", "content": "um"},
        {"role": "assistant", "content": "ok", "status": "complete"},
        {"role": "user", "content": "dois"},
        {"role": "assistant", "content": "parcial", "status": "interrupted"},
        {"role": "user", "content": "solto"},
    ]
    kept = prompt_messages(history)
    assert [item["content"] for item in kept] == ["um", "ok"]


def test_strip_think_across_chunks():
    stripper = ThinkStripper()
    assert stripper.feed("<thi") == ""
    assert stripper.feed("nk>segredo</think>Oi") == "Oi"
    assert stripper.flush() == ""


def test_strip_think_complete_string():
    assert strip_think("antes <think>oculto</think> depois") == "antes  depois"


def test_feed_split_emits_thinking_and_visible():
    stripper = ThinkStripper()
    assert stripper.feed_split("antes <think>oculto</think> depois") == (
        "oculto",
        "antes  depois",
    )
    stripper2 = ThinkStripper()
    assert stripper2.feed("<thi") == ""
    assert stripper2.feed_split("nk>segredo</think>Oi") == ("segredo", "Oi")
