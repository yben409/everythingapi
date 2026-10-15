import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import registry
from app.db import init_db
from app.registry import CapabilityConflict, CapabilitySpec, NotFound

OBJ = {"type": "object", "properties": {"rows": {"type": "array"}}, "required": ["rows"]}


def spec(**over):
    base = dict(
        name="dedupe",
        version="1.0.0",
        description="Remove duplicate rows from a table",
        input_schema=OBJ,
        output_schema=OBJ,
        price_cents=5,
        deterministic=True,
        validation="recompute",
        endpoint="local:dedupe",
        worker_id="platform",
    )
    return CapabilitySpec(**(base | over))


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as s:
        yield s


def test_register_is_idempotent(session):
    a = registry.register(session, spec())
    b = registry.register(session, spec())
    assert a.id == b.id


def test_changed_spec_needs_new_version(session):
    registry.register(session, spec())
    with pytest.raises(CapabilityConflict):
        registry.register(session, spec(price_cents=6))
    registry.register(session, spec(version="1.1.0", price_cents=6))


def test_get_latest_active_version(session):
    registry.register(session, spec(version="1.2.0"))
    registry.register(session, spec(version="1.10.0"))
    assert registry.get(session, "dedupe").version == "1.10.0"
    assert registry.get(session, "dedupe", "1.2.0").version == "1.2.0"
    registry.get(session, "dedupe", "1.10.0").active = False
    assert registry.get(session, "dedupe").version == "1.2.0"
    with pytest.raises(NotFound):
        registry.get(session, "missing")


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "Bad Name"},
        {"version": "1.0"},
        {"price_cents": 0},
        {"price_cents": 1.5},
        {"kind": "magic"},
        {"validation": "vibes"},
        {"endpoint": "ftp://x"},
        {"input_schema": {"type": "nonsense"}},
        {"output_schema": {"type": "array"}},
    ],
)
def test_invalid_specs_rejected(bad):
    with pytest.raises(ValidationError):
        spec(**bad)


def test_find_by_keywords_and_budget(session):
    registry.register(session, spec())
    registry.register(session, spec(name="parse_csv", description="Parse a CSV file into rows", price_cents=3))
    registry.register(session, spec(name="compute_kpis", description="Compute OTIF and other KPIs from rows", price_cents=50))
    assert [c.name for c in registry.find(session, "parse this csv")] == ["parse_csv"]
    assert [c.name for c in registry.find(session, "kpis from rows")][0] == "compute_kpis"
    assert "compute_kpis" not in [c.name for c in registry.find(session, "kpis from rows", max_price_cents=10)]
    assert registry.find(session, "translate poetry") == []
