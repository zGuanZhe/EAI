from __future__ import annotations

import hashlib
import unicodedata


VERSION = "eai-text-v1"
PUNCTUATION = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "−": "-"})


def _is_cjk(value: str) -> bool:
    if not value:
        return False
    codepoint = ord(value[0])
    return 0x3400 <= codepoint <= 0x9FFF or 0xF900 <= codepoint <= 0xFAFF


def normalize_with_map(text: str) -> tuple[str, list[list[int]]]:
    source = str(text or "")
    characters: list[str] = []
    offsets: list[int] = []
    index = 0
    while index < len(source):
        char = source[index]
        if char == "\u00ad":
            index += 1
            continue
        if char == "-" and index > 0:
            probe = index + 1
            saw_newline = False
            while probe < len(source) and source[probe].isspace():
                saw_newline = saw_newline or source[probe] in "\r\n"
                probe += 1
            if saw_newline and source[index - 1].isalnum() and probe < len(source) and source[probe].isalnum():
                index = probe
                continue
        normalized = unicodedata.normalize("NFKC", char).translate(PUNCTUATION).casefold()
        for value in normalized:
            if value.isspace():
                probe = index + 1
                while probe < len(source) and source[probe].isspace():
                    probe += 1
                following = unicodedata.normalize("NFKC", source[probe]).casefold() if probe < len(source) else ""
                if characters and _is_cjk(characters[-1]) and _is_cjk(following):
                    continue
                if characters and characters[-1] != " ":
                    characters.append(" ")
                    offsets.append(index)
            else:
                characters.append(value)
                offsets.append(index)
        index += 1
    while characters and characters[-1] == " ":
        characters.pop()
        offsets.pop()
    ranges: list[list[int]] = []
    for normalized_index, original_index in enumerate(offsets):
        if ranges and ranges[-1][1] == normalized_index and original_index in {ranges[-1][2], ranges[-1][3]}:
            ranges[-1][1] += 1
            ranges[-1][3] = max(ranges[-1][3], original_index + 1)
        else:
            ranges.append([normalized_index, normalized_index + 1, original_index, original_index + 1])
    return "".join(characters), ranges


def normalized_hash(text: str) -> tuple[str, list[list[int]], str]:
    normalized, mapping = normalize_with_map(text)
    return normalized, mapping, hashlib.sha256(normalized.encode("utf-8")).hexdigest()
