"""
Helpers for counting and truncating text with model-accurate tokenization.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Iterable

import tiktoken
from django.conf import settings

DEFAULT_ENCODING = "cl100k_base"
PROMPT_TOKEN_LIMIT = 10_000


@lru_cache(maxsize=8)
def _resolve_encoding(model_name: str | None) -> tiktoken.Encoding:
    """
    Return a tiktoken encoding for the provided model, falling back safely.
    """

    if not model_name:
        return tiktoken.get_encoding(DEFAULT_ENCODING)

    try:
        return tiktoken.encoding_for_model(model_name)
    except KeyError:
        return tiktoken.get_encoding(DEFAULT_ENCODING)


def count_tokens(text: str, *, model_name: str | None = None) -> int:
    """
    Return the number of tokens the text would consume for the target model.
    """

    if not text:
        return 0

    encoding = _resolve_encoding(model_name or settings.OPENAI_RESPONSES_MODEL)
    return len(encoding.encode(text))


def truncate_to_limit(
    text: str, limit: int = PROMPT_TOKEN_LIMIT, *, model_name: str | None = None
) -> str:
    """
    Trim text to the specified token budget using the target model encoding.
    """

    cleaned = (text or "").strip()
    if not cleaned or limit <= 0:
        return ""

    encoding = _resolve_encoding(model_name or settings.OPENAI_RESPONSES_MODEL)
    tokens = encoding.encode(cleaned)
    if len(tokens) <= limit:
        return cleaned

    truncated_tokens = tokens[:limit]
    truncated_text = encoding.decode(truncated_tokens).rstrip()
    return f"{truncated_text}\n[truncated]"


def iter_token_chunks(
    text: str, chunk_size: int, *, model_name: str | None = None
) -> Iterable[str]:
    """
    Yield successive text chunks respecting the chunk_size token limit.
    """

    if chunk_size <= 0:
        return

    cleaned = (text or "").strip()
    if not cleaned:
        return

    encoding = _resolve_encoding(model_name or settings.OPENAI_RESPONSES_MODEL)
    tokens = encoding.encode(cleaned)
    for start in range(0, len(tokens), chunk_size):
        yield encoding.decode(tokens[start : start + chunk_size])
