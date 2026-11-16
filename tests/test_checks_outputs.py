import pytest
from conftest import decision, evidence, propose, ready, recipe
from pydantic import ValidationError

from checks import daily_checks, measure, target_check
from models import Target
from rendering import menu, outputs, shopping_list


def target(kind="budget", **kwargs):
    defaults = {
        "kind": kind,
        "interpretation": "maximum",
        "high": 25,
        "unit": "ILS" if kind == "budget" else "kcal" if kind == "calories" else "minutes",
        "basis": "consumption" if kind == "budget" else "total",
    }
    return Target(**{**defaults, **kwargs})


@pytest.mark.parametrize("per,expected", [("household", 10), ("person", 5)])
def test_household_and_per_person_budget_servings(supplied, per, expected):
    manager = ready(1, tools=supplied)
    propose(manager)
    slot = manager.state.slots[0]
    value, complete = measure(manager.state.meals[slot.id], slot, target(per=per))
    assert complete and value == expected


def test_daily_budget_is_not_converted_to_meal_or_person(supplied):
    manager = ready(1, ("lunch", "dinner"), supplied)
    manager.state.requirements.targets = [
        target(period="day", per="household", high=15, planned_subset_confirmed=True)
    ]
    propose(manager)
    checks = daily_checks(manager.state)["Day 1"]
    assert checks[0].outcome == "fails" and "20 ILS" in checks[0].detail
    assert not any(c.name == "budget" for m in manager.state.meals.values() for c in m.checks)


def test_daily_calories_need_agreed_planned_portion(supplied):
    manager = ready(1, tools=supplied)
    manager.state.requirements.targets = [target("calories", period="day", high=2000)]
    propose(manager)
    assert daily_checks(manager.state)["Day 1"][0].outcome == "cannot_verify"
    manager.state.requirements.targets[0].planned_subset_confirmed = True
    assert daily_checks(manager.state)["Day 1"][0].outcome == "passes"


@pytest.mark.parametrize("data,expected", [(None, "cannot_verify"), (0, "passes"), (30, "fails")])
def test_unknown_is_not_zero_and_known_violations_fail(data, expected):
    assert target_check(target(), data, data is not None).outcome == expected


@pytest.mark.parametrize(
    "kind,args",
    [("time", {"basis": "active"}), ("budget", {"basis": "checkout"}), ("calories", {})],
)
def test_missing_or_wrong_measurement_basis_never_passes(supplied, kind, args):
    manager = ready(1, tools=supplied)
    supplied.recipe_factory = lambda name: recipe(name).model_copy(update={"active_minutes": None})
    propose(manager)
    meal, slot = manager.state.meals["d1-dinner"], manager.state.slots[0]
    if kind == "calories":
        meal.nutrition.total.basis_verified = False
    value, complete = measure(meal, slot, target(kind, **args))
    assert target_check(target(kind, **args), value, complete).outcome == "cannot_verify"


def test_local_target_overrides_global_and_repetition_is_only_checked_when_selected(supplied):
    manager = ready(1, ("lunch", "dinner"), supplied)
    manager.state.requirements.targets = [
        target("time", high=5),
        target("time", high=30, scope={"kind": "meal", "id": "d1-dinner"}),
    ]
    manager.state.requirements.repeat_dishes = False
    propose(manager)
    lunch, dinner = manager.state.meals["d1-lunch"], manager.state.meals["d1-dinner"]
    assert next(c for c in lunch.checks if c.name == "time").outcome == "fails"
    assert next(c for c in dinner.checks if c.name == "time").outcome == "passes"
    assert next(c for c in dinner.checks if c.name == "variety").outcome == "fails"


@pytest.mark.parametrize("status", ["not_connected", "not_found", "missing_data", "error"])
def test_unavailable_results_remain_unverified_and_approval_can_progress(supplied, status):
    supplied.status = status
    manager = ready(1, tools=supplied)
    propose(manager)
    result, stop = decision(manager)
    meal = manager.state.meals["d1-dinner"]
    assert meal.approved and stop
    assert any(c.outcome == "cannot_verify" for c in meal.checks)
    assert "Technical verification pending" in result


def test_partial_totals_are_not_rendered_as_complete(supplied):
    manager = ready(1, tools=supplied)
    propose(manager)
    meal = manager.state.meals["d1-dinner"]
    meal.nutrition.total.complete = False
    meal.prices.total.complete = False
    text = menu(manager.state)
    assert "200 kcal" not in text and "10 ILS" not in text


def test_normalized_compatible_units_and_serving_scaling(supplied):
    manager = ready(1, ("lunch", "dinner"), supplied)
    supplied.recipe_factory = lambda name: recipe(name, quantity=0.1, unit="kg")
    propose(manager)
    dinner = manager.state.meals["d1-dinner"]
    dinner.recipe.ingredients[0].quantity = 100
    dinner.recipe.ingredients[0].unit = "g"
    shopping = shopping_list(manager.state)
    assert shopping.complete and shopping.items == [("carrot", 200, "g", "raw")]


@pytest.mark.parametrize(
    "unit,basis,ingredient_id",
    [
        ("cup", "raw", "carrot"),
        ("g", "cooked", "carrot"),
        ("g", "unknown", "carrot"),
        ("g", "raw", None),
    ],
)
def test_incompatible_units_bases_and_unknown_identity_are_not_guessed(
    supplied, unit, basis, ingredient_id
):
    manager = ready(1, ("lunch", "dinner"), supplied)
    propose(manager)
    ingredient = manager.state.meals["d1-dinner"].recipe.ingredients[0]
    ingredient.unit, ingredient.basis, ingredient.ingredient_id = unit, basis, ingredient_id
    assert len(shopping_list(manager.state).items) == 2


def test_shared_ingredient_survives_replacement_and_outputs_use_same_revision(supplied):
    manager = ready(1, ("lunch", "dinner"), supplied)
    propose(manager)
    decision(manager)
    manager.apply(
        "edit", {**evidence(manager, "Replace dinner with Peas"), "choices": {"d1-dinner": "Peas"}}
    )
    supplied.recipe_factory = lambda name: recipe(name, ingredient_id="peas")
    propose(manager, {"d1-dinner": "Peas"})
    items = shopping_list(manager.state).items
    assert ("carrot", 100, "g", "raw") in items and ("peas", 100, "g", "raw") in items
    text = outputs(manager.state)
    assert f"v{manager.state.revision}" in text and "fixture:recipe" in text
    assert "peas: 100 g" in text and "carrot: 100 g" in text


def test_unknown_quantity_produces_partial_shopping_list(supplied):
    manager = ready(1, tools=supplied)
    supplied.recipe_factory = lambda name: recipe(name, quantity=None)
    propose(manager)
    shopping = shopping_list(manager.state)
    assert not shopping.complete and not shopping.items and shopping.missing == ["carrot"]


def test_disconnected_shopping_list_is_partial():
    manager = ready(1)
    propose(manager)
    assert not shopping_list(manager.state).complete
    assert "PARTIAL" in outputs(manager.state)


def test_recipe_or_quantity_change_replaces_derived_values_and_clears_failed_calculations(supplied):
    manager = ready(1, tools=supplied)
    propose(manager)
    assert manager.state.meals["d1-dinner"].nutrition.total.kcal == 200
    supplied.recipe_factory = lambda name: recipe(name, quantity=50)
    manager.add_turn("Use a smaller recipe")
    propose(manager, {"d1-dinner": "Small soup"})
    assert manager.state.meals["d1-dinner"].nutrition.total.kcal == 100
    assert manager.state.meals["d1-dinner"].prices.total.amount == 5
    supplied.failure = True
    manager.add_turn("Try another")
    propose(manager, {"d1-dinner": "Another soup"})
    assert manager.state.meals["d1-dinner"].nutrition.total is None
    assert manager.state.meals["d1-dinner"].prices.total is None


@pytest.mark.parametrize(
    "kwargs", [{"high": -1}, {"low": 50, "high": 10}, {"unit": "shekels"}, {"basis": "total"}]
)
def test_invalid_budget_targets_rejected(kwargs):
    with pytest.raises(ValidationError):
        target(**kwargs)


def test_approximate_targets_report_value_without_inventing_tolerance():
    result = target_check(
        target("calories", interpretation="approximate", low=500, high=None), 450, True
    )
    assert not result.hard and "450" in result.detail and "500" in result.detail


def test_generic_recipe_cannot_establish_package_allergy_safety(supplied):
    manager = ready(1, tools=supplied)
    manager.state.requirements.allergies = ["nuts"]
    propose(manager)
    check = next(
        c for c in manager.state.meals["d1-dinner"].checks if c.name == "package_allergy_safety"
    )
    assert check.outcome == "cannot_verify"


def test_rescaled_servings_do_not_claim_original_recipe_time_is_verified(supplied):
    manager = ready(1, tools=supplied)
    propose(manager)
    slots = [s.model_dump() for s in manager.state.slots]
    slots[0]["diners"] = 4
    manager.apply("framework", {**evidence(manager, "Four diners now"), "slots": slots})
    meal = manager.state.meals["d1-dinner"]
    time = target("time", high=30)
    value, complete = measure(meal, manager.state.slots[0], time)
    assert value is None and not complete
    assert meal.recipe.total_minutes == 20 and meal.recipe.timing_servings == 2
    assert "20 min total" not in menu(manager.state)


def test_tool_input_mutation_cannot_rewrite_authoritative_requirements(supplied):
    original_search = supplied.search_recipes

    def mutating_search(query):
        query.requirements.allergies = ["invented"]
        query.slot.diners = 99
        return original_search(query)

    supplied.search_recipes = mutating_search
    manager = ready(1, tools=supplied)
    propose(manager)
    assert manager.state.requirements.allergies == []
    assert manager.state.slots[0].diners == 2


def test_conflicting_targets_require_clarification():
    from models import Requirements

    with pytest.raises(ValidationError, match="Conflicting targets"):
        Requirements(targets=[target(high=20), target(high=30)])


def test_provider_cannot_label_partial_recipe_quantities_as_complete_total(supplied):
    manager = ready(1, tools=supplied)
    propose(manager)
    meal = manager.state.meals["d1-dinner"]
    meal.recipe.ingredients_complete = False
    value, complete = measure(meal, manager.state.slots[0], target("calories", high=500))
    assert value == 100 and not complete
    assert "200 kcal" not in menu(manager.state) and "10 ILS" not in menu(manager.state)
