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


class ModelParams(StrictModel):
    """All model parameters of config/model.yaml (spec sections 3.2-3.4)."""

    horizon_days: Parameter

    # Run-off: daily base rate per deposit category, before severity and panic.
    base_runoff_insured_retail: Parameter
    base_runoff_uninsured_retail: Parameter
    base_runoff_corporate: Parameter
    base_runoff_financial: Parameter

    # Panic multiplier = 1 + (max - 1) * logistic(steepness * (x - midpoint)),
    # x = cumulative deposit loss share + news shocks to date.
    panic_max_multiplier: Parameter
    panic_steepness: Parameter
    panic_midpoint: Parameter
    # Rollover fraction = 1 - sensitivity * normalised panic, clamped to [0, 1].
    wholesale_rollover_sensitivity: Parameter
    # Upper limit on the rollover fraction: 1 = market open, 0 = market closed.
    wholesale_rollover_cap: Parameter

    # Payment outage: withdrawals are queued from start day to end day (0 = no outage).
    payment_outage_start_day: Parameter
    payment_outage_end_day: Parameter

    # Funding waterfall.
    hqla_level1_haircut: Parameter
    hqla_level2_haircut: Parameter
    central_bank_haircut: Parameter
    central_bank_mobilisation_lag_days: Parameter
    fire_sale_discount: Parameter
    fire_sale_daily_cap_share: Parameter

    # LCR proxy: 30-day outflow rates.
    lcr_outflow_rate_insured_retail: Parameter
    lcr_outflow_rate_uninsured_retail: Parameter
    lcr_outflow_rate_corporate: Parameter
    lcr_outflow_rate_financial: Parameter
    lcr_outflow_rate_wholesale: Parameter

    # Triggers.
    recovery_lcr_threshold: Parameter
    recovery_deposit_loss_threshold: Parameter
    early_intervention_lcr_threshold: Parameter
    early_intervention_cet1_margin: Parameter
    resolution_funding_days: Parameter

    @model_validator(mode="after")
    def _check_values(self) -> "ModelParams":
        for name in type(self).model_fields:
            value = getattr(self, name).value
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
            is_share = name.endswith(("_haircut", "_discount", "_share")) or name.startswith(
                ("base_runoff_", "lcr_outflow_rate_")
            )
            if is_share and value > 1:
                raise ValueError(f"{name} must be <= 1")
            if name.endswith(("_haircut", "_discount")) and value >= 1:
                raise ValueError(f"{name} must be < 1")
        if self.horizon_days.value < 1 or self.horizon_days.value != int(self.horizon_days.value):
            raise ValueError("horizon_days must be a positive whole number")
        if self.panic_max_multiplier.value < 1:
            raise ValueError("panic_max_multiplier must be >= 1")
        if self.wholesale_rollover_cap.value > 1:
            raise ValueError("wholesale_rollover_cap must be <= 1")
        start = self.payment_outage_start_day.value
        end = self.payment_outage_end_day.value
        if start != int(start) or end != int(end):
            raise ValueError("payment outage days must be whole numbers")
        if (start == 0) != (end == 0) or end < start:
            raise ValueError("payment outage needs 1 <= start day <= end day, or 0 and 0")
        return self

    def with_overrides(self, overrides: dict[str, Parameter]) -> "ModelParams":
        data = {name: getattr(self, name) for name in type(self).model_fields}
        data.update(overrides)
        return ModelParams(**data)


class Config(StrictModel):
    banks: dict[str, Bank]
    model: ModelParams
    scenarios: dict[str, Scenario]

    @model_validator(mode="after")
    def _check_overrides(self) -> "Config":
        for key, scenario in self.scenarios.items():
            unknown = set(scenario.overrides) - set(ModelParams.model_fields)
            if unknown:
                raise ValueError(
                    f"scenario '{key}' overrides unknown parameters: {sorted(unknown)}"
                )
        return self
