import pytest
from conftest import decision, evidence, propose, ready
from pydantic import ValidationError

from models import Requirements, Scope, Target
from rendering import menu
from state import StateManager, TransitionError
from tools import DisconnectedTools


def test_new_run_has_no_state_or_approval_leakage():
    first = ready(1)
    propose(first)
    decision(first)
    second = StateManager(DisconnectedTools())
    assert second.state.requirements.allergies is None
    assert not second.state.slots and not second.state.meals and not second.state.approvals
    assert not second.state.turns and not second.state.preferences


def test_no_allergies_is_distinct_from_unanswered():
    assert "allergies" in Requirements().missing()
    assert "allergies" not in Requirements(allergies=[]).missing()


def test_missing_answers_prevent_summary_and_proposal():
    manager = StateManager(DisconnectedTools())
    manager.add_turn("Plan dinner")
    with pytest.raises(TransitionError, match="missing answers"):
        manager.apply("summary", {})
    with pytest.raises(TransitionError, match="summary"):
        manager.apply("propose", {"dishes": [{"slot_id": "x", "name": "Soup"}]})
    assert not manager.state.meals


def test_optional_calories_and_repetition_need_no_fixed_rules():
    manager = ready(2)
    assert manager.state.requirements.targets == []
    propose(manager)
    decision(manager)
    manager.apply("mode", {**evidence(manager, "one day at a time"), "mode": "day_by_day"})
    propose(manager)
    assert not any(c.name == "variety" for m in manager.state.meals.values() for c in m.checks)


@pytest.mark.parametrize(
    "scope",
    [None, {"kind": "day", "id": "Day 1"}, {"kind": "meal", "id": "d1-dinner"}, {"kind": "global"}],
)
def test_scoped_preferences_and_ambiguity(scope):
    manager = ready(2)
    result, stop = manager.apply(
        "preference",
        {
            **evidence(manager, "No pasta today"),
            "id": "pasta",
            "kind": "dislike",
            "text": "No pasta",
            "terms": ["pasta"],
            "scope": scope,
        },
    )
    if scope is None:
        assert stop and "generally" in result
        assert not manager.state.preferences
    else:
        assert manager.state.preferences[-1].scope == Scope.model_validate(scope)
        assert manager.state.preferences[-1].turn_id == manager.state.turns[-1].id


def test_invalid_scope_is_transactional():
    manager = ready()
    ev = evidence(manager, "No pasta on Mars")
    before = manager.state.model_dump()
    with pytest.raises(TransitionError):
        manager.apply(
            "preference",
            {
                **ev,
                "id": "p",
                "kind": "dislike",
                "text": "Pasta",
                "scope": {"kind": "day", "id": "Mars"},
            },
        )
    assert manager.state.model_dump() == before


def test_summary_approval_requires_new_actual_user_turn():
    manager = ready()
    ev = evidence(manager, "I like lentils too")
    manager.apply("requirements", {**ev, "answers": {"favorites": ["soup", "lentils"]}})
    manager.apply("summary", {})
    pending = manager.state.pending
    with pytest.raises(TransitionError, match="before this user response"):
        manager.apply(
            "decision",
            {**ev, "target": pending.id, "revision": pending.revision, "decision": "approve"},
        )
    assert manager.state.summary_approved is None
    decision(manager, text="כן, בדיוק")
    assert manager.state.summary_approved == manager.state.requirements_version


@pytest.mark.parametrize(
    "change",
    [
        {"turn_id": 900},
        {"quote": "invented approval"},
        {"revision": -1},
        {"target": "old-target"},
        {"approved": True},
    ],
)
def test_invented_and_stale_approvals_are_rejected(change):
    manager = ready(1)
    propose(manager)
    pending = manager.state.pending
    args = {
        **evidence(manager, "Approve it"),
        "target": pending.id,
        "revision": pending.revision,
        "decision": "approve",
        **change,
    }
    before = manager.state.model_dump()
    with pytest.raises((TransitionError, ValidationError)):
        manager.apply("decision", args)
    assert manager.state.model_dump() == before


def test_stale_menu_revision_cannot_be_approved():
    manager = ready(1)
    propose(manager)
    old = manager.state.pending.model_copy(deep=True)
    manager.add_turn("Try salad")
    propose(manager, {"d1-dinner": "Salad"})
    with pytest.raises(TransitionError):
        manager.apply(
            "decision",
            {
                **evidence(manager, "Approve the old one"),
                "target": old.id,
                "revision": old.revision,
                "decision": "approve",
            },
        )
    assert not manager.state.meals["d1-dinner"].approved


def change_preference(manager, scope=None):
    return manager.apply(
        "preference",
        {
            **evidence(manager, "I dislike soup in general"),
            "id": "soup",
            "kind": "dislike",
            "text": "No soup",
            "terms": ["soup"],
            "scope": scope or {"kind": "global"},
        },
    )


def test_general_change_never_rewrites_earlier_meals_and_refusal_is_respected():
    manager = ready(2)
    propose(manager)
    decision(manager)
    original = manager.state.meals["d1-dinner"].model_copy(deep=True)
    result, stop = change_preference(manager)
    assert stop and "Day 1 / dinner: Soup" in result
    assert manager.state.meals["d1-dinner"].name == original.name
    assert manager.state.meals["d1-dinner"].approved
    decision(manager, "reject", "Leave earlier meals alone")
    assert manager.state.replacement is None and manager.state.pending is None
    assert manager.state.meals["d1-dinner"].name == "Soup"


def test_revisit_is_not_delegation_and_exact_scope_is_listed():
    manager = ready(1, ("lunch", "dinner"))
    propose(manager)
    decision(manager)
    change_preference(manager)
    with pytest.raises(TransitionError):
        decision(manager, "delegate", "Yes")
    result, stop = decision(manager, "revisit", "Yes, revisit")
    assert stop and "Day 1 / lunch: Soup" in result and "Day 1 / dinner: Soup" in result
    assert manager.state.replacement is None
    assert all(m.name == "Soup" for m in manager.state.meals.values())


def test_scoped_delegation_preserves_unaffected_approved_meal():
    manager = ready(1, ("lunch", "dinner"))
    propose(manager)
    decision(manager)
    original_lunch = manager.state.meals["d1-lunch"].model_dump()
    change_preference(manager, {"kind": "meal", "id": "d1-dinner"})
    decision(manager, "revisit", "Yes, revisit dinner")
    decision(manager, "delegate", "Choose the replacement for dinner")
    with pytest.raises(TransitionError):
        manager.apply("propose", {"dishes": [{"slot_id": "d1-lunch", "name": "Salad"}]})
    result, stop = propose(manager, {"d1-dinner": "Salad"})
    assert stop and "Soup → Salad" in result
    assert manager.state.meals["d1-lunch"].model_dump() == original_lunch
    assert manager.state.meals["d1-dinner"].approved
    assert manager.state.approvals[-1].decision == "delegate"


def test_explicit_replacements_apply_only_user_choices():
    manager = ready(1)
    propose(manager)
    decision(manager)
    change_preference(manager)
    decision(manager, "revisit", "Yes")
    decision(manager, "choose", "Replace dinner with Salad", choices={"d1-dinner": "Salad"})
    with pytest.raises(TransitionError):
        propose(manager, {"d1-dinner": "Stew"})
    propose(manager, {"d1-dinner": "Salad"})
    assert manager.state.meals["d1-dinner"].name == "Salad"


def test_new_preference_revokes_unspent_replacement_grant():
    manager = ready(1)
    propose(manager)
    decision(manager)
    change_preference(manager)
    decision(manager, "revisit", "Yes")
    decision(manager, "delegate", "Choose for me")
    assert manager.state.replacement
    manager.apply(
        "preference",
        {
            **evidence(manager, "I now also exclude sugar"),
            "id": "sugar",
            "kind": "exclude",
            "text": "No sugar",
            "terms": ["sugar"],
            "scope": {"kind": "global"},
        },
    )
    assert manager.state.replacement is None
    with pytest.raises(TransitionError):
        manager.apply("propose", {"dishes": [{"slot_id": "d1-dinner", "name": "Salad"}]})


def test_hard_restriction_conflict_remains_after_user_refuses_revision():
    manager = ready(1)
    propose(manager, {"d1-dinner": "Peanut salad"})
    decision(manager)
    manager.apply(
        "requirements",
        {
            **evidence(manager, "Actually I am allergic to peanut"),
            "answers": {"allergies": ["peanut"]},
        },
    )
    result, _ = decision(manager, "reject", "Keep the existing choice")
    checks = manager.state.meals["d1-dinner"].checks
    assert any(c.name == "allergens" and c.outcome == "fails" for c in checks)
    assert "Conflict" in result and "peanut" in result
    assert "Conflict" in menu(manager.state)


def test_serving_correction_recalculates_and_only_revokes_affected_approval(supplied):
    manager = ready(1, ("lunch", "dinner"), supplied)
    propose(manager)
    decision(manager)
    lunch = manager.state.meals["d1-lunch"].model_copy(deep=True)
    slots = [s.model_dump() for s in manager.state.slots]
    slots[1]["diners"] = 4
    manager.apply("framework", {**evidence(manager, "Dinner is now for four"), "slots": slots})
    dinner = manager.state.meals["d1-dinner"]
    assert dinner.recipe.ingredients[0].quantity == 200
    assert dinner.source_recipe.servings == 2
    assert dinner.source_recipe.ingredients[0].quantity == 100
    assert dinner.nutrition.total.kcal == 400 and dinner.prices.total.amount == 20
    assert not dinner.approved and manager.state.meals["d1-lunch"].approved
    assert manager.state.meals["d1-lunch"].version == lunch.version


def test_unchanged_answers_preserve_approvals_and_pending_decisions():
    manager = ready(1)
    propose(manager)
    pending = manager.state.pending.model_dump()
    version = manager.state.requirements_version
    manager.apply(
        "requirements", {**evidence(manager, "Still no allergies"), "answers": {"allergies": []}}
    )
    assert manager.state.pending.model_dump() == pending
    assert manager.state.requirements_version == version
    assert manager.state.summary_approved == version


def test_local_target_change_only_offers_affected_meal_review(supplied):
    manager = ready(1, ("lunch", "dinner"), supplied)
    propose(manager)
    decision(manager)
    manager.apply(
        "requirements",
        {
            **evidence(manager, "Dinner must be under ten minutes"),
            "answers": {
                "targets": [
                    {
                        "kind": "time",
                        "scope": {"kind": "meal", "id": "d1-dinner"},
                        "period": "meal",
                        "interpretation": "maximum",
                        "high": 10,
                        "unit": "minutes",
                        "basis": "total",
                    }
                ]
            },
        },
    )
    assert manager.state.pending.meals == {"d1-dinner": 1}
    assert manager.state.meals["d1-lunch"].approved


def test_known_hard_conflict_is_not_auto_approved_under_delegation():
    manager = ready(1)
    propose(manager)
    decision(manager)
    manager.apply(
        "preference",
        {
            **evidence(manager, "Exclude peanuts"),
            "id": "peanut",
            "kind": "exclude",
            "text": "No peanuts",
            "terms": ["peanut"],
            "scope": {"kind": "global"},
        },
    )
    decision(manager, "revisit", "Revisit dinner")
    decision(manager, "delegate", "Choose its replacement")
    result, stop = propose(manager, {"d1-dinner": "Peanut salad"})
    assert stop and "Conflict" in result
    assert not manager.state.meals["d1-dinner"].approved
    assert manager.state.selected_meals["d1-dinner"].name == "Soup"
    assert manager.state.pending.kind == "menu"


def test_removed_slot_removes_its_local_requirements_but_preserves_other_meals(supplied):
    manager = ready(1, ("lunch", "dinner"), supplied)
    propose(manager)
    decision(manager)
    manager.state.requirements.targets = [
        Target(
            kind="time",
            interpretation="maximum",
            high=20,
            unit="minutes",
            basis="total",
            scope=Scope(kind="meal", id="d1-dinner"),
        )
    ]
    manager.apply(
        "framework",
        {**evidence(manager, "Only lunch now"), "slots": [manager.state.slots[0].model_dump()]},
    )
    assert set(manager.state.meals) == {"d1-lunch"}
    assert set(manager.state.selected_meals) == {"d1-lunch"}
    assert manager.state.meals["d1-lunch"].approved
    assert manager.state.requirements.targets == []
