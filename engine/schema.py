"""Pydantic models for the YAML config files.

Conventions:
- Every model parameter is a `Parameter` with value, unit and source.
- `source: ASSUMPTION` marks a parameter with no verifiable source.
- Bank archetypes are fictitious: one unit and one source per archetype.
- Balance-sheet equity is represented by CET1 (simplification).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

ASSUMPTION = "ASSUMPTION"
BALANCE_TOLERANCE = 1e-6

NonNegative = Field(ge=0)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Parameter(StrictModel):
    value: float
    unit: str = Field(min_length=1)
    source: str = Field(min_length=1)
    # Optional range, used by the Monte Carlo milestone (M4).
    low: Optional[float] = None
    high: Optional[float] = None

    @model_validator(mode="after")
    def _check_range(self) -> "Parameter":
        if (self.low is None) != (self.high is None):
            raise ValueError("low and high must be given together")
        if self.low is not None and not (self.low <= self.value <= self.high):
            raise ValueError("expected low <= value <= high")
        return self

    @property
    def is_assumption(self) -> bool:
        return self.source.strip().upper() == ASSUMPTION


class Assets(StrictModel):
    cash_reserves: float = NonNegative
    hqla_level1: float = NonNegative
    hqla_level2: float = NonNegative
    other_eligible_collateral: float = NonNegative
    illiquid_assets: float = NonNegative

    def total(self) -> float:
        return (
            self.cash_reserves
            + self.hqla_level1
            + self.hqla_level2
            + self.other_eligible_collateral
            + self.illiquid_assets
        )


class Deposits(StrictModel):
    insured_retail: float = NonNegative
    uninsured_retail: float = NonNegative
    corporate: float = NonNegative
    financial: float = NonNegative

    def total(self) -> float:
        return self.insured_retail + self.uninsured_retail + self.corporate + self.financial


class WholesaleFunding(StrictModel):
    # Amount maturing on each simulated day (key = day number, starting at 1).
    maturing: dict[int, float] = Field(default_factory=dict)
    beyond_horizon: float = NonNegative

    @model_validator(mode="after")
    def _check_ladder(self) -> "WholesaleFunding":
        for day, amount in self.maturing.items():
            if day < 1:
                raise ValueError(f"maturity day must be >= 1, got {day}")
            if amount < 0:
                raise ValueError(f"negative amount maturing on day {day}")
        return self

    def total(self) -> float:
        return sum(self.maturing.values()) + self.beyond_horizon


class Liabilities(StrictModel):
    deposits: Deposits
    wholesale_funding: WholesaleFunding
    other_liabilities: float = NonNegative

    def total(self) -> float:
        return self.deposits.total() + self.wholesale_funding.total() + self.other_liabilities


class Capital(StrictModel):
    cet1: float = NonNegative
    rwa: float = Field(gt=0)
    capital_requirement_ratio: float = Field(gt=0, lt=1)
    mrel: Optional[float] = Field(default=None, ge=0)


class Bank(StrictModel):
    name: str = Field(min_length=1)
    description: str = ""
    unit: str = Field(min_length=1)
    source: str = Field(min_length=1)
    assets: Assets
    liabilities: Liabilities
    capital: Capital

    @model_validator(mode="after")
    def _check_identity(self) -> "Bank":
        gap = self.assets.total() - (self.liabilities.total() + self.capital.cet1)
        if abs(gap) > BALANCE_TOLERANCE:
            raise ValueError(
                f"balance sheet does not balance: assets - (liabilities + equity) = {gap:.6f}"
            )
        return self


class NewsEvent(StrictModel):
    day: int = Field(ge=1)
    headline: str = Field(min_length=1)
    panic_shock: float = NonNegative


class Scenario(StrictModel):
    name: str = Field(min_length=1)
    description: str = ""
    severity: Parameter
    # Parameter values that replace those of model.yaml for this scenario.
    overrides: dict[str, Parameter] = Field(default_factory=dict)
    news_events: list[NewsEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_severity(self) -> "Scenario":
        if self.severity.value < 0:
            raise ValueError("severity must be >= 0")
        return self


class Config(StrictModel):
    banks: dict[str, Bank]
    model: dict[str, Parameter]
    scenarios: dict[str, Scenario]

    @model_validator(mode="after")
    def _check_overrides(self) -> "Config":
        for key, scenario in self.scenarios.items():
            unknown = set(scenario.overrides) - set(self.model)
            if unknown:
                raise ValueError(
                    f"scenario '{key}' overrides unknown parameters: {sorted(unknown)}"
                )
        return self
