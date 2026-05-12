"""Pydantic contracts for the autonomous worker and bounded LLM tasks."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

ToolStatus = Literal["succeeded", "failed", "skipped"]
ReActActionType = Literal["run_tool", "finish"]
RoleMatchStatus = Literal["exact_match", "adjacent", "irrelevant", "unknown"]
Level = Literal["intern", "entry", "mid", "senior", "staff", "principal", "manager", "unknown"]


class LocationNormalization(BaseModel):
    raw: str | None = None
    city: str | None = None
    region: str | None = None
    country: str | None = None
    display: str | None = None
    remote_type: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class JobIntelligence(BaseModel):
    role_family: str = "unknown"
    role_match_status: RoleMatchStatus = "unknown"
    level: Level = "unknown"
    level_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    location: LocationNormalization = Field(default_factory=LocationNormalization)


class ToolInput(BaseModel):
    dry_run: bool = False
    runtime: dict[str, Any] = Field(default_factory=dict)


class ToolObservation(BaseModel):
    status: ToolStatus
    source_name: str
    candidates: int = 0
    jobs_saved: int = 0
    message: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReActDecision(BaseModel):
    action: ReActActionType
    thought_summary: str = ""
    tool_name: str | None = None
    reason: str = ""


class CycleReflection(BaseModel):
    summary: str = ""
    next_cycle_focus: list[str] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
