import logging
import re

import httpx
import trafilatura
from pydantic import BaseModel
from recipe_scrapers import scrape_html

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class ScrapeResult(BaseModel):
    name: str
    time_min: int = 0
    servings: int = 0
    ingredients: list[str] = []
    method: list[str] = []
    image_url: str = ""
    cuisine: str = ""

    def as_prompt(self) -> str:
        return "\n".join(
            [
                f"Title: {self.name}",
                f"Total time in minutes: {self.time_min}",
                f"Servings: {self.servings}",
                f"Cuisine hint: {self.cuisine}",
                "Ingredient lines:",
                *(f"- {line}" for line in self.ingredients),
                "Method:",
                *(f"{n}. {step}" for n, step in enumerate(self.method, 1)),
            ]
        )


def fetch_html(url: str) -> str:
    response = httpx.get(
        url, follow_redirects=True, timeout=20.0, headers={"User-Agent": USER_AGENT}
    )
    response.raise_for_status()
    return response.text


def _first_int(value) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group()) if match else 0


# ponytail: any scraper failure means the same thing to us, "no usable structured
# data", and the caller already has a full fallback path. recipe-scrapers raises
# several unrelated exception types across versions, so catch broadly and log.
def scrape_jsonld(html: str, url: str) -> ScrapeResult | None:
    try:
        scraper = scrape_html(html, url)
        ingredients = scraper.ingredients()
        method = scraper.instructions_list()
    except Exception as exc:
        log.info("no structured recipe data for %s: %s", url, exc)
        return None
    if not ingredients or not method:
        return None

    def optional(name: str, default=""):
        try:
            return getattr(scraper, name)() or default
        except Exception:
            return default

    return ScrapeResult(
        name=optional("title") or url,
        time_min=_first_int(optional("total_time", 0)),
        servings=_first_int(optional("yields")),
        ingredients=ingredients,
        method=method,
        image_url=optional("image"),
        cuisine=optional("cuisine"),
    )


def readable_text(html: str, url: str) -> str:
    return (
        trafilatura.extract(
            html, url=url, favor_precision=True, include_comments=False
        )
        or ""
    )
