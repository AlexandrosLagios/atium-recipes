from dataclasses import dataclass
from typing import Protocol

from .config import Config
from .models import ExtractedRecipe
from .notion import Vocabulary

SYSTEM = """You extract exactly one recipe from the material the user shares.

Rules you must follow:
- Ingredient names are shopping level and singular: "Chicken", never "boneless chicken thighs", never "Chickens". Put the quantity and the form in the quantity field instead.
- Reuse an ingredient name from the known list below whenever the ingredient matches. Mint a new name only when the ingredient is genuinely new.
- Choose cuisine, meal, and ingredient category values from the known options below whenever one fits. A value must never contain a comma.
- time_min is the total time in minutes including resting, marinating, and chilling. An overnight rest is at least 480 minutes.
- Write in English. Where a Greek or other non-English ingredient has no honest English equivalent, keep the transliterated term and add a gloss, for example "Anthotyro (Greek whey cheese)". Never substitute an approximate name.
- difficulty is "Easy" unless the recipe needs a technique a home cook would have to practise, in which case it is "Hard".
- Method steps are whole sentences in order.

If the material does not contain a recipe, return empty ingredients and an empty method."""


def _system_prompt(vocab: Vocabulary) -> str:
    return "\n\n".join(
        [
            SYSTEM,
            "Known ingredient names:\n" + ", ".join(sorted(vocab.ingredients)),
            "Known cuisines: " + ", ".join(vocab.cuisines),
            "Known meals: " + ", ".join(vocab.meals),
            "Known ingredient categories: " + ", ".join(vocab.categories),
        ]
    )


@dataclass(frozen=True)
class TextPart:
    text: str


@dataclass(frozen=True)
class ImagePart:
    data: bytes
    media_type: str


Part = TextPart | ImagePart


def text_block(text: str) -> TextPart:
    return TextPart(text)


def image_block(data: bytes, media_type: str) -> ImagePart:
    return ImagePart(data, media_type)


class Backend(Protocol):
    fast: str
    strong: str
    # The exception types this provider raises for a failed call. The escalation
    # loop catches exactly these, so a bug in our own code still propagates
    # instead of buying a second call on the stronger model.
    api_error: tuple[type[BaseException], ...]

    def complete(
        self, model: str, system: str, parts: list[Part]
    ) -> ExtractedRecipe | None: ...

    def is_rate_limited(self, exc: Exception) -> bool: ...


class Extractor:
    def __init__(self, backend: Backend):
        self.backend = backend

    @classmethod
    def from_config(cls, cfg: Config) -> "Extractor":
        # Imported here because backends imports the part types defined above.
        from .backends import backend_from_config

        return cls(backend_from_config(cfg))

    def extract(self, parts: list[Part], vocab: Vocabulary) -> ExtractedRecipe | None:
        system = _system_prompt(vocab)
        models = (self.backend.fast, self.backend.strong)
        for index, model in enumerate(models):
            last = index == len(models) - 1
            try:
                result = self.backend.complete(model, system, parts)
            except self.backend.api_error as exc:
                # Escalating on a rate limit would promote every later message
                # to the expensive model for as long as the limit holds.
                if last or self.backend.is_rate_limited(exc):
                    raise
                continue
            if result and result.ingredients and result.method:
                return result
        return None
