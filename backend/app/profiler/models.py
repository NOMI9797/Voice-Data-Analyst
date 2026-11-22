from __future__ import annotations

from pydantic import BaseModel, Field


class ColumnProfile(BaseModel):
    name: str
    type: str  # raw database type, e.g. VARCHAR(32)
    kind: str  # number | text | date | bool | id | other
    nullable: bool = True
    primary_key: bool = False
    null_fraction: float | None = None
    distinct_count: int | None = None  # exact when the column is categorical, else from sample
    min: str | None = None
    max: str | None = None
    samples: list[str] = Field(default_factory=list)  # most frequent values
    categorical: bool = False
    values: list[str] = Field(default_factory=list)  # all distinct values (categorical text only)
    description: str = ""  # LLM-written, plain English


class Relationship(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    source: str  # declared | inferred
    confidence: float = 1.0


class TableProfile(BaseModel):
    name: str  # as it must be written in SQL (schema-qualified if not default)
    row_count: int | None = None
    is_view: bool = False
    columns: list[ColumnProfile]
    description: str = ""

    def column(self, name: str) -> ColumnProfile | None:
        return next((c for c in self.columns if c.name == name), None)


class SourceProfile(BaseModel):
    source_id: str
    source_name: str
    engine: str
    dialect: str
    tables: list[TableProfile]
    relationships: list[Relationship] = Field(default_factory=list)
    description: str = ""
    profiled_at: str
    seconds: float
    described: bool = False  # LLM descriptions added

    def table(self, name: str) -> TableProfile | None:
        return next((t for t in self.tables if t.name == name), None)
