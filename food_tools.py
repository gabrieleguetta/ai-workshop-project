"""Gabriele's real MealTools: TheMealDB recipes + local nutrition/price tables (data/*.csv).

All nutrition, prices and gram conversions are estimates and are flagged as such.
"""

from __future__ import annotations

import csv
import json
import re
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Optional

from models import (
    Ingredient,
    Nutrition,
    NutritionResult,
    Price,
    PriceResult,
    Recipe,
    RecipeResult,
    Source,
)
from tools import MealDataRequest, RecipeQuery

DATA = Path(__file__).parent / "data"
MEALDB = "https://www.themealdb.com/api/json/v1/1/"
TIMEOUT = 10
# ponytail: TheMealDB has no servings field; assume 4 and flag every number derived from it
ASSUMED_SERVINGS = 4
NUTRITION_SOURCE = Source(name="Nutrition Planner table (data/nutrition.csv, USDA-based)")
PRICE_SOURCE = Source(name="Nutrition Planner estimated Israeli prices (data/prices.csv)")
CONVERSION_SOURCE = Source(name="Nutrition Planner measure conversions (food_tools.py)")

# Words dropped when an ingredient name has no exact/alias match: "Red Onions" -> "onion".
DESCRIPTORS = set(
    "chopped fresh freshly large small medium free-range beaten finely diced sliced minced "
    "ground dried red green yellow white black brown plain extra virgin unsalted salted melted "
    "whole raw king tinned canned baby cherry plum boneless skinless grated crushed cold warm "
    "hot light dark soft sea kosher organic lean thinly roughly peeled".split()
)

# measure word -> (unit, grams per unit); None grams = use the ingredient's unit_g (one item).
UNITS: dict[str, tuple[str, Optional[float]]] = {}
for _words, _unit, _grams in (
    ("g gr gram grams", "g", 1),
    ("kg kilo kilos kilogram kilograms", "kg", 1000),
    ("mg", "mg", 0.001),
    ("ml mls millilitre millilitres milliliter milliliters", "ml", 1),
    ("l litre litres liter liters", "l", 1000),
    ("tsp tsps teaspoon teaspoons", "tsp", 5),
    ("tbsp tbsps tbs tblsp tbls tablespoon tablespoons", "tbsp", 15),
    ("cup cups", "cup", 240),  # ponytail: one volume weight for every food; density table if needed
    ("lb lbs pound pounds", "lb", 454),
    ("oz ounce ounces", "oz", 28.35),
    ("quart quarts", "quart", 946),
    ("can cans tin tins", "can", 400),
    ("pot pots tub tubs", "pot", 150),
    ("pinch pinches", "pinch", 0.5),
    ("dash dashes", "dash", 1),
    ("splash", "splash", 10),
    ("drizzle", "drizzle", 5),
    ("sprinkling sprinking spinkling", "sprinkling", 2),
    ("handful handfuls handfull", "handful", 30),
    ("bunch bunches", "bunch", 30),
    ("sprig sprigs", "sprig", 2),
    ("knob knobs", "knob", 15),
    ("clove cloves", "clove", None),
    ("slice slices", "slice", None),
):
    for _w in _words.split():
        UNITS[_w] = (_unit, _grams)

# Measures without a number: quantity stays unknown, grams get a small estimate.
PHRASES = {
    "taste": 1,
    "garnish": 5,
    "serve": 10,
    "fry": 15,
    "brushing": 5,
    "greasing": 5,
    "dusting": 2,
    "topping": 10,
    "glaze": 10,
}
FRACTIONS = {"½": " 1/2", "¼": " 1/4", "¾": " 3/4", "⅓": " 1/3", "⅔": " 2/3"}
# "1", "1.5", "1/2", "1 1/2"; a range "2-3" keeps its lower bound.
NUMBER = re.compile(r"^(\d+(?:\.\d+)?)(?:\s+(\d+)/(\d+)|/(\d+))?(?:\s*-\s*[\d./]+)?")


def _key(name: str) -> str:
    """Lowercase and singularize each word, so 'Tomatoes' == 'tomato'."""
    words = []
    for w in name.lower().replace(",", " ").split():
        if w.endswith("oes"):
            w = w[:-2]
        elif w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        words.append(w)
    return " ".join(words)


def _load():
    with open(DATA / "prices.csv", newline="", encoding="utf-8") as f:
        prices = {r["ingredient"]: float(r["ils_per_kg"]) for r in csv.DictReader(f)}
    foods, index = {}, {}
    with open(DATA / "nutrition.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name = r["ingredient"]
            foods[name] = {
                "name": name,
                "kcal": float(r["kcal"]),
                "protein_g": float(r["protein_g"]),
                "unit_g": float(r["unit_g"]) if r["unit_g"] else None,
                "tags": [t for t in r["tags"].split("|") if t],
                "ils_per_kg": prices[name],  # KeyError here = prices.csv is missing a row
            }
            for alias in [name, *r["aliases"].split("|")]:
                if alias:
                    index[_key(alias)] = name
    return foods, index


FOODS, INDEX = _load()


def resolve(name: str) -> Optional[dict]:
    """Exact/alias match first, then without descriptor words. No fuzzy guessing:
    'Chicken Stock' must never become 'Chicken'."""
    key = _key(name)
    if key in INDEX:
        return FOODS[INDEX[key]]
    stripped = " ".join(w for w in key.split() if w not in DESCRIPTORS)
    return FOODS[INDEX[stripped]] if stripped in INDEX else None


def _number(text: str) -> tuple[Optional[float], str]:
    m = NUMBER.match(text)
    if not m:
        return None, text
    value = float(m.group(1))
    if m.group(2):
        value += int(m.group(2)) / int(m.group(3))
    elif m.group(4):
        value /= int(m.group(4))
    return value, text[m.end() :].strip()


def parse_measure(
    measure: str, unit_g: Optional[float]
) -> tuple[Optional[float], str, Optional[float]]:
    """'1 1/2 tbsp' -> (1.5, 'tbsp', 22.5). Returns (quantity, unit, estimated grams)."""
    text = (measure or "").strip().lower()
    for char, repl in FRACTIONS.items():
        text = text.replace(char, repl)
    text = text.strip()
    citrus = re.search(r"\b(juice|zest)\b.*?\bof\s+(\d.*)", text)  # "juice of 1" lemon
    if citrus:
        count, _ = _number(citrus.group(2))
        if count is not None:
            return count, "each", count * (30 if citrus.group(1) == "juice" else 2)
    quantity, rest = _number(text)
    token = re.match(r"[a-z]+", rest)
    word = token.group(0) if token else ""
    if word in UNITS:
        unit, grams = UNITS[word]
        quantity = 1 if quantity is None else quantity
        per = grams if grams is not None else unit_g
        return quantity, unit, quantity * per if per is not None else None
    if quantity is not None:
        return quantity, "each", quantity * unit_g if unit_g else None
    for phrase, grams in PHRASES.items():
        if phrase in text:
            return None, text, grams
    # "chopped" parsley, "" lemon: quantity stays unknown, grams estimated as one unit
    return None, text or "unspecified", unit_g


def _ingredient(name: str, measure: str) -> Ingredient:
    food = resolve(name)
    quantity, unit, grams = parse_measure(measure, food["unit_g"] if food else None)
    exact = unit in ("g", "kg", "mg")
    return Ingredient(
        name=name,
        ingredient_id=f"food:{_key(food['name'])}" if food else None,
        quantity=quantity,
        unit=unit,
        basis="raw" if food else "unknown",
        grams=grams,
        conversion_source=None if grams is None or exact else CONVERSION_SOURCE,
        estimated_conversion=grams is not None and not exact,
        allergens=food["tags"] if food else [],
    )


def diet_tags(tags: set[str]) -> list[str]:
    meatless = not tags & {"meat", "fish", "shellfish"}
    rules = {
        "vegetarian": meatless,
        "vegan": meatless and not tags & {"milk", "eggs", "honey"},
        "pescatarian": "meat" not in tags,
        "dairy-free": "milk" not in tags,
        "gluten-free": "gluten" not in tags,
        "nut-free": not tags & {"tree nuts", "peanuts"},
    }
    return [name for name, ok in rules.items() if ok]


def to_recipe(meal: dict) -> Recipe:
    ingredients = [
        _ingredient(meal[f"strIngredient{i}"].strip(), meal.get(f"strMeasure{i}") or "")
        for i in range(1, 21)
        if (meal.get(f"strIngredient{i}") or "").strip()
    ]
    known = all(i.ingredient_id for i in ingredients)
    link = (
        meal.get("strSource")
        or meal.get("strYoutube")
        or f"https://www.themealdb.com/meal/{meal['idMeal']}"
    )
    steps = re.split(r"[\r\n]+", meal.get("strInstructions") or "")
    return Recipe(
        id=f"themealdb:{meal['idMeal']}",
        name=meal["strMeal"],
        servings=ASSUMED_SERVINGS,
        ingredients=ingredients,
        instructions=[s.strip() for s in steps if s.strip()],
        source=Source(name="TheMealDB", reference=link, observed_at=date.today().isoformat()),
        ingredients_complete=bool(ingredients),
        # Tags are only trustworthy when every ingredient is in our table.
        allergens_complete=known,
        dietary_tags=diet_tags({t for i in ingredients for t in i.allergens}) if known else [],
        dietary_tags_complete=known,
    )


def _conflicts(recipe: Recipe, query: RecipeQuery) -> float:
    """Rank helper: how badly a candidate clashes with the user's known constraints."""
    req = query.requirements
    terms = [t.casefold() for t in (req.allergies or []) + req.exclusions + (req.dislikes or [])]
    names = [i.name.casefold() for i in recipe.ingredients]
    names += [a for i in recipe.ingredients for a in i.allergens]
    score: float = sum(1 for t in terms if any(t in n for n in names))
    for diet in req.dietary or []:
        if diet.casefold() not in recipe.dietary_tags:
            score += 1 if recipe.dietary_tags_complete else 0.5
    return score


def _get(endpoint: str, **params: str) -> list[dict]:
    url = MEALDB + endpoint + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        return json.load(response).get("meals") or []


def _search(dish: str) -> list[dict]:
    meals = _get("search.php", s=dish)
    words = sorted((w for w in re.findall(r"[a-zA-Z]+", dish) if len(w) > 3), key=len, reverse=True)
    for word in words[:3]:  # "Chickpea curry with rice" -> try "chickpea", "curry"
        if meals:
            break
        meals = _get("search.php", s=word)
    if not meals:  # dish may be a main ingredient, e.g. "chicken breast"
        ids = [m["idMeal"] for m in _get("filter.php", i=dish.replace(" ", "_"))[:5]]
        meals = [m for i in ids for m in _get("lookup.php", i=i)[:1]]
    return meals[:10]


def _estimate(request: MealDataRequest, field: str):
    """Per-ingredient values for one field ('kcal' or 'ils'), plus what is missing."""
    values: dict[str, tuple[float, Optional[float], bool]] = {}
    missing: list[str] = []
    for index, ing in enumerate(request.recipe.ingredients):
        food = resolve(ing.name)
        if food is None:
            missing.append(f"{ing.name}: not in nutrition/price table")
            continue
        rate = food["kcal"] / 100 if field == "kcal" else food["ils_per_kg"] / 1000
        if ing.grams is None and rate:
            missing.append(f"{ing.name}: quantity unknown")
            continue
        grams = ing.grams or 0  # only zero-rate items (salt, water) reach here without grams
        protein = food["protein_g"] * grams / 100 if field == "kcal" else None
        values[str(index)] = (round(rate * grams, 2), protein, ing.basis == "raw")
    return values, missing


class FoodTools:
    def search_recipes(self, query: RecipeQuery) -> RecipeResult:
        try:
            meals = _search(query.dish)
            # Fewest constraint clashes first, then recipes our tables fully cover.
            recipes = sorted(
                (to_recipe(m) for m in meals),
                key=lambda r: (_conflicts(r, query), not r.allergens_complete),
            )
        except Exception:  # network/API/format failure must never crash the chat
            return RecipeResult(status="error", missing=["recipe search failed"])
        if not recipes:
            return RecipeResult(status="not_found", missing=["no TheMealDB recipe matched"])
        return RecipeResult(status="ok", recipes=recipes[:5])

    def get_nutrition(self, request: MealDataRequest) -> NutritionResult:
        values, missing = _estimate(request, "kcal")
        per = {
            k: Nutrition(
                kcal=kcal,
                protein_g=round(p or 0, 1),
                complete=True,
                basis_verified=raw,
                source=NUTRITION_SOURCE,
                estimated=True,
            )
            for k, (kcal, p, raw) in values.items()
        }
        done = not missing
        total = Nutrition(
            kcal=round(sum(n.kcal or 0 for n in per.values())) if done else None,
            protein_g=round(sum(n.protein_g or 0 for n in per.values()), 1) if done else None,
            complete=done,
            basis_verified=done and all(n.basis_verified for n in per.values()),
            source=NUTRITION_SOURCE,
            estimated=True,
        )
        return NutritionResult(
            status="ok" if done else "missing_data",
            total=total,
            per_ingredient=per,
            missing=missing,
        )

    def get_prices(self, request: MealDataRequest) -> PriceResult:
        values, missing = _estimate(request, "ils")
        unit = "ILS for consumed quantities (table price per kg)"
        per = {
            k: Price(
                amount=ils,
                currency="ILS",
                basis="consumption",
                pricing_unit=unit,
                complete=True,
                source=PRICE_SOURCE,
                estimated=True,
            )
            for k, (ils, _, _) in values.items()
        }
        done = not missing
        total = Price(
            amount=round(sum(p.amount or 0 for p in per.values()), 2) if done else None,
            currency="ILS",
            basis="consumption",
            pricing_unit=unit,
            complete=done,
            source=PRICE_SOURCE,
            estimated=True,
        )
        return PriceResult(
            status="ok" if done else "missing_data",
            total=total,
            per_ingredient=per,
            missing=missing,
        )
