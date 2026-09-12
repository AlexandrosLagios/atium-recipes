from typing import Literal
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from pydantic import BaseModel, field_validator


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


class Ingredient(BaseModel):
    name: str
    quantity: str = ""
    category: str = ""


class ExtractedRecipe(BaseModel):
    name: str
    cuisine: str
    meal: list[str]
    difficulty: Literal["Easy", "Hard"]
    time_min: int
    servings: int
    ingredients: list[Ingredient]
    method: list[str]


class Recipe(ExtractedRecipe):
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
