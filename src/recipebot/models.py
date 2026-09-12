from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


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
