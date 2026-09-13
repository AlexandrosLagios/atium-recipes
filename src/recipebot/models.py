import unicodedata
from typing import Literal
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, field_validator


# The video id is the whole identity of a YouTube watch URL, so stripping the
# query would collapse every video onto one dedupe key and make every video
# after the first look already saved.
def _kept_query(parts) -> str:
    host = parts.netloc.lower()
    if host != "youtube.com" and not host.endswith(".youtube.com"):
        return ""
    if parts.path != "/watch":
        return ""
    video_id = parse_qs(parts.query).get("v", [""])[0]
    return urlencode({"v": video_id}) if video_id else ""


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, _kept_query(parts), ""))


# A Notion page icon is one emoji, and the API rejects the whole page when it is
# anything else. The model returns a string, so keep the first grapheme cluster
# and drop the rest: a zero width joiner, a variation selector, a keycap, a skin
# tone modifier and a second regional indicator all continue the same emoji.
_CONTINUATIONS = frozenset({0x200D, 0xFE0F, 0x20E3})
_SKIN_TONES = range(0x1F3FB, 0x1F400)
_REGIONAL = range(0x1F1E6, 0x1F200)


def first_emoji(value: str) -> str:
    value = value.strip()
    if not value or unicodedata.category(value[0]) != "So":
        return ""
    kept = [value[0]]
    for char in value[1:]:
        code = ord(char)
        continues = (
            kept[-1] == "\u200d"
            or code in _CONTINUATIONS
            or code in _SKIN_TONES
            or (len(kept) == 1 and code in _REGIONAL and ord(kept[0]) in _REGIONAL)
        )
        if not continues:
            break
        kept.append(char)
    return "".join(kept)


class Ingredient(BaseModel):
    name: str
    quantity: str = ""
    category: str = ""
    group: str = ""


class ExtractedRecipe(BaseModel):
    name: str
    cuisine: str
    meal: list[str]
    difficulty: Literal["Easy", "Hard"]
    time_min: int
    keeps_days: int = 0
    servings: int
    ingredients: list[Ingredient]
    method: list[str]
    notes: list[str] = []
    emoji: str = ""

    @field_validator("emoji")
    @classmethod
    def _single_emoji(cls, value: str) -> str:
        return first_emoji(value)


class Recipe(ExtractedRecipe):
    model_config = ConfigDict(validate_assignment=True)

    source: str
    source_url: str = ""
    image_url: str = ""
    source_text: str = ""
    high_confidence: bool = False

    @field_validator("source_url")
    @classmethod
    def _canonicalize_source_url(cls, value: str) -> str:
        return canonical_url(value)

    @classmethod
    def from_extracted(
        cls,
        extracted: ExtractedRecipe,
        *,
        source: str,
        source_url: str = "",
        image_url: str = "",
        source_text: str = "",
        high_confidence: bool = False,
    ) -> "Recipe":
        return cls(
            **extracted.model_dump(),
            source=source,
            source_url=source_url,
            image_url=image_url,
            source_text=source_text,
            high_confidence=high_confidence,
        )
