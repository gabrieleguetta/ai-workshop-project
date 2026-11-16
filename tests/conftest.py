"""Small deterministic boundary fixtures; these are not real end-to-end integrations."""

import pytest

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
from state import StateManager
from tools import DisconnectedTools

SOURCE = Source(name="unit-test source", reference="fixture:recipe", observed_at="2026-01-01")


def recipe(name="Soup", quantity=100, unit="g", ingredient_id="carrot", basis="raw"):
    return Recipe(
        id=name,
        name=name,
        servings=2,
        ingredients=[
            Ingredient(
                name=ingredient_id or "vegetable",
                ingredient_id=ingredient_id,
                quantity=quantity,
                unit=unit,
                basis=basis,
            )
        ],
        instructions=["Prepare the supplied ingredients."],
        source=SOURCE,
        ingredients_complete=True,
        allergens_complete=True,
        active_minutes=10,
        total_minutes=20,
    )


class SuppliedTools:
    def __init__(self):
        self.calls = []
        self.recipe_factory = recipe
        self.status = "ok"
        self.failure = False

    def search_recipes(self, query):
        self.calls.append(("recipe", query))
        if self.failure:
            raise RuntimeError("private failure")
        return RecipeResult(status=self.status, recipes=[self.recipe_factory(query.dish)])

    def get_nutrition(self, request):
        self.calls.append(("nutrition", request))
        if self.failure:
            raise RuntimeError("private failure")
        quantity = request.recipe.ingredients[0].quantity
        return NutritionResult(
            status=self.status,
            total=Nutrition(
                kcal=None if quantity is None else quantity * 2,
                complete=self.status == "ok",
                basis_verified=True,
                protein_g=10,
                source=SOURCE,
            ),
        )

    def get_prices(self, request):
        self.calls.append(("prices", request))
        if self.failure:
            raise RuntimeError("private failure")
        quantity = request.recipe.ingredients[0].quantity
        return PriceResult(
            status=self.status,
            total=Price(
                amount=None if quantity is None else quantity / 10,
                currency="ILS",
                basis="consumption",
                pricing_unit="supplied quantities",
                complete=self.status == "ok",
                source=SOURCE,
            ),
        )


def evidence(manager, text):
    turn = manager.add_turn(text)
    return {"turn_id": turn.id, "quote": text}


def decision(manager, choice="approve", text="That works for me", **extra):
    pending = manager.state.pending
    return manager.apply(
        "decision",
        {
            **evidence(manager, text),
            "target": pending.id,
            "revision": pending.revision,
            "decision": choice,
            **extra,
        },
    )


def propose(manager, names=None):
    keys = (
        manager.state.replacement.meals if manager.state.replacement else manager.proposal_scope()
    )
    return manager.apply(
        "propose",
        {
            "dishes": [
                {"slot_id": key, "name": (names or {}).get(key, "Soup")} for key in sorted(keys)
            ]
        },
    )


def ready(days=2, labels=("dinner",), tools=None):
    manager = StateManager(tools or DisconnectedTools())
    ev = evidence(
        manager,
        "Plan these days for two; no allergies or restrictions, I like soup. "
        "No calorie, budget or time target. Repetition is welcome.",
    )
    slots = [
        {"id": f"d{d}-{label}", "day": f"Day {d}", "label": label, "diners": 2}
        for d in range(1, days + 1)
        for label in labels
    ]
    manager.apply("framework", {**ev, "slots": slots})
    manager.apply(
        "requirements",
        {
            **ev,
            "answers": {
                "allergies": [],
                "dietary": [],
                "dislikes": [],
                "cuisines": [],
                "favorites": ["soup"],
                "time_answered": True,
                "calories_answered": True,
                "budget_answered": True,
                "variety_answered": True,
                "repeat_dishes": True,
            },
        },
    )
    manager.apply("summary", {})
    decision(manager)
    return manager


@pytest.fixture
def supplied():
    return SuppliedTools()
