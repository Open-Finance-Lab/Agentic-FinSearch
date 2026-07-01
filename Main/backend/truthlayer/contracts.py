from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True)
class Provenance:
    fact_id: str
    cik: int
    accession: str
    filed: date
    form: str
    taxonomy: str
    tag: str            # the ACTUAL us-gaap tag matched
    # fy/fp widened to Optional vs spec S5's non-null prose: companyfacts entries
    # can omit fiscal year/period, and the facts table declares them NULLable. This
    # is the non-breaking direction; spec S5 of-record should be aligned to match.
    fy: int | None
    fp: str | None
    frame: str | None


@dataclass(frozen=True)
class Period:
    fiscal_year: int | None = None
    fiscal_period: str = "FY"          # 'FY' | 'Q1'..'Q4'
    period_end: date | None = None     # exact end-date (demo path)


@dataclass(frozen=True)
class Query:
    entity: str                        # ticker or CIK
    concept: str
    period: Period
    as_of: date | None = None


@dataclass(frozen=True)
class Evidence:
    concept: str
    value: float | None                # compute view (existing engine)
    value_exact: Decimal | None        # exact (grader / truth claim)
    unit: str | None
    period: Period
    as_of: date | None
    provenance: Provenance | None
    found: bool
    tags_tried: tuple[str, ...]
    restated_later: bool | None        # a filing after as_of changed this value; None if uncomputed
