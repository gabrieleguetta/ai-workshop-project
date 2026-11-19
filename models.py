"""Validated session and evidence contracts. No session data is persisted."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, validate_assignment=True, allow_inf_nan=False
    )


class Source(Model):
    name: str
    reference: Optional[str] = None
    observed_at: Optional[str] = None


class Ingredient(Model):
    name: str
    ingredient_id: Optional[str] = None
    quantity: Optional[float] = Field(default=None, ge=0)
    unit: str
    basis: Literal["raw", "cooked", "as_supplied", "unknown"] = "unknown"
    grams: Optional[float] = Field(default=None, ge=0)
    conversion_source: Optional[Source] = None
    estimated_conversion: bool = False
    allergens: list[str] = Field(default_factory=list)


class Recipe(Model):
    id: str
    name: str
    servings: float = Field(gt=0)
    ingredients: list[Ingredient]
    instructions: list[str]
    source: Source
    ingredients_complete: bool = False
    allergens_complete: bool = False
    active_minutes: Optional[float] = Field(default=None, ge=0)
    total_minutes: Optional[float] = Field(default=None, ge=0)
    timing_servings: Optional[float] = Field(default=None, gt=0)
    dietary_tags: list[str] = Field(default_factory=list)
    dietary_tags_complete: bool = False


Status = Literal["ok", "not_connected", "missing_data", "not_found", "error"]


class RecipeResult(Model):
    status: Status
    recipes: list[Recipe] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)


class Nutrition(Model):
    """All numbers apply to ALL quantities in the scaled recipe request."""

    kcal: Optional[float] = Field(default=None, ge=0)
    protein_g: Optional[float] = Field(default=None, ge=0)
    complete: bool = False
    basis_verified: bool = False
    source: Source
    estimated: bool = False


class NutritionResult(Model):
    status: Status
    total: Optional[Nutrition] = None
    per_ingredient: dict[str, Nutrition] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)


class Price(Model):
    amount: Optional[float] = Field(default=None, ge=0)
    currency: str = Field(min_length=3, max_length=3)
    basis: Literal["consumption", "checkout"]
    pricing_unit: str
    complete: bool = False
    source: Source
    estimated: bool = False


class PriceResult(Model):
    status: Status
    total: Optional[Price] = None
    per_ingredient: dict[str, Price] = Field(default_factory=dict)
    missing: list[str] = Field(default_factory=list)


class Scope(Model):
    kind: Literal["global", "day", "meal"] = "global"
    id: Optional[str] = None

    @model_validator(mode="after")
    def validate_scope(self):
        if (self.kind == "global") != (self.id is None):
            raise ValueError("A local scope requires an ID; a global scope has no ID")
        return self


class Target(Model):
    kind: Literal["calories", "budget", "time"]
    scope: Scope = Field(default_factory=Scope)
    period: Literal["meal", "day"] = "meal"
    per: Literal["person", "household"] = "person"
    interpretation: Literal["maximum", "minimum", "range", "approximate"]
    low: Optional[float] = Field(default=None, ge=0)
    high: Optional[float] = Field(default=None, ge=0)
    unit: str
    basis: Literal["total", "active", "consumption", "checkout"] = "total"
    hard: bool = True
    # A daily target only covers the explicitly agreed planned subset.
    planned_subset_confirmed: bool = False

    @model_validator(mode="after")
    def validate_target(self):
        if self.interpretation in ("minimum", "range", "approximate") and self.low is None:
            raise ValueError("A lower/approximate value is required")
        if self.interpretation in ("maximum", "range") and self.high is None:
            raise ValueError("An upper value is required")
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError("Target range is reversed")
        if self.kind == "time" and (
            self.unit != "minutes" or self.basis not in ("active", "total")
        ):
            raise ValueError("Time needs minutes and active/total basis")
        if self.kind == "calories" and self.unit != "kcal":
            raise ValueError("Calories need kcal")
        if self.kind == "budget" and (
            len(self.unit) != 3 or self.basis not in ("consumption", "checkout")
        ):
            raise ValueError("Budget needs a currency and consumption/checkout basis")
        if self.scope.kind == "meal" and self.period == "day":
            raise ValueError("A daily target must apply to a day or the whole plan")
        return self


class Slot(Model):
    id: str = Field(min_length=1)
    day: str = Field(min_length=1)
    label: str = Field(min_length=1)
    diners: int = Field(gt=0)
    circumstances: str = ""


class Preference(Model):
    id: str
    kind: Literal["like", "dislike", "exclude", "dietary", "allergy", "other"]
    text: str
    terms: list[str] = Field(default_factory=list)
    scope: Scope
    turn_id: int
    quote: str
    active: bool = True


class Requirements(Model):
    # None means unanswered. [] explicitly confirms no restrictions.
    allergies: Optional[list[str]] = None
    dietary: Optional[list[str]] = None
    dislikes: Optional[list[str]] = None
    exclusions: list[str] = Field(default_factory=list)
    cuisines: Optional[list[str]] = None
    favorites: Optional[list[str]] = None
    time_answered: bool = False
    calories_answered: bool = False
    budget_answered: bool = False
    variety_answered: bool = False
    repeat_dishes: Optional[bool] = None
    leftovers: Optional[bool] = None
    batch_cooking: Optional[bool] = None
    targets: list[Target] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_targets(self):
        keys = [(t.kind, t.period, t.scope.kind, t.scope.id) for t in self.targets]
        if len(keys) != len(set(keys)):
            raise ValueError("Conflicting targets for the same scope need clarification")
        return self

    def missing(self) -> list[str]:
        return [
            key
            for key in ("allergies", "dietary", "dislikes", "cuisines", "favorites")
            if getattr(self, key) is None
        ] + [
            key
            for key in ("time", "calories", "budget", "variety")
            if not getattr(self, key + "_answered")
        ]


class Check(Model):
    name: str
    outcome: Literal["passes", "fails", "cannot_verify"]
    detail: str
    hard: bool = True


class Meal(Model):
    slot_id: str
    name: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    version: int = 1
    source_recipe: Optional[Recipe] = None
    recipe: Optional[Recipe] = None
    recipe_status: Status = "not_connected"
    nutrition: NutritionResult = Field(
        default_factory=lambda: NutritionResult(status="not_connected")
    )
    prices: PriceResult = Field(default_factory=lambda: PriceResult(status="not_connected"))
    checks: list[Check] = Field(default_factory=list)
    approved_version: Optional[int] = None

    @property
    def approved(self) -> bool:
        return self.approved_version == self.version


class Turn(Model):
    id: int
    text: str


class Pending(Model):
    id: str
    kind: Literal["summary", "menu", "revisit", "replacement"]
    revision: int
    created_turn: int
    meals: dict[str, int] = Field(default_factory=dict)


class Approval(Model):
    target: str
    revision: int
    turn_id: int
    quote: str
    decision: str
    meals: dict[str, int] = Field(default_factory=dict)


class Replacement(Model):
    meals: dict[str, int]
    requirements_version: int
    turn_id: int
    choices: dict[str, str] = Field(default_factory=dict)
    delegated: bool = False


class Session(Model):
    stage: str = "collect"
    slots: list[Slot] = Field(default_factory=list)
    requirements: Requirements = Field(default_factory=Requirements)
    preferences: list[Preference] = Field(default_factory=list)
    requirements_version: int = 0
    summary_approved: Optional[int] = None
    revision: int = 0
    meals: dict[str, Meal] = Field(default_factory=dict)
    # Snapshots change only through actual user approval or scoped replacement authority.
    selected_meals: dict[str, Meal] = Field(default_factory=dict)
    turns: list[Turn] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    pending: Optional[Pending] = None
    replacement: Optional[Replacement] = None
    mode: Optional[Literal["day_by_day", "together"]] = None
    calibrated: bool = False
    changes: list[str] = Field(default_factory=list)
    requirement_turns: dict[str, int] = Field(default_factory=dict)
