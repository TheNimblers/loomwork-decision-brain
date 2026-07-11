from typing import Literal

from pydantic import BaseModel, model_validator


class IngestRequest(BaseModel):
    title: str
    source_type: Literal[
        "call_transcript",
        "investor_update",
        "board_note",
        "internal_email",
        "internal_note",
        "social_post",
        "board_document",
        "email",
        "research",
    ]
    content: str
    document_date: str | None = None
    metadata: dict | None = None


class ExtractedFact(BaseModel):
    fact_type: str
    claim: str
    verbatim_quote: str
    evidence_tier: Literal["E1", "E2", "E3", "E4", "E5"]
    confidence: float
    conditions: list[str] | None = None
    entities: list[str] | None = None
    numeric_value: float | None = None
    numeric_unit: str | None = None

    @model_validator(mode="after")
    def validate_numeric_pair(self):
        has_value = self.numeric_value is not None
        has_unit = self.numeric_unit is not None
        if has_value != has_unit:
            raise ValueError("numeric_value and numeric_unit must both be set or both be null")
        return self


class ExtractionResult(BaseModel):
    facts: list[ExtractedFact]


class QueryRequest(BaseModel):
    question: str


class DecideRequest(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str | None = None


class Fact(BaseModel):
    id: str
    source_id: str
    fact_type: str
    claim: str
    verbatim_quote: str
    evidence_tier: str
    confidence: float
    conditions: str | None = None
    entities: str | None = None
    numeric_value: float | None = None
    numeric_unit: str | None = None
    valid_from: str | None = None
    learned_at: str
    superseded_by: str | None = None
    contested: int = 0


class Contradiction(BaseModel):
    id: str
    fact_a_id: str
    fact_b_id: str
    conflict_type: str
    description: str
    severity: str
    detected_at: str
    resolved_at: str | None = None
    resolution_note: str | None = None


class DecisionRecord(BaseModel):
    id: str
    question: str
    retrieved_fact_ids: str
    contradictions_found: str | None = None
    research_triggered: int = 0
    research_queries: str | None = None
    research_fact_ids: str | None = None
    synthesis: str
    confidence: float
    recommended_action: str
    open_gaps: str | None = None
    key_contradiction: str | None = None
    human_decision: str | None = None
    decision_note: str | None = None
    created_at: str
