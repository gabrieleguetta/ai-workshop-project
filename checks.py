"""Deterministic checks over available evidence, never model-invented totals."""

from __future__ import annotations

import re
from typing import Literal, Optional

from models import Check, Meal, Scope, Session, Slot, Target


def applies(scope: Scope, slot: Slot) -> bool:
    return scope.kind == "global" or scope.id == (slot.day if scope.kind == "day" else slot.id)


def quantities_complete(meal: Meal) -> bool:
    recipe = meal.recipe
    return bool(
        recipe
        and recipe.ingredients_complete
        and recipe.ingredients
        and all(
            ingredient.quantity is not None or ingredient.grams is not None
            for ingredient in recipe.ingredients
        )
    )


def nutrition_ready(meal: Meal) -> bool:
    data = meal.nutrition.total
    return bool(
        quantities_complete(meal)
        and meal.nutrition.status == "ok"
        and data
        and data.complete
        and data.basis_verified
        and data.kcal is not None
    )


def prices_ready(meal: Meal) -> bool:
    data = meal.prices.total
    return bool(
        quantities_complete(meal)
        and meal.prices.status == "ok"
        and data
        and data.complete
        and data.amount is not None
    )


def target_check(target: Target, value: Optional[float], complete: bool) -> Check:
    if value is None or not complete:
        return Check(
            name=target.kind,
            outcome="cannot_verify",
            hard=target.hard,
            detail="Required evidence, units, basis, or agreed daily allocation is missing",
        )
    low, high = target.low, target.high
    if target.interpretation == "approximate":
        return Check(
            name=target.kind,
            outcome="passes",
            hard=False,
            detail=f"Observed {value:g} {target.unit}; approximate goal {low:g}",
        )
    failed = (low is not None and value < low) or (high is not None and value > high)
    return Check(
        name=target.kind,
        outcome="fails" if failed else "passes",
        hard=target.hard,
        detail=f"Observed {value:g} {target.unit} ({target.period}, {target.per}, {target.basis})",
    )


def measure(meal: Meal, slot: Slot, target: Target) -> tuple[Optional[float], bool]:
    if target.kind == "time":
        if meal.recipe is None or meal.recipe.timing_servings != slot.diners:
            return None, False
        value = (
            meal.recipe.active_minutes if target.basis == "active" else meal.recipe.total_minutes
        )
        return value, value is not None
    if target.kind == "calories":
        nutrition = meal.nutrition.total
        if nutrition is None:
            return None, False
        value = nutrition.kcal
        complete = nutrition_ready(meal)
    else:
        price = meal.prices.total
        if price is None:
            return None, False
        value = price.amount
        complete = (
            prices_ready(meal) and price.currency == target.unit and price.basis == target.basis
        )
    if value is not None and target.per == "person":
        value /= slot.diners
    return value, complete


def selected_targets(session: Session, slot: Slot) -> list[Target]:
    """A meal/day override supersedes a global target of the same kind and period."""
    targets: dict[tuple[str, str], Target] = {}
    for scope_kind in ("global", "day", "meal"):
        for target in session.requirements.targets:
            if target.scope.kind == scope_kind and applies(target.scope, slot):
                targets[(target.kind, target.period)] = target
    return list(targets.values())


# "no mushrooms", "without nuts or dairy", "nut-free", "dairy free"
NEGATED = re.compile(r"\b(?:no|without)\s+[\w-]+(?:\s*(?:,|or|and|&)\s*[\w-]+)*|[\w-]+[- ]free\b")


def check_meal(session: Session, meal: Meal, slot: Slot) -> list[Check]:
    recipe = meal.recipe
    results: list[Check] = []
    # Names and provider allergen metadata can establish a conflict, never package safety.
    # Strip only the negated phrase: "gluten-free; contains peanuts" still names peanuts.
    names = [meal.name.casefold()] + [NEGATED.sub(" ", t.casefold()) for t in meal.tags]
    if recipe:
        names += [i.name.casefold() for i in recipe.ingredients]
        names += [a.casefold() for i in recipe.ingredients for a in i.allergens]
    exclusions = list(session.requirements.exclusions)
    dislikes = list(session.requirements.dislikes or [])
    allergies = list(session.requirements.allergies or [])
    dietary = list(session.requirements.dietary or [])
    for pref in session.preferences:
        if not pref.active or not applies(pref.scope, slot):
            continue
        terms = pref.terms or [pref.text]
        if pref.kind == "allergy":
            allergies += terms
        elif pref.kind == "dietary":
            dietary += terms
        elif pref.kind == "exclude":
            exclusions += terms
        elif pref.kind == "dislike":
            dislikes += terms
    for name, terms, complete, hard in (
        ("exclusions", exclusions, bool(recipe and recipe.ingredients_complete), True),
        ("dislikes", dislikes, bool(recipe and recipe.ingredients_complete), False),
        ("allergens", allergies, bool(recipe and recipe.allergens_complete), True),
    ):
        found = [term for term in terms if any(term.casefold() in n for n in names)]
        outcome: Literal["passes", "fails", "cannot_verify"] = (
            "fails" if found else ("passes" if not terms or complete else "cannot_verify")
        )
        results.append(
            Check(
                name=name,
                outcome=outcome,
                hard=hard,
                detail="Conflicts: " + ", ".join(found)
                if found
                else "No known ingredient conflict"
                if outcome == "passes"
                else "Complete ingredient/allergen evidence is missing",
            )
        )
    if session.requirements.allergies is None:
        results.append(
            Check(
                name="allergy_answer",
                outcome="cannot_verify",
                detail="Allergy question is unanswered",
            )
        )
    if allergies:
        results.append(
            Check(
                name="package_allergy_safety",
                outcome="cannot_verify",
                detail="Generic recipes cannot verify labels or cross-contamination",
            )
        )
    if dietary:
        tags = [tag.casefold() for tag in recipe.dietary_tags] if recipe else []
        known = bool(recipe and recipe.dietary_tags_complete)
        results.append(
            Check(
                name="dietary",
                outcome=("passes" if all(t.casefold() in tags for t in dietary) else "fails")
                if known
                else "cannot_verify",
                detail="Dietary provider evidence" if known else "Dietary evidence missing",
            )
        )
    for target in selected_targets(session, slot):
        if target.period == "meal":
            value, complete = measure(meal, slot, target)
            results.append(target_check(target, value, complete))
    if session.requirements.repeat_dishes is False:
        repeated = any(
            m.slot_id != meal.slot_id and m.name.casefold() == meal.name.casefold()
            for m in session.meals.values()
        )
        results.append(
            Check(
                name="variety",
                outcome="fails" if repeated else "passes",
                detail="User requested no repeated dishes",
                hard=False,
            )
        )
    for name, available in (
        (
            "recipe",
            bool(
                recipe is not None
                and meal.recipe_status == "ok"
                and recipe.ingredients_complete
                and recipe.instructions
            ),
        ),
        (
            "nutrition",
            nutrition_ready(meal),
        ),
        (
            "price",
            prices_ready(meal),
        ),
    ):
        results.append(
            Check(
                name=name,
                outcome="passes" if available else "cannot_verify",
                detail="Evidence supplied" if available else "Data unavailable or incomplete",
            )
        )
    return results


def daily_checks(session: Session) -> dict[str, list[Check]]:
    results: dict[str, list[Check]] = {}
    for day in dict.fromkeys(slot.day for slot in session.slots):
        slots = [slot for slot in session.slots if slot.day == day]
        targets = {
            target.model_dump_json(): target
            for slot in slots
            for target in selected_targets(session, slot)
            if target.period == "day"
        }
        checks = []
        for target in targets.values():
            values = [
                measure(session.meals[s.id], s, target) if s.id in session.meals else (None, False)
                for s in slots
            ]
            complete = target.planned_subset_confirmed and all(
                v is not None and c for v, c in values
            )
            value = sum(v for v, _ in values if v is not None) if complete else None
            checks.append(target_check(target, value, complete))
        results[day] = checks
    return results


def recheck(session: Session) -> None:
    for slot in session.slots:
        if slot.id in session.meals:
            session.meals[slot.id].checks = check_meal(session, session.meals[slot.id], slot)
