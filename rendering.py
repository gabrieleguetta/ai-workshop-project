"""Terminal outputs derived exclusively from the current application state."""

from __future__ import annotations

from dataclasses import dataclass, field

from checks import daily_checks, nutrition_ready, prices_ready
from models import Check, Scope, Session, Target


def scope_text(session: Session, scope: Scope) -> str:
    if scope.kind == "global":
        return "whole plan"
    if scope.kind == "day":
        return scope.id or ""
    slot = next((s for s in session.slots if s.id == scope.id), None)
    return f"{slot.day} / {slot.label}" if slot else scope.id or ""


def target_text(session: Session, target: Target) -> str:
    interpretation = {
        "maximum": f"up to {target.high}",
        "minimum": f"at least {target.low}",
        "range": f"{target.low}–{target.high}",
        "approximate": f"about {target.low}",
    }
    period = "per day" if target.period == "day" else "per meal"
    per = "per person" if target.per == "person" else "all diners"
    description = f"{target.kind}: {interpretation[target.interpretation]}"
    description += f" {target.unit}; {period}, {per}; {scope_text(session, target.scope)}"
    if target.kind in ("time", "budget"):
        description += "; " + target.basis
    if target.period == "day" and not target.planned_subset_confirmed:
        description += "; " + "planned portion needs agreement"
    if not target.hard:
        description += "; " + "preference"
    return description


def check_text(session: Session, check: Check) -> str:
    return f"{check.name}: {check.outcome.replace('_', ' ')} — {check.detail}"


@dataclass
class ShoppingList:
    items: list[tuple[str, float, str, str]] = field(default_factory=list)
    complete: bool = True
    missing: list[str] = field(default_factory=list)


def shopping_list(session: Session) -> ShoppingList:
    result = ShoppingList()
    quantities: dict[tuple[str, str, str], tuple[str, float]] = {}
    units = {
        "kg": ("g", 1000),
        "g": ("g", 1),
        "mg": ("g", 0.001),
        "l": ("ml", 1000),
        "ml": ("ml", 1),
    }
    for slot in session.slots:
        meal = session.meals.get(slot.id)
        if meal is None or meal.recipe is None:
            result.complete = False
            result.missing.append(slot.id)
            continue
        if not meal.recipe.ingredients_complete or meal.recipe_status != "ok":
            result.complete = False
            result.missing.append(slot.id)
        for index, ingredient in enumerate(meal.recipe.ingredients):
            if ingredient.quantity is None:
                result.complete = False
                result.missing.append(ingredient.name)
                continue
            unit, factor = units.get(ingredient.unit.casefold(), (ingredient.unit, 1))
            # Unknown identities and unknown raw/cooked bases must not be merged across meals.
            identity = ingredient.ingredient_id
            if not identity or ingredient.basis == "unknown":
                identity = f"{slot.id}:{index}"
            key = (identity, unit, ingredient.basis)
            old = quantities.get(key, (ingredient.name, 0))
            quantities[key] = (old[0], old[1] + ingredient.quantity * factor)
    result.items = [
        (name, amount, unit, basis) for (_, unit, basis), (name, amount) in quantities.items()
    ]
    return result


def summary(session: Session) -> str:
    requirements = session.requirements
    lines = ["Planning summary" + f" v{session.requirements_version}"]
    lines += [
        f"- {s.day} / {s.label}: {s.diners} "
        + "diners"
        + (f"; {s.circumstances}" if s.circumstances else "")
        for s in session.slots
    ]
    for name in ("allergies", "dietary", "dislikes", "exclusions", "cuisines", "favorites"):
        values = getattr(requirements, name)
        lines.append(
            f"- {name}: "
            + (
                ", ".join(values)
                if values
                else ("none specified" if values == [] else "unanswered")
            )
        )
    for target in requirements.targets:
        lines.append("- " + target_text(session, target))

    def yes_no(value):
        return "yes" if value else "no"

    for key in ("repeat_dishes", "leftovers", "batch_cooking"):
        value = getattr(requirements, key)
        if value is not None:
            lines.append(f"- {key}: {yes_no(value)}")
    for pref in session.preferences:
        if pref.active:
            lines.append(f"- {pref.text} ({scope_text(session, pref.scope)})")
    for kind in ("calories", "budget", "time"):
        if not any(t.kind == kind for t in requirements.targets):
            lines.append(f"- {kind}: no target")
    return "\n".join(lines)


def menu(session: Session) -> str:
    lines = ["Current menu" + f" — v{session.revision}"]
    for slot in session.slots:
        meal = session.meals.get(slot.id)
        if meal is None:
            continue
        approved = "approved" if meal.approved else "draft"
        lines.append(
            f"- {slot.day} / {slot.label}: {meal.name} — {slot.diners} "
            + "servings"
            + f" [{approved}; {slot.id} v{meal.version}]"
        )
        if meal.description:
            lines.append("  " + meal.description)
        if meal.recipe:
            recipe = meal.recipe
            times = [
                f"{value:g} " + f"min {kind}"
                for value, kind in (
                    (recipe.active_minutes, "active"),
                    (recipe.total_minutes, "total"),
                )
                if value is not None and recipe.timing_servings == slot.diners
            ]
            lines.append("  " + ("; ".join(times) or "Time unavailable"))
        else:
            lines.append("  " + "Provisional suggestion; recipe and time pending integration")
        nutrition, price = meal.nutrition.total, meal.prices.total
        if (
            nutrition_ready(meal)
            and nutrition
            and nutrition.complete
            and nutrition.basis_verified
            and nutrition.kcal is not None
        ):
            protein = (
                f"; {nutrition.protein_g:g} " + "g protein"
                if nutrition.protein_g is not None
                else ""
            )
            lines.append(
                f"  {nutrition.kcal:g} kcal"
                + protein
                + " / "
                + "all servings"
                + (" (estimate)" if nutrition.estimated else "")
            )
        else:
            lines.append("  " + "Nutrition unavailable/incomplete")
        if prices_ready(meal) and price and price.amount is not None:
            lines.append(
                f"  {price.amount:g} {price.currency} ({price.basis}; "
                f"{price.source.name}; {price.source.observed_at or 'date unknown'})"
                + (" estimate" if price.estimated else "")
            )
        else:
            lines.append("  " + "Cost unavailable/incomplete")
        for check in meal.checks:
            if check.outcome == "fails":
                lines.append("  " + "Conflict" + ": " + check_text(session, check))
        if any(check.outcome == "cannot_verify" for check in meal.checks):
            lines.append("  " + "Technical verification pending")
    for day, checks in daily_checks(session).items():
        for check in checks:
            lines.append(f"  {day}: " + check_text(session, check))
    return "\n".join(lines)


def outputs(session: Session) -> str:
    lines = [menu(session), "", "Recipes"]
    for slot in session.slots:
        meal = session.meals.get(slot.id)
        if meal is None:
            continue
        lines.append(f"{slot.day} / {slot.label}: {meal.name}")
        if meal.recipe is None:
            lines.append("Recipe details pending integration.")
            continue
        recipe = meal.recipe
        lines += [
            f"  - {i.name}: {i.quantity if i.quantity is not None else '?'} {i.unit} ({i.basis})"
            for i in recipe.ingredients
        ]
        lines += [f"  {n}. {step}" for n, step in enumerate(recipe.instructions, 1)]
        if not recipe.instructions or not recipe.ingredients_complete:
            lines.append("Recipe details incomplete")
        lines.append(f"  {recipe.source.name}: {recipe.source.reference or recipe.id}")
    shopping = shopping_list(session)
    lines += [
        "",
        "Shopping list"
        + " — "
        + ("complete" if shopping.complete else "PARTIAL: recipes or quantities missing"),
    ]
    lines += [
        f"- {name}: {amount:g} {unit} ({basis})" for name, amount, unit, basis in shopping.items
    ]
    return "\n".join(lines)
