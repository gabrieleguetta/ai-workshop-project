"""Transactional state transitions and user-turn-bound approval/delegation rules."""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import Field

from checks import applies, daily_checks, recheck
from models import (
    Approval,
    Meal,
    Model,
    NutritionResult,
    Pending,
    Preference,
    PriceResult,
    RecipeResult,
    Replacement,
    Requirements,
    Scope,
    Session,
    Slot,
    Turn,
)
from rendering import menu, outputs, summary, tr
from tools import MealDataRequest, MealTools, RecipeQuery, scale_recipe


class TransitionError(ValueError):
    """An action is not authorized or its target is no longer current."""


class Evidence(Model):
    turn_id: int
    quote: str = Field(min_length=1)


class FrameworkAction(Evidence):
    slots: list[Slot] = Field(min_length=1)


class RequirementsAction(Evidence):
    # Patch keys are validated against Requirements before any state mutation.
    answers: Requirements


class PreferenceAction(Evidence):
    id: str
    kind: Literal["like", "dislike", "exclude", "dietary", "allergy", "other"]
    text: str
    terms: list[str] = Field(default_factory=list)
    scope: Optional[Scope] = None
    active: bool = True


class DecisionAction(Evidence):
    target: str
    revision: int
    decision: Literal["approve", "reject", "revisit", "delegate", "choose"]
    choices: dict[str, str] = Field(default_factory=dict)


class ModeAction(Evidence):
    mode: Literal["day_by_day", "together"]


class Dish(Model):
    slot_id: str
    name: str = Field(min_length=1)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    recipe_id: Optional[str] = None


class ProposalAction(Model):
    dishes: list[Dish] = Field(min_length=1)


class EditAction(Evidence):
    choices: dict[str, str]


ACTION_MODELS: dict[str, type[Model]] = {
    "framework": FrameworkAction,
    "requirements": RequirementsAction,
    "preference": PreferenceAction,
    "decision": DecisionAction,
    "mode": ModeAction,
    "propose": ProposalAction,
    "edit": EditAction,
}


class StateManager:
    def __init__(self, tools: MealTools):
        self.state = Session()
        self.tools = tools

    def add_turn(self, text: str) -> Turn:
        turn = Turn(id=len(self.state.turns) + 1, text=text)
        self.state.turns.append(turn)
        if re.search(r"[\u0590-\u05ff]", text):
            self.state.language = "he"
        return turn

    def evidence(self, action: Evidence) -> None:
        if not self.state.turns or action.turn_id != self.state.turns[-1].id:
            raise TransitionError("Use the actual current user turn")
        if action.quote not in self.state.turns[-1].text or not action.quote.strip():
            raise TransitionError("Evidence must quote the actual user message")

    def validate_scope(self, scope: Scope) -> None:
        valid = [s.day if scope.kind == "day" else s.id for s in self.state.slots]
        if scope.kind != "global" and scope.id not in valid:
            raise TransitionError("Unknown preference/target scope")

    def apply(self, name: str, arguments: dict) -> tuple[str, bool]:
        """Return (user-visible result, must return control to user). Roll back on rejection."""
        before = self.state.model_copy(deep=True)
        try:
            if name == "summary":
                if arguments:
                    raise TransitionError("Summary takes no arguments")
                result = self.confirm_summary()
            elif name in ACTION_MODELS:
                action = ACTION_MODELS[name].model_validate(arguments)
                if isinstance(action, Evidence):
                    self.evidence(action)
                result = getattr(self, "on_" + name)(action)
            else:
                raise TransitionError("Unknown action")
            recheck(self.state)
            self.update_stage()
            return result
        except (ValueError, TypeError, KeyError, AttributeError):
            self.state = before
            raise

    def pending(
        self,
        kind: Literal["summary", "menu", "revisit", "replacement"],
        meals: Optional[dict[str, int]] = None,
    ) -> Pending:
        s = self.state
        p = Pending(
            id=f"{kind}-{s.revision}-{s.requirements_version}-{len(s.approvals)}",
            kind=kind,
            revision=s.requirements_version if kind == "summary" else s.revision,
            created_turn=s.turns[-1].id,
            meals=meals or {},
        )
        s.pending = p
        return p

    def changed_requirements(self, affected: list[str]) -> tuple[str, bool]:
        s = self.state
        s.requirements_version += 1
        s.summary_approved = None
        s.replacement = None
        s.pending = None
        if affected:
            self.pending("revisit", {key: s.meals[key].version for key in affected})
            return self.affected_text(affected) + "\n" + tr(
                s,
                "Should we revisit these earlier meals too? They have not been changed.",
                "האם לעבור גם על הארוחות הקודמות האלה? הן עדיין לא השתנו.",
            ), True
        return "Requirements updated", False

    def on_framework(self, action: FrameworkAction) -> tuple[str, bool]:
        s = self.state
        if s.slots == action.slots:
            return "Framework unchanged", False
        ids = [slot.id for slot in action.slots]
        if len(ids) != len(set(ids)):
            raise TransitionError("Meal IDs must be unique")
        old = {slot.id: slot for slot in s.slots}
        s.slots = action.slots
        s.revision += 1
        for key in list(s.meals):
            if key not in ids:
                del s.meals[key]
                s.selected_meals.pop(key, None)
        for slot in s.slots:
            if slot.id in old and slot.id in s.meals and slot != old[slot.id]:
                meal = s.meals[slot.id]
                meal.version += 1
                meal.approved_version = None
                if meal.recipe:
                    meal.recipe = scale_recipe(meal.source_recipe or meal.recipe, slot.diners)
                self.derived(meal, slot)
        # Explicit removal of a slot also removes its local targets/preferences.
        days = {slot.day for slot in s.slots}

        def retained(scope: Scope) -> bool:
            return scope.kind == "global" or scope.id in (days if scope.kind == "day" else ids)

        s.requirements.targets = [t for t in s.requirements.targets if retained(t.scope)]
        s.preferences = [p for p in s.preferences if retained(p.scope)]
        s.changes.append(f"Framework corrected by user turn {action.turn_id}")
        return self.changed_requirements([])

    def on_requirements(self, action: RequirementsAction) -> tuple[str, bool]:
        s = self.state
        original = s.requirements.model_dump()
        patch = action.answers.model_dump(exclude_unset=True)
        updated = Requirements.model_validate({**original, **patch})
        for target in updated.targets:
            self.validate_scope(target.scope)
        if updated == s.requirements:
            return "Requirements unchanged", False
        changed = {key for key in patch if original[key] != updated.model_dump()[key]}
        changed_targets = [t for t in s.requirements.targets if t not in updated.targets]
        changed_targets += [t for t in updated.targets if t not in s.requirements.targets]
        s.requirements = updated
        for key in patch:
            s.requirement_turns[key] = action.turn_id
        substantive = {key for key in changed if not key.endswith("_answered") and key != "targets"}
        affected = [
            key
            for key in s.meals
            if substantive or any(applies(t.scope, self.slot(key)) for t in changed_targets)
        ]
        return self.changed_requirements(affected)

    def on_preference(self, action: PreferenceAction) -> tuple[str, bool]:
        s = self.state
        if action.scope is None:
            return tr(
                s,
                "Does that apply generally, or just to this day/meal?",
                "זה תקף באופן כללי, או רק ליום או לארוחה הזאת?",
            ), True
        self.validate_scope(action.scope)
        previous = next((p for p in s.preferences if p.id == action.id), None)
        if previous and all(
            getattr(previous, key) == getattr(action, key)
            for key in ("kind", "text", "terms", "scope", "active")
        ):
            return "Preference unchanged", False
        old_scope = previous.scope if previous else None
        s.preferences = [p for p in s.preferences if p.id != action.id]
        s.preferences.append(Preference(**action.model_dump()))
        affected = [
            key
            for key in s.meals
            if applies(action.scope, self.slot(key))
            or (old_scope is not None and applies(old_scope, self.slot(key)))
        ]
        return self.changed_requirements(affected)

    def confirm_summary(self) -> tuple[str, bool]:
        s = self.state
        if s.pending and s.pending.kind in ("revisit", "replacement"):
            raise TransitionError("Resolve the earlier-meal decision first")
        missing = s.requirements.missing()
        if not s.slots or missing:
            raise TransitionError(
                "Collect the framework and missing answers first: " + ", ".join(missing)
            )
        if s.summary_approved == s.requirements_version:
            return "The current summary is already approved", False
        self.pending("summary")
        return summary(s) + "\n" + tr(
            s,
            "Is this correct, or would you like to change anything?",
            "האם הסיכום נכון, או שתרצה לשנות משהו?",
        ), True

    def affected_text(self, keys) -> str:
        s = self.state
        return "\n".join(
            f"- {self.slot(key).day} / {self.slot(key).label}: {s.meals[key].name}" for key in keys
        )

    def on_decision(self, action: DecisionAction) -> tuple[str, bool]:
        s = self.state
        pending = s.pending
        if (
            pending is None
            or action.target != pending.id
            or action.revision != pending.revision
            or action.turn_id <= pending.created_turn
        ):
            raise TransitionError(
                "No matching current decision was shown before this user response"
            )
        if any(
            key not in s.meals or s.meals[key].version != version
            for key, version in pending.meals.items()
        ):
            raise TransitionError("Proposal changed; show the current version before asking again")
        allowed = {
            "summary": {"approve", "reject"},
            "menu": {"approve", "reject"},
            "revisit": {"revisit", "reject"},
            "replacement": {"delegate", "choose", "reject"},
        }
        if action.decision not in allowed[pending.kind]:
            raise TransitionError(
                "Revisiting a meal is separate from delegating replacement selection"
            )
        s.approvals.append(
            Approval(
                target=pending.id,
                revision=pending.revision,
                turn_id=action.turn_id,
                quote=action.quote,
                decision=action.decision,
                meals=pending.meals,
            )
        )
        s.pending = None
        if action.decision == "reject":
            s.replacement = None
            message = tr(
                s,
                "Kept the current choices. What would you like to change or plan next?",
                "הבחירות הקיימות נשמרו. מה תרצה לשנות או לתכנן בהמשך?",
            )
            if pending.kind in ("revisit", "replacement"):
                message += "\n" + (outputs(s) if self.complete() else menu(s))
            return message, True
        if pending.kind == "summary":
            s.summary_approved = s.requirements_version
            return "Summary approved", False
        if pending.kind == "menu":
            for key in pending.meals:
                s.meals[key].approved_version = s.meals[key].version
                s.selected_meals[key] = s.meals[key].model_copy(deep=True)
            s.calibrated = True
            if self.complete():
                return outputs(s), True
            if s.mode is None and len(self.days()) > 1:
                return tr(
                    s,
                    "Continue one day at a time, or see the remaining days together?",
                    "להמשיך יום בכל פעם, או לראות את שאר הימים יחד?",
                ), True
            return "Menu selection approved; technical checks remain separate", False
        if pending.kind == "revisit":
            self.pending("replacement", pending.meals)
            return self.affected_text(pending.meals) + "\n" + tr(
                s,
                "What should replace these meals, or may I choose replacements for this list?",
                "במה להחליף את הארוחות האלה, או שאבחר חלופות לרשימה הזאת?",
            ), True
        if action.decision == "choose":
            if set(action.choices) != set(pending.meals):
                raise TransitionError("Specify replacements for exactly the listed meals")
            if any(
                name.casefold() not in action.quote.casefold() for name in action.choices.values()
            ):
                raise TransitionError(
                    "Replacement choices must come from the user's actual response"
                )
        s.replacement = Replacement(
            meals=pending.meals,
            requirements_version=s.requirements_version,
            turn_id=action.turn_id,
            choices=action.choices,
            delegated=action.decision == "delegate",
        )
        return "Replacement selection authorized for listed meals only", False

    def on_edit(self, action: EditAction) -> tuple[str, bool]:
        if not action.choices or any(key not in self.state.meals for key in action.choices):
            raise TransitionError("An edit must identify existing meals")
        if any(name.casefold() not in action.quote.casefold() for name in action.choices.values()):
            raise TransitionError("Explicit replacement names must be supplied by the user")
        self.state.replacement = Replacement(
            meals={key: self.state.meals[key].version for key in action.choices},
            requirements_version=self.state.requirements_version,
            turn_id=action.turn_id,
            choices=action.choices,
        )
        self.state.pending = None
        return "Explicit user replacements recorded", False

    def on_mode(self, action: ModeAction) -> tuple[str, bool]:
        if len(self.days()) <= 1:
            raise TransitionError("One day/meal does not need a continuation mode")
        self.state.mode = action.mode
        return "Continuation mode updated", False

    def slot(self, key: str) -> Slot:
        for slot in self.state.slots:
            if slot.id == key:
                return slot
        raise TransitionError("Unknown meal ID")

    def days(self) -> list[str]:
        return list(dict.fromkeys(s.day for s in self.state.slots))

    def complete(self) -> bool:
        return bool(self.state.slots) and all(
            slot.id in self.state.meals and self.state.meals[slot.id].approved
            for slot in self.state.slots
        )

    def proposal_scope(self) -> set[str]:
        s = self.state
        if not s.calibrated:
            return {slot.id for slot in s.slots if slot.day == self.days()[0]}
        if len(self.days()) > 1 and s.mode is None:
            raise TransitionError("Ask for the continuation mode")
        remaining = [
            slot for slot in s.slots if slot.id not in s.meals or not s.meals[slot.id].approved
        ]
        if s.mode == "together":
            return {slot.id for slot in remaining}
        return (
            {slot.id for slot in remaining if slot.day == remaining[0].day} if remaining else set()
        )

    def derived(self, meal: Meal, slot: Slot) -> None:
        # Clear stale values before every calculation, including failed tool calls.
        meal.nutrition = NutritionResult(status="missing_data")
        meal.prices = PriceResult(status="missing_data")
        if not meal.recipe:
            meal.nutrition = NutritionResult(
                status=meal.recipe_status
                if meal.recipe_status in ("not_connected", "error")
                else "missing_data"
            )
            meal.prices = PriceResult(status=meal.nutrition.status)
            return
        request = MealDataRequest(recipe=meal.recipe.model_copy(deep=True), servings=slot.diners)
        for name, attr, model in (
            ("get_nutrition", "nutrition", NutritionResult),
            ("get_prices", "prices", PriceResult),
        ):
            try:
                raw = getattr(self.tools, name)(request.model_copy(deep=True))
                value = model.model_validate(raw)
            except Exception:
                value = model(status="error")
            setattr(meal, attr, value)

    def develop(self, dish: Dish) -> Meal:
        s = self.state
        slot = self.slot(dish.slot_id)
        old = s.meals.get(slot.id)
        meal = Meal(
            slot_id=slot.id,
            name=dish.name,
            description=dish.description,
            tags=dish.tags,
            version=old.version + 1 if old else 1,
        )
        query = RecipeQuery(
            dish=dish.name,
            slot=slot,
            requirements=s.requirements,
            scoped_preferences=[
                p.text for p in s.preferences if p.active and applies(p.scope, slot)
            ],
        )
        try:
            result = RecipeResult.model_validate(
                self.tools.search_recipes(query.model_copy(deep=True))
            )
        except Exception:
            result = RecipeResult(status="error")
        meal.recipe_status = result.status
        if result.status in ("ok", "missing_data") and result.recipes:
            candidates = [
                r for r in result.recipes if dish.recipe_id is None or r.id == dish.recipe_id
            ]
            if not candidates:
                raise TransitionError("Selected recipe ID was not returned by the recipe tool")
            meal.source_recipe = candidates[0].model_copy(deep=True)
            meal.recipe = scale_recipe(meal.source_recipe, slot.diners)
            meal.name = meal.recipe.name
        self.derived(meal, slot)
        return meal

    def on_propose(self, action: ProposalAction) -> tuple[str, bool]:
        s = self.state
        keys = {dish.slot_id for dish in action.dishes}
        if len(keys) != len(action.dishes):
            raise TransitionError("Duplicate proposal slots")
        grant = s.replacement
        if grant:
            if (
                grant.requirements_version != s.requirements_version
                or keys != set(grant.meals)
                or any(s.meals[k].version != version for k, version in grant.meals.items())
            ):
                raise TransitionError(
                    "Replacement authorization is stale or outside the listed scope"
                )
            if not grant.delegated and any(
                grant.choices.get(d.slot_id) != d.name for d in action.dishes
            ):
                raise TransitionError("Use the user's explicit replacement choices")
        else:
            if s.summary_approved != s.requirements_version:
                raise TransitionError(
                    "The current requirements summary needs actual user confirmation"
                )
            if s.pending and s.pending.kind != "menu":
                raise TransitionError("Resolve the pending decision first")
            if keys != self.proposal_scope():
                raise TransitionError(
                    "Propose exactly the requested first/next day or agreed remaining group"
                )
            if any(key in s.meals and s.meals[key].approved for key in keys):
                raise TransitionError(
                    "Earlier approved meals require explicit replacement authorization"
                )
        previous = {key: s.meals[key].name for key in keys if key in s.meals}
        was_approved = {key: s.meals[key].approved for key in keys if key in s.meals}
        for dish in action.dishes:
            s.meals[dish.slot_id] = self.develop(dish)
        s.revision += 1
        recheck(s)
        s.replacement = None
        changes = [
            f"{self.slot(key).day} / {self.slot(key).label}: {previous.get(key, '—')} → {s.meals[key].name}"
            for key in sorted(keys)
        ]
        s.changes += changes
        if grant:
            known_conflict = any(
                c.hard and c.outcome == "fails" for key in keys for c in s.meals[key].checks
            )
            known_conflict = known_conflict or any(
                c.hard and c.outcome == "fails"
                for day, day_checks in daily_checks(s).items()
                if day in {self.slot(k).day for k in keys}
                for c in day_checks
            )
            for key in keys:
                if was_approved.get(key) and not known_conflict:
                    s.meals[key].approved_version = s.meals[key].version
                    s.selected_meals[key] = s.meals[key].model_copy(deep=True)
            s.approvals.append(
                Approval(
                    target="replacement",
                    revision=s.revision,
                    turn_id=grant.turn_id,
                    quote=s.turns[grant.turn_id - 1].text,
                    decision="delegate" if grant.delegated else "choose",
                    meals={key: s.meals[key].version for key in keys},
                )
            )
            if all(s.meals[key].approved for key in keys):
                return "\n".join(changes) + "\n" + (
                    outputs(s) if self.complete() else menu(s)
                ), True
        self.pending("menu", {key: s.meals[key].version for key in keys})
        return "\n".join(changes) + "\n" + menu(s) + "\n" + tr(
            s,
            "What suits you, and what would you change? You can approve this proposal.",
            "מה מתאים לך ומה תרצה לשנות? אפשר לאשר את ההצעה הזאת.",
        ), True

    def update_stage(self) -> None:
        s = self.state
        if s.pending:
            s.stage = s.pending.kind
        elif s.requirements.missing() or not s.slots:
            s.stage = "collect"
        elif s.summary_approved != s.requirements_version:
            s.stage = "confirm_summary"
        elif self.complete():
            s.stage = "complete"
        elif s.calibrated and len(self.days()) > 1 and s.mode is None:
            s.stage = "continuation"
        else:
            s.stage = "planning"


def action_contracts() -> dict:
    return {key: model.model_json_schema() for key, model in ACTION_MODELS.items()}
