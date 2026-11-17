"""Gabriele's integration boundary. Default adapters never make external requests."""

from __future__ import annotations

from typing import Protocol

from models import Model, NutritionResult, PriceResult, Recipe, RecipeResult, Requirements, Slot


class RecipeQuery(Model):
    dish: str
    slot: Slot
    requirements: Requirements
    scoped_preferences: list[str]


class MealDataRequest(Model):
    """Recipe quantities have ALREADY been scaled to servings; do not scale again."""

    recipe: Recipe
    servings: int


class MealTools(Protocol):
    def search_recipes(self, query: RecipeQuery) -> RecipeResult: ...

    def get_nutrition(self, request: MealDataRequest) -> NutritionResult: ...

    def get_prices(self, request: MealDataRequest) -> PriceResult: ...


class DisconnectedTools:
    def search_recipes(self, query: RecipeQuery) -> RecipeResult:
        return RecipeResult(status="not_connected")

    def get_nutrition(self, request: MealDataRequest) -> NutritionResult:
        return NutritionResult(status="not_connected")

    def get_prices(self, request: MealDataRequest) -> PriceResult:
        return PriceResult(status="not_connected")


def build_tools() -> MealTools:
    """Register Gabriele's implementation here without changing the conversation engine."""
    return DisconnectedTools()


def scale_recipe(recipe: Recipe, servings: int) -> Recipe:
    scaled = recipe.model_copy(deep=True)
    if scaled.timing_servings is None:
        scaled.timing_servings = recipe.servings
    factor = servings / recipe.servings
    for ingredient in scaled.ingredients:
        if ingredient.quantity is not None:
            ingredient.quantity *= factor
        if ingredient.grams is not None:
            ingredient.grams *= factor
    scaled.servings = servings
    return scaled
