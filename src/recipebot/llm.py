from dataclasses import dataclass
from typing import Protocol

from .config import Config
from .models import ExtractedRecipe
from .notion import Vocabulary

SYSTEM = """You extract exactly one recipe from the material the user shares.

Rules you must follow:
- Ingredient names are shopping level and singular: what you pick off the shelf, never a plural such as "Chickens". Keep a word that names a different product: "Pork belly", "Ground beef", and "Chicken thigh", never "Pork", "Beef", or "Chicken". Drop a word that only describes the preparation, and put it with the quantity instead: "2 boneless chicken thighs, sliced" is "Chicken thigh" at "2, boneless and sliced".
- Where the recipe already states a metric figure, use that figure and never compute your own: "1 cup (180 g) red lentils" is "180 g", and "1 14 oz can coconut milk or 400 ml" is "400 ml". Convert only what the recipe leaves in another unit. This covers a metric figure alone: always convert an imperial or a US figure, even where the recipe states it plainly.
- Quantities are metric, in the quantity field and inside a method step alike. Convert mass to g, or to kg above 1000 g. Convert volume to ml, or to l above 1000 ml. Convert length to cm and temperature to °C. Convert a cup by what it holds: 1 cup of flour is 120 g, 1 cup of butter is 225 g, 1 cup of a liquid is 240 ml.
- A spoon is already metric, so it is never a unit to convert. Never rewrite tbsp or tsp as ml or as g: "2 tbsp soy sauce" stays "2 tbsp", and "2 tsp brown sugar" stays "2 tsp".
- Round a converted number to what a kitchen measures: 1 lb is 450 g, 8 oz is 225 g, 375°F is 190°C, 1/4 cup of water is 60 ml. Give the converted figure alone and never repeat the source's own imperial or US figure beside it: "1.5 lb" becomes "675 g", never "675 g (1.5 lb)".
- Write a quantity as a number, a space, then a lowercase unit: "200 g", "180 ml", "1.5 tbsp", "190°C". Write a fraction as a decimal. Keep an imprecise amount as the recipe writes it, such as "a pinch" or "to taste".
- Never discard a count the recipe states. Where the recipe counts a whole item that a shop sells by weight, such as a potato, a carrot, an onion or a tomato, write the weight first and that count in parentheses: "4 medium potatoes" becomes "400 g (4 medium)", and "1 whole onion" becomes "150 g (1 medium)", never "1 (150 g)". Give an item that a shop sells by the piece as a count alone and never as a weight: "2" for eggs, "3 cloves" for garlic. A count is the only thing that ever goes in parentheses.
- group is the source's own heading for a block of ingredients, such as "Sauce", "Marinade" or "Dip". Set the same short label on every ingredient under that heading, and drop any trailing colon. Leave it empty when the source gives one undivided list.
- Reuse an ingredient name from the known list below whenever the ingredient matches. Mint a new name only when the ingredient is genuinely new.
- cuisine is an open list, so a dish whose cuisine is missing from the known options below takes a new value. Never file a dish under a neighbouring country because that country is a known option: the cuisine of Vietnamese Shaking Beef is "Vietnamese", never "Chinese".
- Choose meal and ingredient category values from the known options below whenever one fits. A value must never contain a comma.
- meal holds every option the dish fits, not only the best one: a pasta bake is Lunch and Dinner, a brownie is Dessert and Snack, a cake is Dessert alone.
- time_min is the total time in minutes including resting, marinating, and chilling. An overnight rest is at least 480 minutes.
- Write in English. Where a Greek or other non-English ingredient has no honest English equivalent, keep the transliterated term and add a gloss, for example "Anthotyro (Greek whey cheese)". Never substitute an approximate name.
- keeps_days is how many days the finished dish keeps: in the fridge, or at room temperature for a dish that lives in a jar or a tin, such as cookies or roasted nuts. Use the figure the recipe states. Most recipes state none, so estimate from 3 to 4 days for a cooked dish. A pickled or a marinated dish keeps 5 to 7 days. A dish whose texture fails before it spoils, such as rice noodles or anything fried and crisp, keeps 2 to 3 days. Use 0 when the dish has to be eaten straight away.
- difficulty is "Easy" unless the recipe needs a technique a home cook would have to practise, in which case it is "Hard".
- Method steps are whole sentences in order.
- State the amount in the step where an ingredient is first used, so the cook never scrolls back for it: "Add 565 g chicken breast and toss to coat", never "Add the chicken". A later step that uses the same ingredient names it without the amount.
- A method step obeys every quantity rule above, so never copy a measure out of the source unconverted: "Heat oven to 425°F" becomes "Heat the oven to 220°C".
- A step is an instruction, never a heading the source uses to group its steps: drop "How To Make The Stir-Fry Sauce:" and keep the instructions under it.
- notes carry the substitutions, the tips, and the storage or serving advice the source gives outside the method. Write one whole sentence each, and take only what the source states. Return an empty list when the source gives none.
- emoji is exactly one emoji that suits the finished dish, and it becomes the recipe's icon. Prefer the dish itself over an ingredient or a flag. Return an empty string only when no emoji fits.

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
