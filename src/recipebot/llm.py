import base64

import anthropic

from .config import Config
from .models import ExtractedRecipe
from .notion import Vocabulary

HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-5"

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


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def image_block(data: bytes, media_type: str) -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode("utf-8"),
        },
    }


class Extractor:
    def __init__(self, client):
        self.client = client

    @classmethod
    def from_config(cls, cfg: Config) -> "Extractor":
        return cls(anthropic.Anthropic(api_key=cfg.anthropic_key))

    def extract(self, blocks: list[dict], vocab: Vocabulary) -> ExtractedRecipe | None:
        system = _system_prompt(vocab)
        models = (HAIKU, SONNET)
        for index, model in enumerate(models):
            last = index == len(models) - 1
            try:
                # Haiku 4.5 rejects output_config.effort with a 400, so never pass it.
                response = self.client.messages.parse(
                    model=model,
                    max_tokens=8000,
                    system=system,
                    messages=[{"role": "user", "content": blocks}],
                    output_format=ExtractedRecipe,
                )
            except anthropic.APIStatusError:
                if last:
                    raise
                continue
            parsed = response.parsed_output
            if parsed and parsed.ingredients and parsed.method:
                return parsed
        return None
