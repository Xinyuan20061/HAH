"""Decode one JSON object from text without accepting arrays or guessing merged objects."""

import json


def json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    text = text.strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        pass
    objects = []
    position = 0
    while position < len(text):
        start = text.find("{", position)
        if start < 0:
            break
        try:
            value, length = decoder.raw_decode(text[start:])
        except ValueError:
            position = start + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        position = start + length
    return objects[0] if len(objects) == 1 else {}
