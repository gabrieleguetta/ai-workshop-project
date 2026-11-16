"""Terminal outputs derived exclusively from the current application state."""

from __future__ import annotations

from dataclasses import dataclass, field

from checks import daily_checks, nutrition_ready, prices_ready
from models import Check, Scope, Session, Target


def tr(session: Session, english: str, hebrew: str) -> str:
    return hebrew if session.language == "he" else english


def scope_text(session: Session, scope: Scope) -> str:
    if scope.kind == "global":
        return tr(session, "whole plan", "כל התכנון")
    if scope.kind == "day":
        return scope.id or ""
    slot = next((s for s in session.slots if s.id == scope.id), None)
    return f"{slot.day} / {slot.label}" if slot else scope.id or ""


def target_text(session: Session, target: Target) -> str:
    labels = {"calories": "קלוריות", "budget": "תקציב", "time": "זמן הכנה"}
    interpretation = {
        "maximum": (f"up to {target.high}", f"עד {target.high}"),
        "minimum": (f"at least {target.low}", f"לפחות {target.low}"),
        "range": (f"{target.low}–{target.high}", f"{target.low}–{target.high}"),
        "approximate": (f"about {target.low}", f"בערך {target.low}"),
    }
    period = (
        tr(session, "per day", "ליום")
        if target.period == "day"
        else tr(session, "per meal", "לארוחה")
    )
    per = (
        tr(session, "per person", "לאדם")
        if target.per == "person"
        else tr(session, "all diners", "לכל הסועדים")
    )
    basis = {
        "total": "זמן כולל",
        "active": "זמן עבודה",
        "consumption": "עלות צריכה",
        "checkout": "עלות רכישה",
    }
    description = f"{tr(session, target.kind, labels[target.kind])}: " + tr(
        session, *interpretation[target.interpretation]
    )
    description += f" {target.unit}; {period}, {per}; {scope_text(session, target.scope)}"
    if target.kind in ("time", "budget"):
        description += "; " + tr(session, target.basis, basis[target.basis])
    if target.period == "day" and not target.planned_subset_confirmed:
        description += "; " + tr(
            session, "planned portion needs agreement", "נדרש אישור החלק המתוכנן מתוך היום"
        )
    if not target.hard:
        description += "; " + tr(session, "preference", "העדפה")
    return description


def check_text(session: Session, check: Check) -> str:
    if session.language == "en":
        return f"{check.name}: {check.outcome.replace('_', ' ')} — {check.detail}"
    names = {
        "calories": "קלוריות",
        "budget": "תקציב",
        "time": "זמן הכנה",
        "allergens": "אלרגנים",
        "exclusions": "מרכיבים מוחרגים",
        "dislikes": "העדפות מזון",
        "dietary": "מגבלות תזונה",
        "variety": "גיוון",
    }
    outcomes = {
        "passes": "עומד בדרישה",
        "fails": "אינו עומד בדרישה",
        "cannot_verify": "אין מספיק נתונים לאימות",
    }
    return f"{names.get(check.name, 'בדיקת נתונים')}: {outcomes[check.outcome]}"


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
    lines = [tr(session, "Planning summary", "סיכום התכנון") + f" v{session.requirements_version}"]
    lines += [
        f"- {s.day} / {s.label}: {s.diners} "
        + tr(session, "diners", "סועדים")
        + (f"; {s.circumstances}" if s.circumstances else "")
        for s in session.slots
    ]
    labels = {
        "allergies": "אלרגיות",
        "dietary": "מגבלות תזונה",
        "dislikes": "מזונות לא רצויים",
        "exclusions": "מרכיבים מוחרגים",
        "cuisines": "מטבחים אהובים",
        "favorites": "מנות ומרכיבים אהובים",
    }
    for name, label in labels.items():
        values = getattr(requirements, name)
        lines.append(
            f"- {tr(session, name, label)}: "
            + (
                ", ".join(values)
                if values
                else tr(
                    session,
                    "none specified" if values == [] else "unanswered",
                    "ללא" if values == [] else "טרם נענה",
                )
            )
        )
    for target in requirements.targets:
        lines.append("- " + target_text(session, target))

    def yes_no(value):
        return tr(session, "yes", "כן") if value else tr(session, "no", "לא")

    for key, he in (
        ("repeat_dishes", "חזרה על מנות"),
        ("leftovers", "שאריות"),
        ("batch_cooking", "בישול למספר ארוחות"),
    ):
        value = getattr(requirements, key)
        if value is not None:
            lines.append(f"- {tr(session, key, he)}: {yes_no(value)}")
    for pref in session.preferences:
        if pref.active:
            lines.append(f"- {pref.text} ({scope_text(session, pref.scope)})")
    for kind, he in (("calories", "קלוריות"), ("budget", "תקציב"), ("time", "זמן הכנה")):
        if not any(t.kind == kind for t in requirements.targets):
            lines.append(f"- {tr(session, kind, he)}: " + tr(session, "no target", "ללא יעד"))
    return "\n".join(lines)


def menu(session: Session) -> str:
    lines = [tr(session, "Current menu", "התפריט הנוכחי") + f" — v{session.revision}"]
    for slot in session.slots:
        meal = session.meals.get(slot.id)
        if meal is None:
            continue
        approved = (
            tr(session, "approved", "אושר") if meal.approved else tr(session, "draft", "טיוטה")
        )
        lines.append(
            f"- {slot.day} / {slot.label}: {meal.name} — {slot.diners} "
            + tr(session, "servings", "מנות")
            + f" [{approved}; {slot.id} v{meal.version}]"
        )
        if meal.description:
            lines.append("  " + meal.description)
        if meal.recipe:
            recipe = meal.recipe
            times = [
                f"{value:g} " + tr(session, f"min {kind}", f"דקות {he}")
                for value, kind, he in (
                    (recipe.active_minutes, "active", "עבודה"),
                    (recipe.total_minutes, "total", "בסך הכול"),
                )
                if value is not None and recipe.timing_servings == slot.diners
            ]
            lines.append(
                "  " + ("; ".join(times) or tr(session, "Time unavailable", "זמן הכנה לא זמין"))
            )
        else:
            lines.append(
                "  "
                + tr(
                    session,
                    "Provisional suggestion; recipe and time pending integration",
                    "הצעה ראשונית; מתכון וזמן הכנה ממתינים לחיבור נתונים",
                )
            )
        nutrition, price = meal.nutrition.total, meal.prices.total
        if (
            nutrition_ready(meal)
            and nutrition
            and nutrition.complete
            and nutrition.basis_verified
            and nutrition.kcal is not None
        ):
            protein = (
                f"; {nutrition.protein_g:g} " + tr(session, "g protein", "גרם חלבון")
                if nutrition.protein_g is not None
                else ""
            )
            lines.append(
                f"  {nutrition.kcal:g} kcal"
                + protein
                + " / "
                + tr(session, "all servings", "כל המנות")
                + (tr(session, " (estimate)", " (הערכה)") if nutrition.estimated else "")
            )
        else:
            lines.append(
                "  "
                + tr(session, "Nutrition unavailable/incomplete", "נתוני תזונה חסרים או לא זמינים")
            )
        if prices_ready(meal) and price and price.amount is not None:
            lines.append(
                f"  {price.amount:g} {price.currency} ({price.basis}; "
                f"{price.source.name}; {price.source.observed_at or 'date unknown'})"
                + (tr(session, " estimate", " הערכה") if price.estimated else "")
            )
        else:
            lines.append("  " + tr(session, "Cost unavailable/incomplete", "מחיר חסר או לא זמין"))
        for check in meal.checks:
            if check.outcome == "fails":
                lines.append(
                    "  " + tr(session, "Conflict", "התנגשות") + ": " + check_text(session, check)
                )
        if any(check.outcome == "cannot_verify" for check in meal.checks):
            lines.append(
                "  " + tr(session, "Technical verification pending", "אימות נתונים טרם הושלם")
            )
    for day, checks in daily_checks(session).items():
        for check in checks:
            lines.append(f"  {day}: " + check_text(session, check))
    return "\n".join(lines)


def outputs(session: Session) -> str:
    lines = [menu(session), "", tr(session, "Recipes", "מתכונים")]
    for slot in session.slots:
        meal = session.meals.get(slot.id)
        if meal is None:
            continue
        lines.append(f"{slot.day} / {slot.label}: {meal.name}")
        if meal.recipe is None:
            lines.append(
                tr(
                    session,
                    "Recipe details pending integration.",
                    "פרטי המתכון ממתינים לחיבור נתונים.",
                )
            )
            continue
        recipe = meal.recipe
        lines += [
            f"  - {i.name}: {i.quantity if i.quantity is not None else '?'} {i.unit} ({i.basis})"
            for i in recipe.ingredients
        ]
        lines += [f"  {n}. {step}" for n, step in enumerate(recipe.instructions, 1)]
        if not recipe.instructions or not recipe.ingredients_complete:
            lines.append(tr(session, "Recipe details incomplete", "פרטי המתכון אינם מלאים"))
        lines.append(f"  {recipe.source.name}: {recipe.source.reference or recipe.id}")
    shopping = shopping_list(session)
    lines += [
        "",
        tr(session, "Shopping list", "רשימת קניות")
        + " — "
        + (
            tr(session, "complete", "מלאה")
            if shopping.complete
            else tr(
                session, "PARTIAL: recipes or quantities missing", "חלקית: חסרים מתכונים או כמויות"
            )
        ),
    ]
    lines += [
        f"- {name}: {amount:g} {unit} ({basis})" for name, amount, unit, basis in shopping.items
    ]
    return "\n".join(lines)
