import hashlib
import re
from typing import Literal
import tiktoken

APPROX_BUFFER = 1.1
TRIM_BUFFER = 0.8
EMBEDDED_IMAGE_DATA_PLACEHOLDER = "[embedded image data omitted from token estimate]"
_EMBEDDED_IMAGE_DATA_URL_PATTERN = re.compile(
    r"data:(image/[A-Za-z0-9.+-]+(?:;[A-Za-z0-9.+-]+=[A-Za-z0-9.+/=_-]+)*);base64,[A-Za-z0-9+/=_-]+"
)
_TOKEN_COUNT_CACHE: dict[tuple[bytes, bytes], int] = {}
_TOKEN_COUNT_CACHE_LIMIT = 256


def count_tokens(text: str, encoding_name="cl100k_base") -> int:
    if not text:
        return 0

    # Prompt builds re-count the same large texts; hashing is far cheaper than encoding.
    key = (
        encoding_name.encode("ascii", "ignore"),
        hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).digest(),
    )
    cached = _TOKEN_COUNT_CACHE.get(key)
    if cached is not None:
        return cached

    # Get the encoding
    encoding = tiktoken.get_encoding(encoding_name)

    # Encode the text and count the tokens
    tokens = encoding.encode(text, disallowed_special=())
    token_count = len(tokens)

    if len(_TOKEN_COUNT_CACHE) >= _TOKEN_COUNT_CACHE_LIMIT:
        _TOKEN_COUNT_CACHE.clear()
    _TOKEN_COUNT_CACHE[key] = token_count

    return token_count


def approximate_tokens(
    text: str,
) -> int:
    return int(count_tokens(text) * APPROX_BUFFER)


def sanitize_embedded_image_data_urls(text: str) -> str:
    if not text:
        return text

    return _EMBEDDED_IMAGE_DATA_URL_PATTERN.sub(
        f"data:\\1;base64,{EMBEDDED_IMAGE_DATA_PLACEHOLDER}",
        text,
    )


def approximate_prompt_tokens(text: str) -> int:
    return approximate_tokens(sanitize_embedded_image_data_urls(text))


def trim_to_tokens(
    text: str,
    max_tokens: int,
    direction: Literal["start", "end"],
    ellipsis: str = "...",
) -> str:
    chars = len(text)
    tokens = count_tokens(text)

    if tokens <= max_tokens:
        return text

    approx_chars = int(chars * (max_tokens / tokens) * TRIM_BUFFER)

    if direction == "start":
        return text[:approx_chars] + ellipsis
    return ellipsis + text[chars - approx_chars : chars]
