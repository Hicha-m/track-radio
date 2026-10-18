"""Contrat des sources HTML : construire une URL et extraire artiste/titre.

Les adaptateurs sont du code Python audité, jamais du code chargé depuis le JSON.
Le téléchargement reste dans metadata.py pour partager ses protections réseau.
"""

import json
import re
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
from threading import Lock
from typing import Literal, Protocol

import soupsieve
from bs4 import BeautifulSoup, UnicodeDammit
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class HtmlConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    adapter: Literal["selectors", "funradio"] = "selectors"
    title_selector: str | None = Field(None, min_length=1, max_length=200)
    artist_selector: str | None = Field(None, min_length=1, max_length=200)
    title_attribute: str | None = Field(None, min_length=1, max_length=100)
    artist_attribute: str | None = Field(None, min_length=1, max_length=100)

    @field_validator("title_selector", "artist_selector")
    @classmethod
    def valid_selector(cls, value):
        if value:
            try:
                soupsieve.compile(value)
            except soupsieve.SelectorSyntaxError as exc:
                raise ValueError("Sélecteur CSS invalide.") from exc
        return value

    @model_validator(mode="after")
    def complete(self):
        if self.adapter == "selectors" and not self.title_selector:
            raise ValueError("title_selector est obligatoire pour l’adaptateur selectors.")
        if self.artist_attribute and not self.artist_selector:
            raise ValueError("artist_attribute nécessite artist_selector.")
        if self.adapter == "funradio" and any(
            (self.title_selector, self.artist_selector, self.title_attribute, self.artist_attribute)
        ):
            raise ValueError("L’adaptateur funradio n’utilise pas de sélecteurs configurables.")
        return self


class HtmlAdapter(Protocol):
    def url(self, base_url: str, now: datetime) -> str: ...

    def parse(self, content: bytes, options: HtmlConfig) -> tuple[str | None, str] | None: ...


class _StructureGuard(HTMLParser):
    """Borner les objets DOM avant de construire l’arbre Beautiful Soup."""

    def __init__(self):
        super().__init__()
        self.nodes = 0

    def count(self, *args):
        self.nodes += 1
        if self.nodes > 20_000:
            raise ValueError(
                "Page HTML trop complexe : maximum 20 000 éléments de structure/texte."
            )

    handle_starttag = count
    handle_startendtag = count
    handle_data = count
    handle_comment = count
    handle_decl = count
    handle_pi = count


class SelectorAdapter:
    def url(self, base_url: str, now: datetime) -> str:
        return base_url

    def parse(self, content: bytes, options: HtmlConfig) -> tuple[str | None, str] | None:
        # html.parser est fourni par Python : aucun navigateur ni moteur XML externe.
        text = UnicodeDammit(content, is_html=True).unicode_markup
        if text is None:
            return None
        guard = _StructureGuard()
        guard.feed(text)
        guard.close()
        soup = BeautifulSoup(text, "html.parser")
        try:

            def extract(selector, attribute):
                node = soup.select_one(selector) if selector else None
                if node is None:
                    return None
                value = node.get(attribute) if attribute else node.get_text(" ", strip=True)
                return " ".join(value.split()) if isinstance(value, str) else None

            title = extract(options.title_selector, options.title_attribute)
            artist = extract(options.artist_selector, options.artist_attribute)
            # Un artiste configuré mais absent peut signaler une page incomplète.
            if not title or (options.artist_selector and not artist):
                return None
            return artist, title
        finally:
            soup.decompose()


class FunRadioAdapter:
    def url(self, base_url: str, now: datetime) -> str:
        return f"{base_url.rstrip('/')}/{now:%d-%m-%Y}/songs?hour={now:%H}&minute={now:%M}"

    def parse(self, content: bytes, options: HtmlConfig) -> tuple[str | None, str] | None:
        for encoded in re.findall(r'data-qect-info="([^"]+)"', content.decode("utf-8", "replace")):
            try:
                info = json.loads(unescape(encoded))
            except json.JSONDecodeError:
                continue
            if not isinstance(info, dict):
                continue
            title = info.get("title")
            artist = info.get("singer")
            if isinstance(title, str) and title.strip():
                return artist.strip() or None if isinstance(artist, str) else None, title.strip()
        return None


HTML_ADAPTERS: dict[str, HtmlAdapter] = {
    "selectors": SelectorAdapter(),
    "funradio": FunRadioAdapter(),
}

# Une seule arborescence HTML en mémoire, même avec plusieurs collectes simultanées.
_parse_lock = Lock()


def parse_html(adapter: HtmlAdapter, content: bytes, options: HtmlConfig):
    with _parse_lock:
        return adapter.parse(content, options)
