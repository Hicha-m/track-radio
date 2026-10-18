import unicodedata
from functools import lru_cache


@lru_cache(maxsize=2048)
def normalize_search(value):
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", (value or "").casefold())
        if not unicodedata.combining(character)
    )


def register_search(connection, _):
    connection.create_function("fold_text", 1, normalize_search, deterministic=True)
