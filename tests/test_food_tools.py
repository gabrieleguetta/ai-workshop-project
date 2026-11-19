"""Gabriele's adapters: name matching, measures, TheMealDB mapping, estimates. No network."""

import pytest

import food_tools as ft
from models import RecipeResult, Requirements, Slot
from tools import MealDataRequest, RecipeQuery, build_tools, scale_recipe


def meal(id_="1", name="Shakshuka", **ingredients):
    m = {"idMeal": id_, "strMeal": name, "strInstructions": "Fry.\r\n\r\nServe.", "strSource": ""}
    for i, (ing, measure) in enumerate(ingredients.items(), 1):
        m[f"strIngredient{i}"], m[f"strMeasure{i}"] = ing.replace("_", " "), measure
    return m


SHAKSHUKA = meal(Olive_Oil="1 tbs", Red_Onions="2 chopped", Eggs="4", Cherry_Tomatoes="800g")


def query(dish="shakshuka", **req):
    slot = Slot(id="mon-dinner", day="Monday", label="Dinner", diners=2)
    return RecipeQuery(
        dish=dish, slot=slot, requirements=Requirements(**req), scoped_preferences=[]
    )


def test_build_tools_registers_real_adapter():
    assert isinstance(build_tools(), ft.FoodTools)


def test_tables_line_up():
    assert all(food["ils_per_kg"] >= 0 for food in ft.FOODS.values())


@pytest.mark.parametrize(
    "name, expected",
    [
        ("Red Onions", "Onion"),
        ("Cherry Tomatoes", "Tomato"),
        ("plain flour", "Flour"),
        ("Chicken Stock", "Chicken Stock"),  # never collapses to the meat
        ("Vegetable Stock Cube", "Vegetable Stock"),
        ("Peanut oil", "Peanut Oil"),  # keeps its peanut allergen
        ("Unobtainium", None),
    ],
)
def test_resolve(name, expected):
    food = ft.resolve(name)
    assert (food["name"] if food else None) == expected


@pytest.mark.parametrize(
    "measure, unit_g, expected",
    [
        ("800g", None, (800, "g", 800)),
        ("1 1/2 cups", None, (1.5, "cup", 360)),
        ("½ tsp", None, (0.5, "tsp", 2.5)),
        ("2-3 tbsp", None, (2, "tbsp", 30)),
        ("2 chopped", 150, (2, "each", 300)),
        ("2 cloves minced", 5, (2, "clove", 10)),
        ("juice of 1", 100, (1, "each", 30)),
        ("to taste", None, (None, "to taste", 1)),
        ("chopped", None, (None, "chopped", None)),
    ],
)
def test_parse_measure(measure, unit_g, expected):
    assert ft.parse_measure(measure, unit_g) == expected


def test_mealdb_mapping():
    recipe = ft.to_recipe(SHAKSHUKA)
    assert recipe.id == "themealdb:1" and recipe.servings == ft.ASSUMED_SERVINGS
    assert recipe.instructions == ["Fry.", "Serve."]
    assert recipe.source.reference == "https://www.themealdb.com/meal/1"
    eggs = recipe.ingredients[2]
    assert (eggs.ingredient_id, eggs.quantity, eggs.unit, eggs.grams) == (
        "food:egg",
        4,
        "each",
        200,
    )
    assert eggs.allergens == ["eggs"] and eggs.estimated_conversion
    assert recipe.allergens_complete and recipe.dietary_tags_complete
    assert "vegetarian" in recipe.dietary_tags and "vegan" not in recipe.dietary_tags


def test_unknown_ingredient_makes_tags_unverified():
    recipe = ft.to_recipe(meal(Eggs="2", Dragonfruit_Foam="1 tbsp"))
    assert not recipe.allergens_complete and recipe.dietary_tags == []
    assert recipe.ingredients[1].ingredient_id is None
    assert recipe.ingredients[1].basis == "unknown"


def test_nutrition_and_prices_use_scaled_grams():
    recipe = scale_recipe(ft.to_recipe(SHAKSHUKA), 2)  # half of the assumed 4 servings
    request = MealDataRequest(recipe=recipe, servings=2)
    n = ft.FoodTools().get_nutrition(request)
    # (15 g oil, 300 g onion, 200 g eggs, 800 g tomato) / 2
    assert n.status == "ok" and n.total and n.total.complete and n.total.estimated
    assert n.total.kcal == round((132.6 + 120 + 286 + 144) / 2)
    p = ft.FoodTools().get_prices(request)
    assert p.status == "ok" and p.total and p.total.currency == "ILS"
    assert p.total.amount == pytest.approx((0.6 + 1.5 + 4 + 5.6) / 2, abs=0.02)


def test_missing_data_gives_null_totals_not_zero():
    recipe = ft.to_recipe(meal(Eggs="2", Dragonfruit_Foam="1 tbsp", Rice="steamed"))
    n = ft.FoodTools().get_nutrition(MealDataRequest(recipe=recipe, servings=4))
    assert n.status == "missing_data" and n.total and n.total.kcal is None
    assert not n.total.complete and len(n.missing) == 2
    assert "0" in n.per_ingredient  # the eggs still have per-ingredient values
    p = ft.FoodTools().get_prices(MealDataRequest(recipe=recipe, servings=4))
    assert p.status == "missing_data" and p.total and p.total.amount is None


def test_search_ranks_conflicts_last(monkeypatch):
    nutty = meal("2", "Nut Shakshuka", Eggs="4", Walnuts="50g")
    monkeypatch.setattr(ft, "_get", lambda endpoint, **_: [nutty, SHAKSHUKA])
    result = ft.FoodTools().search_recipes(query(allergies=["nuts"]))
    assert RecipeResult.model_validate(result).status == "ok"
    assert [r.name for r in result.recipes] == ["Shakshuka", "Nut Shakshuka"]


def test_search_falls_back_to_words_then_ingredient(monkeypatch):
    calls = []

    def fake(endpoint, **params):
        calls.append((endpoint, *params.values()))
        if endpoint == "filter.php":
            return [{"idMeal": "1"}]
        return [SHAKSHUKA] if endpoint == "lookup.php" else []

    monkeypatch.setattr(ft, "_get", fake)
    assert ft.FoodTools().search_recipes(query("chickpea stew")).status == "ok"
    assert calls[0] == ("search.php", "chickpea stew")
    assert ("filter.php", "chickpea_stew") in calls


def test_search_words_rank_by_relevance(monkeypatch):
    eggplant = meal("7", "Grilled eggplant with coconut milk", Eggplant="2")
    chicken = meal("8", "Chicken Salad", Chicken="300g")
    pages = {"grilled": [eggplant], "chicken": [chicken], "salad": [chicken]}
    monkeypatch.setattr(ft, "_get", lambda endpoint, s="", **_: pages.get(s, []))
    result = ft.FoodTools().search_recipes(query("grilled chicken salad"))
    assert [r.name for r in result.recipes] == ["Chicken Salad"]  # "grilled" alone is too weak
    assert ft._score(meal("9", "Lemongrass beef"), ["lemon"]) == 0  # whole words only


def test_search_not_found_and_errors(monkeypatch):
    monkeypatch.setattr(ft, "_get", lambda endpoint, **_: [])
    assert ft.FoodTools().search_recipes(query()).status == "not_found"

    def down(endpoint, **_):
        raise TimeoutError("timed out")

    monkeypatch.setattr(ft, "_get", down)
    result = ft.FoodTools().search_recipes(query())
    assert result.status == "error" and result.recipes == []


def test_mismatched_recipe_does_not_keep_model_description(monkeypatch):
    from conftest import ready

    monkeypatch.setattr(
        ft, "_get", lambda endpoint, **_: [SHAKSHUKA] if endpoint == "search.php" else []
    )
    manager = ready(1, tools=ft.FoodTools())
    manager.apply(
        "propose",
        {
            "dishes": [
                {"slot_id": "d1-dinner", "name": "Egg skillet", "description": "Grilled chicken"}
            ]
        },
    )
    meal = manager.state.meals["d1-dinner"]
    assert meal.name == "Shakshuka" and "Egg skillet" in meal.description


def test_negated_model_tags_are_not_conflicts():
    from conftest import ready

    manager = ready(1)
    manager.state.requirements.dislikes = ["mushrooms"]
    dish = {"slot_id": "d1-dinner", "name": "Soup", "tags": ["no mushrooms", "nut-free"]}
    manager.apply("propose", {"dishes": [dish]})
    assert manager.state.meals["d1-dinner"].checks[1].outcome != "fails"
