"""Worker contract: POST /run with {"task_id", "input"} -> {"status", "output", "usage"}.

Input is checked against the capability's input schema BEFORE the worker runs (fail early and cheap).
Worker output is untrusted data: it is only checked against the output schema, never interpreted.
"""

import time
from typing import Callable, Literal

import httpx
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from app.registry import Capability

LOCAL_WORKERS: dict[str, Callable[[dict], dict]] = {}


def local_worker(name: str):
    """Register an in-process worker reachable as endpoint "local:<name>"."""

    def wrap(fn):
        LOCAL_WORKERS[name] = fn
        return fn

    return wrap


class InputInvalid(Exception):
    """The caller's input does not match the capability's input schema."""


class WorkerError(Exception):
    """The worker failed, timed out, or broke the contract."""


class Usage(BaseModel):
    duration_ms: int = 0
    model: str | None = None


class RunResponse(BaseModel):
    status: Literal["done", "pending"] = "done"
    output: dict | None = None
    task_id: str | None = None  # set when status is "pending" (long tasks)
    usage: Usage = Usage()


class RunResult(BaseModel):
    status: Literal["done", "pending"]
    output: dict | None
    task_id: str | None
    usage: Usage
    output_errors: list[str]

    @property
    def schema_ok(self) -> bool:
        return self.status == "done" and not self.output_errors


def _errors(schema: dict, data) -> list[str]:
    return [f"{'/'.join(map(str, e.path)) or '(root)'}: {e.message}" for e in Draft202012Validator(schema).iter_errors(data)]


def check_input(cap: Capability, data: dict) -> None:
    errors = _errors(cap.input_schema, data)
    if errors:
        raise InputInvalid("; ".join(errors))


def run(cap: Capability, data: dict, task_id: str, http: httpx.Client | None = None, timeout: float = 30) -> RunResult:
    check_input(cap, data)
    started = time.monotonic()
    try:
        if cap.endpoint.startswith("local:"):
            fn = LOCAL_WORKERS.get(cap.endpoint.removeprefix("local:"))
            if fn is None:
                raise WorkerError(f"no local worker {cap.endpoint}")
            raw = {"status": "done", "output": fn(data)}
        else:
            client = http or httpx.Client(timeout=timeout)
            r = client.post(f"{cap.endpoint.rstrip('/')}/run", json={"task_id": task_id, "input": data}, timeout=timeout)
            r.raise_for_status()
            raw = r.json()
        resp = RunResponse.model_validate(raw)
    except WorkerError:
        raise
    except Exception as e:  # HTTP error, bad JSON, contract violation, or a crashed local worker
        raise WorkerError(f"{cap.name}: {type(e).__name__}") from e

    if resp.usage.duration_ms == 0:
        resp.usage.duration_ms = int((time.monotonic() - started) * 1000)
    if resp.status == "pending":
        if not resp.task_id:
            raise WorkerError(f"{cap.name}: pending without task_id")
        return RunResult(status="pending", output=None, task_id=resp.task_id, usage=resp.usage, output_errors=[])
    if resp.output is None:
        raise WorkerError(f"{cap.name}: done without output")
    return RunResult(
        status="done",
        output=resp.output,
        task_id=None,
        usage=resp.usage,
        output_errors=_errors(cap.output_schema, resp.output),
    )
