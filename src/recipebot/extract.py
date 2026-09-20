import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .llm import image_block, text_block
from .models import Recipe
from .notion import Vocabulary
from .scrape import fetch_html, readable_text, scrape_jsonld
from .social import fetch_social

HOSTS = {
    "instagram.com": "Instagram",
    "tiktok.com": "TikTok",
    "youtube.com": "YouTube",
    "youtu.be": "YouTube",
}

PHOTO_PROMPT = (
    "This image or these images show a recipe. Read every legible word, including "
    "handwriting and on-screen captions, and extract the recipe."
)
SOCIAL_PROMPT = (
    "This is a social media post. The caption follows, and the images are frames "
    "from the video. Extract the recipe from both."
)


def source_for(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    for suffix, name in HOSTS.items():
        if host == suffix or host.endswith("." + suffix):
            return name
    return "Web"


def from_url(url: str, extractor, vocab: Vocabulary, language: str) -> Recipe | None:
    source = source_for(url)
    if source in {"Instagram", "TikTok", "YouTube"}:
        return _from_social(url, source, extractor, vocab, language)

    html = fetch_html(url)
    scraped = scrape_jsonld(html, url)
    if scraped is None:
        body = readable_text(html, url)
        extracted = extractor.extract([text_block(body)], vocab, language)
        if extracted is None:
            return None
        return Recipe.from_extracted(
            extracted, source="Web", source_url=url, source_text=body
        )

    prompt = scraped.as_prompt()
    extracted = extractor.extract([text_block(prompt)], vocab, language)
    if extracted is None:
        return None
    recipe = Recipe.from_extracted(
        extracted,
        source="Web",
        source_url=url,
        image_url=scraped.image_url,
        source_text=prompt,
    )
    # The scraper read these from structured data, so they beat the model.
    recipe.name = scraped.name or recipe.name
    # schema.org totalTime conventionally excludes an unattended rest, so take
    # whichever of the two is larger rather than letting the scraper always win.
    recipe.time_min = max(scraped.time_min, recipe.time_min)
    recipe.servings = scraped.servings or recipe.servings
    recipe.method = scraped.method or recipe.method
    return recipe


def _from_social(
    url: str, source: str, extractor, vocab: Vocabulary, language: str
) -> Recipe | None:
    with tempfile.TemporaryDirectory() as tmp:
        result = fetch_social(url, Path(tmp))
        images = [(frame, "image/jpeg") for frame in result.frames]
        return from_photo(
            images,
            extractor,
            vocab,
            language,
            source=source,
            source_url=url,
            caption=result.caption,
            image_url=result.thumbnail_url,
            prompt=SOCIAL_PROMPT,
        )


def from_photo(
    images: list[tuple[bytes, str]],
    extractor,
    vocab: Vocabulary,
    language: str,
    *,
    source: str = "Photo",
    source_url: str = "",
    caption: str = "",
    image_url: str = "",
    prompt: str = PHOTO_PROMPT,
) -> Recipe | None:
    blocks = [image_block(data, media_type) for data, media_type in images]
    blocks.append(text_block(f"{prompt}\n\n{caption}".strip()))
    extracted = extractor.extract(blocks, vocab, language)
    if extracted is None:
        return None
    return Recipe.from_extracted(
        extracted,
        source=source,
        source_url=source_url,
        image_url=image_url,
        source_text=caption,
    )


def from_text(
    text: str,
    extractor,
    vocab: Vocabulary,
    language: str,
    *,
    source: str = "Text",
    source_url: str = "",
) -> Recipe | None:
    extracted = extractor.extract([text_block(text)], vocab, language)
    if extracted is None:
        return None
    return Recipe.from_extracted(
        extracted, source=source, source_url=source_url, source_text=text
    )
