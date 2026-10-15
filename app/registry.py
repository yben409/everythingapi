"""Capability registry. A (name, version) pair is immutable once registered."""

import re

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import JSON, Boolean, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db import Base

KINDS = ("primitive", "recipe", "heavy")
VALIDATIONS = ("schema", "recompute", "judge")


class CapabilityConflict(Exception):
    pass


class NotFound(Exception):
    pass


class CapabilitySpec(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,63}$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    kind: str = "primitive"
    description: str = Field(min_length=10, max_length=2000)
    input_schema: dict
    output_schema: dict
    price_cents: int = Field(gt=0, strict=True)
    deterministic: bool
    validation: str
    endpoint: str  # "local:<name>" or an https URL whose POST /run follows the worker contract
    worker_id: str

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        if v not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        return v

    @field_validator("validation")
    @classmethod
    def _validation(cls, v):
        if v not in VALIDATIONS:
            raise ValueError(f"validation must be one of {VALIDATIONS}")
        return v

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schema(cls, v):
        try:
            Draft202012Validator.check_schema(v)
        except SchemaError as e:
            raise ValueError(f"invalid JSON schema: {e.message}")
        if v.get("type") != "object":
            raise ValueError("schema must describe an object")
        return v

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, v):
        if not re.match(r"^(local:[a-z][a-z0-9_]*|https?://\S+)$", v):
            raise ValueError("endpoint must be local:<name> or an http(s) URL")
        return v


class Capability(Base):
    __tablename__ = "capabilities"
    __table_args__ = (UniqueConstraint("name", "version"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    input_schema: Mapped[dict] = mapped_column(JSON)
    output_schema: Mapped[dict] = mapped_column(JSON)
    price_cents: Mapped[int] = mapped_column(Integer)
    deterministic: Mapped[bool] = mapped_column(Boolean)
    validation: Mapped[str] = mapped_column(String)
    endpoint: Mapped[str] = mapped_column(String)
    worker_id: Mapped[str] = mapped_column(String)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    passes: Mapped[int] = mapped_column(Integer, default=0)
    fails: Mapped[int] = mapped_column(Integer, default=0)

    def spec(self) -> CapabilitySpec:
        return CapabilitySpec.model_validate(
            {k: getattr(self, k) for k in CapabilitySpec.model_fields}
        )


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def register(session: Session, spec: CapabilitySpec) -> Capability:
    """Idempotent for an identical spec. A changed spec needs a new version."""
    existing = session.scalar(
        select(Capability).where(Capability.name == spec.name, Capability.version == spec.version)
    )
    if existing is not None:
        if existing.spec() != spec:
            raise CapabilityConflict(f"{spec.name} {spec.version} already registered with a different spec")
        return existing
    cap = Capability(**spec.model_dump(), active=True, passes=0, fails=0)
    session.add(cap)
    session.commit()
    return cap


def get(session: Session, name: str, version: str | None = None) -> Capability:
    """Exact version, or the latest active version."""
    q = select(Capability).where(Capability.name == name)
    if version is not None:
        cap = session.scalar(q.where(Capability.version == version))
    else:
        caps = session.scalars(q.where(Capability.active.is_(True))).all()
        cap = max(caps, key=lambda c: _version_key(c.version), default=None)
    if cap is None:
        raise NotFound(f"{name} {version or '(latest)'}")
    return cap


def find(session: Session, query: str, max_price_cents: int | None = None, limit: int = 5) -> list[Capability]:
    """Keyword match over name and description, active only, within budget.

    Placeholder for semantic search (needs an embedding provider).
    """
    words = set(re.findall(r"[a-z0-9]+", query.lower()))
    q = select(Capability).where(Capability.active.is_(True))
    if max_price_cents is not None:
        q = q.where(Capability.price_cents <= max_price_cents)
    scored = []
    for cap in session.scalars(q):
        text = set(re.findall(r"[a-z0-9]+", f"{cap.name.replace('_', ' ')} {cap.description}".lower()))
        score = len(words & text)
        if score:
            scored.append((score, cap.price_cents, cap))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [cap for _, _, cap in scored[:limit]]
