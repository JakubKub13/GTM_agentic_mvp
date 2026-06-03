"""Pydantic models — the contract shared across all agents."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Company(BaseModel):
    name: str
    domain: str
    country: str = ""
    description: str = ""


class Signal(BaseModel):
    signal_type: Literal["erp_migration", "hiring", "ma_leadership", "pain"]
    title: str
    summary: str
    source_url: str
    published_date: str | None = None
    relevance: str = ""


class OutreachDraft(BaseModel):
    persona: str
    subject: str
    first_line: str
    body: str


class ICPScore(BaseModel):
    company_name: str
    domain: str
    score: int = Field(ge=1, le=10)
    tier: Literal["Tier 1", "Tier 2", "Tier 3"]
    confidence: Literal["high", "medium", "low"]
    why_fit: list[str]
    why_not: list[str]
    recommended_persona: str
    recommended_angle: str
    reasoning: str
    needs_human_research: bool
    outreach: OutreachDraft


class RunResult(BaseModel):
    score: ICPScore
    signals: list[Signal]
    crm_status: str = "skipped"
    slack_status: str = "skipped"
    outreach_status: str = "skipped"
    agent_log: list[str] = []  # tool calls each agent made, for the report
