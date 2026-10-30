import pytest

from app.planner import Plan, PlanInvalid, PlanStep, materialize_step_input, validate_plan
from app.registry import Capability


INPUT = {
    "type": "object",
    "properties": {"rows": {"type": "array"}},
    "required": ["rows"],
}
ROWS = {
    "type": "object",
    "properties": {"rows": {"type": "array"}},
    "required": ["rows"],
}
COUNT = {
    "type": "object",
    "properties": {"count": {"type": "integer"}},
    "required": ["count"],
}


def capability(name, version, input_schema, output_schema, price=5):
    return Capability(
        name=name,
        version=version,
        input_schema=input_schema,
        output_schema=output_schema,
        price_cents=price,
        active=True,
    )


CAPS = {
    ("dedupe", "1.0.0"): capability("dedupe", "1.0.0", INPUT, ROWS),
    ("count_rows", "1.0.0"): capability("count_rows", "1.0.0", ROWS, COUNT, price=7),
}


def test_chain_maps_output_to_next_input_and_prices_it():
    plan = Plan(
        status="ready",
        estimated_price_cents=12,
        steps=[
            PlanStep(capability="dedupe", version="1.0.0", input_mapping={"rows": "$input.rows"}),
            PlanStep(
                capability="count_rows",
                version="1.0.0",
                input_mapping={"rows": "$step.0.output.rows"},
            ),
        ],
    )

    resolved = validate_plan(plan, CAPS, INPUT, inputs={"rows": [1, 1, 2]})

    assert [c.name for c in resolved] == ["dedupe", "count_rows"]
    assert materialize_step_input(plan.steps[1], {}, [{"rows": [1, 2]}]) == {"rows": [1, 2]}


def test_planner_rejects_missing_mapping():
    plan = Plan(
        status="ready",
        steps=[PlanStep(capability="dedupe", version="1.0.0")],
    )

    with pytest.raises(PlanInvalid, match="missing mappings"):
        validate_plan(plan, CAPS, INPUT)


def test_planner_rejects_type_mismatch_between_steps():
    plan = Plan(
        status="ready",
        steps=[
            PlanStep(capability="count_rows", version="1.0.0", input_mapping={"rows": "$input.rows"})
        ],
    )

    wrong_input = {
        "type": "object",
        "properties": {"rows": {"type": "string"}},
        "required": ["rows"],
    }
    with pytest.raises(PlanInvalid, match="type mismatch"):
        validate_plan(plan, CAPS, wrong_input)


def test_planner_rejects_future_step_reference():
    plan = Plan(
        status="ready",
        steps=[
            PlanStep(capability="dedupe", version="1.0.0", input_mapping={"rows": "$step.1.output.rows"}),
            PlanStep(capability="count_rows", version="1.0.0", input_mapping={"rows": "$input.rows"}),
        ],
    )

    with pytest.raises(PlanInvalid, match="future step"):
        validate_plan(plan, CAPS, INPUT)


def test_clarification_plan_cannot_execute():
    plan = Plan(status="needs_clarification", questions=["Which date field should be used?"])

    with pytest.raises(PlanInvalid, match="not executable"):
        validate_plan(plan, CAPS, INPUT)
