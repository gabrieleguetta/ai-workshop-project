"""Data tools for the Nutrition Planner: recipe search, nutrition and price lookup.

Nutrition and prices come from local CSVs (data/), recipes from TheMealDB.
All numbers are estimates. Tool results are plain dicts, ready to json.dumps
into a tool message.
"""

import csv
import difflib
import json
import urllib.parse
import urllib.request
from pathlib import Path

DATA = Path(__file__).parent / "data"
MEALDB = "https://www.themealdb.com/api/json/v1/1/"


def _key(name):
    """Lowercase, and drop plural endings per word, so 'Red Onions' == 'red onion'."""
    words = []
    for w in str(name).lower().split():
        if w.endswith("oes"):
            w = w[:-2]
        elif w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        words.append(w)
    return " ".join(words)


def _load(file):
    with open(DATA / file, newline="", encoding="utf-8") as f:
        return {_key(r["ingredient"]): r for r in csv.DictReader(f)}


NUTRITION = _load("nutrition.csv")
PRICES = _load("prices.csv")
INGREDIENTS = [r["ingredient"] for r in NUTRITION.values()]


def _lookup(table, item):
    """Return (row, None) on a match, or (None, error entry) for unknown/bad items."""
    name = item.get("ingredient", "") if isinstance(item, dict) else ""
    try:
        grams = float(item["grams"])
    except (KeyError, TypeError, ValueError):
        return None, {"ingredient": name, "found": False,
                      "note": "missing or invalid 'grams'; pass a number"}
    row = table.get(_key(name))
    if row:
        return row, None
    # ponytail: no auto fuzzy match ('Chicken Stock' must not become 'Chicken'); suggest instead
    suggestions = [n for n in INGREDIENTS if set(_key(n).split()) <= set(_key(name).split())]
    suggestions += difflib.get_close_matches(name.title(), INGREDIENTS, n=3, cutoff=0.6)
    return None, {"ingredient": name, "grams": grams, "found": False,
                  "note": "not in table: retry with a suggested name, or estimate it yourself and label it as an estimate",
                  "suggestions": list(dict.fromkeys(suggestions))[:3]}


def get_nutrition(items):
    """items: [{"ingredient": str, "grams": number}] -> per-item and total kcal/macros."""
    out, total, missing = [], {"kcal": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}, []
    for item in items:
        row, err = _lookup(NUTRITION, item)
        if err:
            out.append(err)
            missing.append(err["ingredient"])
            continue
        g = float(item["grams"])
        entry = {"ingredient": row["ingredient"], "grams": g, "found": True}
        for k in total:
            entry[k] = round(float(row[k]) * g / 100, 1)
            total[k] += entry[k]
        out.append(entry)
    return {"items": out, "total": {k: round(v, 1) for k, v in total.items()},
            "missing": missing, "note": "estimates; per 100 g raw/dry weight; total excludes missing items"}


def get_prices(items):
    """items: [{"ingredient": str, "grams": number}] -> per-item and total cost in ILS."""
    out, total, missing = [], 0.0, []
    for item in items:
        row, err = _lookup(PRICES, item)
        if err:
            out.append(err)
            missing.append(err["ingredient"])
            continue
        g = float(item["grams"])
        cost = round(float(row["ils_per_kg"]) * g / 1000, 2)
        total += cost
        out.append({"ingredient": row["ingredient"], "grams": g, "found": True, "cost_ils": cost})
    return {"items": out, "total_ils": round(total, 2), "missing": missing,
            "note": "estimated Israeli supermarket prices; total excludes missing items"}


def _get(endpoint, **params):
    url = MEALDB + endpoint + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=10) as r:
        return json.load(r).get("meals") or []


def _recipe(m):
    ingredients = [{"ingredient": m[f"strIngredient{i}"].strip(), "measure": (m[f"strMeasure{i}"] or "").strip()}
                   for i in range(1, 21) if (m.get(f"strIngredient{i}") or "").strip()]
    return {"name": m["strMeal"], "category": m.get("strCategory"), "area": m.get("strArea"),
            "ingredients": ingredients,
            "link": m.get("strSource") or m.get("strYoutube") or f"https://www.themealdb.com/meal/{m['idMeal']}"}


def search_recipes(query, max_results=3):
    """Search TheMealDB by dish name; if nothing matches, by main ingredient."""
    try:
        meals = _get("search.php", s=query)[:max_results]
        if not meals:  # e.g. "chickpeas" -> dishes that use chickpeas
            ids = [m["idMeal"] for m in _get("filter.php", i=query)[:max_results]]
            meals = [_get("lookup.php", i=i)[0] for i in ids]
    except Exception as e:  # network/API failure must not crash the chat
        return {"query": query, "recipes": [], "error": f"recipe search failed: {e}"}
    return {"query": query, "recipes": [_recipe(m) for m in meals],
            **({} if meals else {"note": "no recipes found; try a simpler dish name or one main ingredient"})}


_ITEMS = {"type": "array", "items": {"type": "object", "properties": {
    "ingredient": {"type": "string"}, "grams": {"type": "number"}}, "required": ["ingredient", "grams"]}}

# OpenAI/OpenRouter function-calling schemas for these tools.
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": "search_recipes",
        "description": "Find real recipes on TheMealDB by dish name (e.g. 'shakshuka') or one main ingredient "
                       "(e.g. 'chickpeas'). Returns name, ingredients with measures, and a link.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "get_nutrition",
        "description": "Estimate kcal, protein, carbs and fat for a list of ingredients. You estimate grams "
                       "(raw/dry weight per portion). Known ingredients: " + ", ".join(INGREDIENTS),
        "parameters": {"type": "object", "properties": {"items": _ITEMS}, "required": ["items"]}}},
    {"type": "function", "function": {
        "name": "get_prices",
        "description": "Estimate cost in ILS for a list of ingredients by grams. Same ingredient names as get_nutrition.",
        "parameters": {"type": "object", "properties": {"items": _ITEMS}, "required": ["items"]}}},
]

TOOLS = {"search_recipes": search_recipes, "get_nutrition": get_nutrition, "get_prices": get_prices}


if __name__ == "__main__":
    assert set(NUTRITION) == set(PRICES), set(NUTRITION) ^ set(PRICES)
    n = get_nutrition([{"ingredient": "Chicken Breasts", "grams": 200}, {"ingredient": "rice", "grams": 100},
                       {"ingredient": "Chicken Stock", "grams": 250}, {"ingredient": "Eggs", "grams": "x"}])
    assert n["total"]["kcal"] == 240 + 365 and n["total"]["protein_g"] == 45 + 7.1, n
    assert n["missing"] == ["Chicken Stock", "Eggs"] and "Chicken" in n["items"][2]["suggestions"], n
    p = get_prices([{"ingredient": "Onions", "grams": 500}, {"ingredient": "Unobtainium", "grams": 1}])
    assert p["total_ils"] == 2.5 and p["missing"] == ["Unobtainium"], p
    r = search_recipes("shakshuka")
    assert r.get("error") or r["recipes"][0]["ingredients"], r
    print(json.dumps(search_recipes("chickpeas", 1), indent=1)[:600])
    print("ok")
