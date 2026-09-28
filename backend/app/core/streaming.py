import re


_DISPLAY_TOKEN = re.compile(
    r"[\u3400-\u4dbf\u4e00-\u9fff]|"
    r"[A-Za-z0-9]+(?:[._:/+-][A-Za-z0-9]+)*|"
    r"\s+|.",
    re.DOTALL,
)


def display_tokens(text: str):
    """Yield small display tokens without splitting a UTF-8 byte sequence.

    Agent output is validated as a complete response before this helper is
    used.  The stream is therefore a presentation channel, not a way to
    bypass the safety and structured-plan checks.
    """

    yield from _DISPLAY_TOKEN.findall(text or "")
