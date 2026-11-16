import json

import pytest
from conftest import decision, evidence, propose, ready

from agent import Agent
from providers import Response
from tools import DisconnectedTools


class ScriptedModel:
    name = "test boundary model"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        response = next(self.responses)
        return response(messages) if callable(response) else response


def action(name, **arguments):
    return Response(message="", action=name, arguments_json=json.dumps(arguments))


def contextual_decision(choice="approve", **extra):
    def respond(messages):
        context = json.loads(messages[-1]["content"].split("\n", 1)[1])
        state = context["state"]
        pending, turn = state["pending"], state["turns"][-1]
        return action(
            "decision",
            turn_id=turn["id"],
            quote=turn["text"],
            target=pending["id"],
            revision=pending["revision"],
            decision=choice,
            **extra,
        )

    return respond


def test_actual_loop_handles_first_day_then_contextual_approval_and_final_outputs(supplied):
    model = ScriptedModel(
        [
            action("propose", dishes=[{"slot_id": "d1-dinner", "name": "Soup"}]),
            contextual_decision(),
        ]
    )
    agent = Agent(model, supplied)
    agent.manager = ready(1, tools=supplied)
    first = agent.reply("Suggest dinner")
    assert "Soup" in first and "draft" in first
    final = agent.reply("כן, מתאים")
    assert agent.manager.complete()
    assert "רשימת קניות" in final and "carrot: 100 g" in final
    assert "fixture:recipe" in final
    assert [name for name, _ in supplied.calls] == ["recipe", "nutrition", "prices"]


def test_multi_day_first_proposal_cannot_expand_to_whole_week():
    manager = ready(3, ("lunch", "dinner"))
    before = manager.state.model_dump()
    with pytest.raises(ValueError):
        manager.apply(
            "propose", {"dishes": [{"slot_id": s.id, "name": "Soup"} for s in manager.state.slots]}
        )
    assert manager.state.model_dump() == before
    propose(manager)
    assert set(manager.state.meals) == {"d1-lunch", "d1-dinner"}


@pytest.mark.parametrize("labels", [("dinner",), ("breakfast", "lunch", "dinner")])
def test_single_meal_and_single_day_do_not_ask_continuation(labels):
    manager = ready(1, labels)
    propose(manager)
    final, stop = decision(manager)
    assert stop and manager.complete()
    assert manager.state.mode is None and "remaining days" not in final


@pytest.mark.parametrize(
    "mode,expected", [("day_by_day", {"d2-dinner"}), ("together", {"d2-dinner", "d3-dinner"})]
)
def test_continuation_mode_controls_scope_and_can_change(mode, expected):
    manager = ready(3)
    propose(manager)
    result, stop = decision(manager)
    assert stop and "one day at a time" in result
    manager.apply("mode", {**evidence(manager, mode), "mode": mode})
    assert manager.proposal_scope() == expected
    manager.apply("mode", {**evidence(manager, "Show the rest together"), "mode": "together"})
    propose(manager)
    assert not manager.state.meals["d2-dinner"].approved
    assert not manager.state.meals["d3-dinner"].approved
    assert manager.state.meals["d1-dinner"].approved


def test_preselected_mode_does_not_ask_again():
    manager = ready(2)
    manager.apply(
        "mode",
        {**evidence(manager, "After the first day show the rest together"), "mode": "together"},
    )
    propose(manager)
    result, stop = decision(manager)
    assert not stop and "one day at a time" not in result


@pytest.mark.parametrize("failure", [False, True])
def test_disconnected_or_failed_tools_preserve_choices_and_return_control(supplied, failure):
    supplied.failure = failure
    tools = supplied if failure else DisconnectedTools()
    model = ScriptedModel(
        [
            action("propose", dishes=[{"slot_id": "d1-dinner", "name": "Soup"}]),
            contextual_decision(),
        ]
    )
    agent = Agent(model, tools)
    agent.manager = ready(1, tools=tools)
    proposal = agent.reply("What about soup?")
    assert "pending integration" in proposal
    final = agent.reply("Yes, choose that")
    assert agent.manager.state.meals["d1-dinner"].approved
    assert "PARTIAL" in final and "Technical verification pending" in final
    assert len(model.calls) == 2
    assert len(supplied.calls) == (1 if failure else 0)


@pytest.mark.parametrize(
    "response",
    [
        action("unknown"),
        Response(message="", action="framework", arguments_json="not-json"),
        action("framework", slots="invalid"),
        action("respond", approved=True),
    ],
)
def test_unknown_and_malformed_actions_leave_state_intact_and_loop_is_bounded(response):
    model = ScriptedModel([response] * 3)
    agent = Agent(model, DisconnectedTools(), max_actions=3)
    result = agent.reply("Plan a meal")
    assert "clarify" in result
    assert len(model.calls) == 3 and not agent.manager.state.meals
    assert not agent.manager.state.slots and not agent.manager.state.approvals


def test_freeform_prose_never_changes_authoritative_state():
    model = ScriptedModel(
        [Response(message="You approved an entire week", action="respond", arguments_json="{}")]
    )
    agent = Agent(model, DisconnectedTools())
    agent.reply("Hello")
    assert not agent.manager.state.meals and not agent.manager.state.approvals


def test_loop_supports_complete_scoped_earlier_meal_delegation():
    model = ScriptedModel(
        [
            action(
                "preference",
                turn_id=4,
                quote="No soup at dinner",
                id="soup",
                kind="dislike",
                text="No soup at dinner",
                terms=["soup"],
                scope={"kind": "meal", "id": "d1-dinner"},
            ),
            contextual_decision("revisit"),
            contextual_decision("delegate"),
            action("propose", dishes=[{"slot_id": "d1-dinner", "name": "Salad"}]),
        ]
    )
    agent = Agent(model, DisconnectedTools())
    agent.manager = ready(1, ("lunch", "dinner"))
    propose(agent.manager)
    decision(agent.manager)
    # ready: initial request + summary approval; menu approval is user turn 3.
    original_version = agent.manager.state.meals["d1-lunch"].version
    revisit = agent.reply("No soup at dinner")
    assert "Should we revisit" in revisit
    selection = agent.reply("Yes, revisit dinner")
    assert "may I choose" in selection
    result = agent.reply("Choose the replacement for dinner")
    assert "Soup → Salad" in result
    assert agent.manager.state.meals["d1-dinner"].approved
    assert agent.manager.state.meals["d1-lunch"].version == original_version
