"""Plan and validate capability chains before any worker or payment runs.

The LLM planner is allowed to propose a plan, mappings, and questions. It is
not allowed to execute a worker or invent an endpoint. This module validates
the proposal and materializes each step's input from explicit references.

References use a deliberately small language:

* ``$input.<field>`` reads a field from the request inputs.
* ``$step.<index>.output.<field>`` reads a field produced by an earlier step.

Input/output engineering is represented by normal capability steps and these
explicit mappings. A future reshape/normalize capability can therefore be
inserted into the chain and audited like every other paid operation.
"""

import re
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import BaseModel, Field, field_validator

from app.registry import Capability

MAX_PLAN_STEPS = 4
Reference = str


class PlanInvalid(ValueError):
    """The planner proposed a plan that cannot be safely executed."""


class PlanStep(BaseModel):
    capability: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    # target input field -> source reference
    input_mapping: dict[str, Reference] = Field(default_factory=dict)

    @field_validator("input_mapping")
    @classmethod
    def mapping_keys_are_fields(cls, value: dict[str, Reference]) -> dict[str, Reference]:
        for key in value:
            if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", key):
                raise ValueError(f"invalid input field name: {key}")
        return value


class Plan(BaseModel):
    status: Literal["ready", "needs_clarification", "unsupported"]
    steps: list[PlanStep] = Field(default_factory=list)
    estimated_price_cents: int | None = Field(default=None, ge=1)
    assumptions: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    reason: str | None = None


_INPUT_REF = re.compile(r"^\$input\.([a-zA-Z_][a-zA-Z0-9_]*)$")
_STEP_REF = re.compile(r"^\$step\.(\d+)\.output\.([a-zA-Z_][a-zA-Z0-9_]*)$")


def _schema_property(schema: dict, field: str) -> dict | None:
    properties = schema.get("properties", {})
    value = properties.get(field)
    return value if isinstance(value, dict) else None


def _source_schema(
    reference: str,
    input_schema: dict,
    steps: list[PlanStep],
    capabilities: dict[tuple[str, str], Capability],
    current_index: int,
) -> dict:
    match = _INPUT_REF.match(reference)
    if match:
        field = match.group(1)
        schema = _schema_property(input_schema, field)
        if schema is None:
            raise PlanInvalid(f"unknown input reference: {reference}")
        return schema

    match = _STEP_REF.match(reference)
    if not match:
        raise PlanInvalid(f"invalid input reference: {reference}")
    source_index = int(match.group(1))
    field = match.group(2)
    if source_index >= current_index:
        raise PlanInvalid(f"step {current_index} references a future step: {reference}")
    source_step = steps[source_index]
    source_cap = capabilities.get((source_step.capability, source_step.version))
    if source_cap is None:
        raise PlanInvalid(f"unknown capability in reference: {reference}")
    schema = _schema_property(source_cap.output_schema, field)
    if schema is None:
        raise PlanInvalid(f"unknown output reference: {reference}")
    return schema


def _check_mapping_types(target_schema: dict, source_schema: dict, target: str, source: str) -> None:
    target_type = target_schema.get("type")
    source_type = source_schema.get("type")
    if target_type and source_type and target_type != source_type:
        raise PlanInvalid(
            f"type mismatch for {target}: {source} produces {source_type}, needs {target_type}"
        )


def validate_plan(
    plan: Plan,
    capabilities: dict[tuple[str, str], Capability],
    input_schema: dict,
    inputs: dict[str, Any] | None = None,
    max_price_cents: int | None = None,
) -> list[Capability]:
    """Validate an LLM proposal and return its resolved capabilities.

    ``inputs`` is optional so a caller can validate a plan before receiving
    the actual file or payload. When present, the first step's materialized
    input is checked against its JSON schema as well.
    """

    if plan.status != "ready":
        raise PlanInvalid(f"plan is not executable: {plan.status}")
    if not plan.steps:
        raise PlanInvalid("ready plan must contain at least one step")
    if len(plan.steps) > MAX_PLAN_STEPS:
        raise PlanInvalid(f"plan cannot contain more than {MAX_PLAN_STEPS} steps")
    if max_price_cents is not None and plan.estimated_price_cents is not None:
        if plan.estimated_price_cents > max_price_cents:
            raise PlanInvalid("plan exceeds the requested budget")

    resolved: list[Capability] = []
    total_price = 0
    for index, step in enumerate(plan.steps):
        capability = capabilities.get((step.capability, step.version))
        if capability is None or not capability.active:
            raise PlanInvalid(f"capability is unavailable: {step.capability} {step.version}")
        resolved.append(capability)
        total_price += capability.price_cents

        required = set(capability.input_schema.get("required", []))
        missing = required - set(step.input_mapping)
        if missing:
            raise PlanInvalid(f"step {index} is missing mappings for: {sorted(missing)}")

        for target, reference in step.input_mapping.items():
            target_schema = _schema_property(capability.input_schema, target)
            if target_schema is None:
                raise PlanInvalid(f"step {index} maps unknown input field: {target}")
            source_schema = _source_schema(reference, input_schema, plan.steps, capabilities, index)
            _check_mapping_types(target_schema, source_schema, target, reference)

    if plan.estimated_price_cents is not None and plan.estimated_price_cents != total_price:
        raise PlanInvalid(
            f"price estimate {plan.estimated_price_cents} does not match capability total {total_price}"
        )

    if inputs is not None:
        materialized = materialize_step_input(plan.steps[0], inputs, [])
        errors = list(Draft202012Validator(resolved[0].input_schema).iter_errors(materialized))
        if errors:
            message = "; ".join(error.message for error in errors)
            raise PlanInvalid(f"first step input is invalid: {message}")
    return resolved


def _read_reference(reference: str, inputs: dict[str, Any], outputs: list[dict]) -> Any:
    match = _INPUT_REF.match(reference)
    if match:
        return inputs.get(match.group(1))
    match = _STEP_REF.match(reference)
    if match:
        index = int(match.group(1))
        if index >= len(outputs):
            raise PlanInvalid(f"step output is not available: {reference}")
        return outputs[index].get(match.group(2))
    raise PlanInvalid(f"invalid input reference: {reference}")


def materialize_step_input(
    step: PlanStep, inputs: dict[str, Any], previous_outputs: list[dict]
) -> dict[str, Any]:
    """Build a worker input using only the plan's explicit mappings."""

    return {
        target: _read_reference(reference, inputs, previous_outputs)
        for target, reference in step.input_mapping.items()
    }
