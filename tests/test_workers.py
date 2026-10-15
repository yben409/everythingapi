import httpx
import pytest

from app.registry import Capability
from app.workers import InputInvalid, WorkerError, local_worker, run

IN = {"type": "object", "properties": {"rows": {"type": "array"}}, "required": ["rows"]}
OUT = {"type": "object", "properties": {"count": {"type": "integer"}}, "required": ["count"]}


def cap(endpoint="local:count_rows"):
    return Capability(name="count_rows", version="1.0.0", input_schema=IN, output_schema=OUT, endpoint=endpoint)


calls = []


@local_worker("count_rows")
def count_rows(data):
    calls.append(data)
    return {"count": len(data["rows"])}


@local_worker("wrong_output")
def wrong_output(data):
    return {"count": "three"}


@local_worker("crashes")
def crashes(data):
    raise RuntimeError("boom")


def http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_local_worker_ok():
    r = run(cap(), {"rows": [1, 2, 3]}, task_id="t1")
    assert r.schema_ok and r.output == {"count": 3}


def test_bad_input_never_reaches_worker():
    calls.clear()
    with pytest.raises(InputInvalid):
        run(cap(), {"rows": "nope"}, task_id="t1")
    assert calls == []


def test_output_schema_errors_are_reported():
    r = run(cap("local:wrong_output"), {"rows": []}, task_id="t1")
    assert not r.schema_ok
    assert "count" in r.output_errors[0]


@pytest.mark.parametrize("endpoint", ["local:crashes", "local:not_registered"])
def test_local_worker_failures(endpoint):
    with pytest.raises(WorkerError):
        run(cap(endpoint), {"rows": []}, task_id="t1")


def test_http_worker_contract():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(200, json={"status": "done", "output": {"count": 2}, "usage": {"duration_ms": 7, "model": "x"}})

    r = run(cap("https://worker.example.com"), {"rows": [1, 2]}, task_id="t1", http=http(handler))
    assert seen["url"] == "https://worker.example.com/run"
    assert b'"task_id":"t1"' in seen["body"].replace(b" ", b"")
    assert r.schema_ok and r.usage.duration_ms == 7


def test_http_worker_pending():
    h = http(lambda req: httpx.Response(200, json={"status": "pending", "task_id": "w-42"}))
    r = run(cap("https://worker.example.com"), {"rows": []}, task_id="t1", http=h)
    assert (r.status, r.task_id, r.schema_ok) == ("pending", "w-42", False)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"status": "weird"}),
        httpx.Response(200, json={"status": "done"}),
        httpx.Response(200, json={"status": "pending"}),
    ],
)
def test_http_worker_contract_violations(response):
    with pytest.raises(WorkerError):
        run(cap("https://worker.example.com"), {"rows": []}, task_id="t1", http=http(lambda req: response))


def test_http_worker_timeout():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(WorkerError):
        run(cap("https://worker.example.com"), {"rows": []}, task_id="t1", http=http(handler))
