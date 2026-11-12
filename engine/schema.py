"""Pydantic models for the YAML config files.

Conventions:
- Every model parameter is a `Parameter` with value, unit and source.
- `source: ASSUMPTION` marks a parameter with no verifiable source.
- Bank archetypes are fictitious: one unit and one source per archetype.
- Balance-sheet equity is represented by CET1 (simplification).
"""

from __future__ import annotations

from string import Formatter
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


# Balance sheet lines that can carry their own source (see Bank.sources).
BANK_ITEMS = (
    "cash_reserves",
    "hqla_level1",
    "hqla_level2",
    "other_eligible_collateral",
    "illiquid_assets",
    "insured_retail",
    "uninsured_retail",
    "corporate",
    "financial",
    "wholesale_funding",
    "other_liabilities",
    "cet1",
    "rwa",
    "capital_requirement_ratio",
)


class Bank(StrictModel):
    name: str = Field(min_length=1)
    description: str = ""
    unit: str = Field(min_length=1)
    # Default source of every line; `sources` overrides it line by line.
    source: str = Field(min_length=1)
    sources: dict[str, str] = Field(default_factory=dict)
    assets: Assets
    liabilities: Liabilities
    capital: Capital
    # Multiplies every deposit run-off rate of this archetype (1 = the global rates).
    runoff_speed_factor: Parameter

    def source_of(self, item: str) -> str:
        return self.sources.get(item, self.source)

    @model_validator(mode="after")
    def _check_identity(self) -> "Bank":
        unknown = set(self.sources) - set(BANK_ITEMS)
        if unknown:
            raise ValueError(f"sources for unknown lines: {sorted(unknown)}")
        if any(not text.strip() for text in self.sources.values()):
            raise ValueError("a line source must not be empty")
        if self.runoff_speed_factor.value < 0:
            raise ValueError("runoff_speed_factor must be >= 0")
        gap = self.assets.total() - (self.liabilities.total() + self.capital.cet1)
        if abs(gap) > BALANCE_TOLERANCE:
            raise ValueError(
                f"balance sheet does not balance: assets - (liabilities + equity) = {gap:.6f}"
            )
        return self


class NewsEvent(StrictModel):
    day: int = Field(ge=1)
    headline: str = Field(min_length=1)
    # Added to the panic input on that day; negative for a reassuring event.
    panic_shock: float


WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class SeverityFade(StrictModel):
    """The scenario's pressure eases once its trigger is over.

    From `from_day` on, severity moves from its starting value towards `floor`, halving the
    distance every `half_life_days`. A severity already at or below the floor does not move.
    """

    from_day: Parameter
    half_life_days: Parameter
    floor: Parameter

    @model_validator(mode="after")
    def _check(self) -> "SeverityFade":
        if self.from_day.value < 1 or self.from_day.value != int(self.from_day.value):
            raise ValueError("fade.from_day must be a whole number >= 1")
        if self.half_life_days.value <= 0:
            raise ValueError("fade.half_life_days must be > 0")
        if self.floor.value < 0:
            raise ValueError("fade.floor must be >= 0")
        return self

    def factor_on(self, day: int, severity: float) -> float:
        """Severity in effect on `day`, given the scenario's starting severity."""
        start, floor = int(self.from_day.value), self.floor.value
        if day < start or severity <= floor:
            return severity
        remaining = 0.5 ** ((day - start + 1) / self.half_life_days.value)
        return floor + (severity - floor) * remaining


class Scenario(StrictModel):
    name: str = Field(min_length=1)
    description: str = ""
    severity: Parameter
    # Optional: the pressure eases after the trigger is over (none = constant severity).
    fade: Optional[SeverityFade] = None
    # Weekday of day 1. Used for labels in the exercise script only (no calendar rules yet).
    start_weekday: str = "Monday"
    # Parameter values that replace those of model.yaml for this scenario.
    overrides: dict[str, Parameter] = Field(default_factory=dict)
    news_events: list[NewsEvent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_severity(self) -> "Scenario":
        if self.severity.value < 0:
            raise ValueError("severity must be >= 0")
        if self.start_weekday not in WEEKDAYS:
            raise ValueError(f"start_weekday must be one of {list(WEEKDAYS)}")
        return self

    def weekday(self, day: int) -> str:
        """Weekday of simulated day `day` (day 1 is the start weekday; day 0 the day before)."""
        return WEEKDAYS[(WEEKDAYS.index(self.start_weekday) + day - 1) % 7]


# Time blocks of the intraday view: key -> label.
INTRADAY_BLOCKS = {
    "morning": "09:00-12:00",
    "midday": "12:00-15:00",
    "afternoon": "15:00-18:00",
    "overnight": "after 18:00",
}


class ModelParams(StrictModel):
    """All model parameters of config/model.yaml (spec sections 3.2-3.4)."""

    horizon_days: Parameter

    # Run-off: daily base rate per deposit category, before severity and panic.
    base_runoff_insured_retail: Parameter
    base_runoff_uninsured_retail: Parameter
    base_runoff_corporate: Parameter
    base_runoff_financial: Parameter
    # Stable core: only this share of each category's opening stock can leave.
    flight_prone_share_insured_retail: Parameter
    flight_prone_share_uninsured_retail: Parameter
    flight_prone_share_corporate: Parameter
    flight_prone_share_financial: Parameter

    # Panic multiplier = 1 + (max - 1) * logistic(steepness * (x - midpoint)).
    # x is a fading memory: each day it loses the share `panic_decay`, then the day's new
    # deposit loss share and news shocks are added. It never goes below zero.
    panic_max_multiplier: Parameter
    panic_steepness: Parameter
    panic_midpoint: Parameter
    panic_decay: Parameter
    # Rollover fraction = 1 - sensitivity * normalised panic, clamped to [0, 1].
    wholesale_rollover_sensitivity: Parameter
    # Upper limit on the rollover fraction: 1 = market open, 0 = market closed.
    wholesale_rollover_cap: Parameter

    # Calendar: weekend rules on or off, and the share of a business day's withdrawal
    # requests that still comes in on a weekend day.
    weekend_rules_on: Parameter
    weekend_withdrawal_share: Parameter
    # Intraday view: allocation of a day's outflow over four time blocks.
    intraday_share_morning: Parameter
    intraday_share_midday: Parameter
    intraday_share_afternoon: Parameter
    intraday_share_overnight: Parameter

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
    # Emergency liquidity assistance: usable once granted (a decision, config/decisions.yaml)
    # and only while the CET1 ratio is at or above the requirement. Collateral: what is left
    # of the eligible collateral plus a share of the loan book, at a higher haircut.
    ela_haircut: Parameter
    ela_eligible_loan_share: Parameter

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
    # "Likely to fail" flag: survival horizon below this number of days, or CET1 ratio
    # projected below the requirement over the same number of days.
    likely_to_fail_survival_days: Parameter
    # Thresholds of the "stabilised" outcome at the end of a run.
    stabilised_days: Parameter
    stabilised_outflow_share: Parameter
    resolution_funding_days: Parameter

    # Contagion: panic shock received by a second bank when the first bank reaches a
    # trigger stage, at similarity 1; and the similarity added by each shared feature.
    contagion_shock_recovery: Parameter
    contagion_shock_early_intervention: Parameter
    contagion_shock_failure: Parameter
    contagion_similarity_same_archetype: Parameter
    contagion_similarity_same_country: Parameter

    # Monte Carlo defaults.
    monte_carlo_runs: Parameter
    monte_carlo_seed: Parameter

    @model_validator(mode="after")
    def _check_values(self) -> "ModelParams":
        for name in type(self).model_fields:
            value = getattr(self, name).value
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
            is_share = name.endswith(("_haircut", "_discount", "_share")) or name.startswith(
                ("base_runoff_", "lcr_outflow_rate_", "flight_prone_share_")
            )
            if is_share and value > 1:
                raise ValueError(f"{name} must be <= 1")
            if name.endswith(("_haircut", "_discount")) and value >= 1:
                raise ValueError(f"{name} must be < 1")
        if self.horizon_days.value < 1 or self.horizon_days.value != int(self.horizon_days.value):
            raise ValueError("horizon_days must be a positive whole number")
        if self.panic_max_multiplier.value < 1:
            raise ValueError("panic_max_multiplier must be >= 1")
        if self.panic_decay.value > 1:
            raise ValueError("panic_decay must be <= 1")
        for name in ("likely_to_fail_survival_days", "stabilised_days"):
            if getattr(self, name).value != int(getattr(self, name).value):
                raise ValueError(f"{name} must be a whole number")
        if self.stabilised_days.value < 1:
            raise ValueError("stabilised_days must be >= 1")
        if self.weekend_rules_on.value not in (0, 1):
            raise ValueError("weekend_rules_on must be 0 or 1")
        intraday = sum(getattr(self, f"intraday_share_{block}").value for block in INTRADAY_BLOCKS)
        if abs(intraday - 1.0) > 1e-9:
            raise ValueError("the intraday shares must add up to 1")
        for name in ("monte_carlo_runs", "monte_carlo_seed"):
            if getattr(self, name).value != int(getattr(self, name).value):
                raise ValueError(f"{name} must be a whole number")
        if self.monte_carlo_runs.value < 1:
            raise ValueError("monte_carlo_runs must be >= 1")
        similarity = (
            self.contagion_similarity_same_archetype.value
            + self.contagion_similarity_same_country.value
        )
        if similarity > 1:
            raise ValueError("contagion similarity components must add up to at most 1")
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


ACTORS = (
    "competent_authority",
    "resolution_authority",
    "central_bank",
    "deposit_guarantee_scheme",
    "bank_management",
)
# --------------------------------------------------------------------------- decisions

# Stages a decision can wait for ("failure" is the payments default).
DECISION_STAGES = ("recovery", "early_intervention", "likely_to_fail", "failure")
RESOLUTION_TOOLS = ("bail_in", "sale_of_business", "liquidation")
# Effect types implemented in the engine: name -> (required parameters, optional parameters).
DECISION_EFFECTS = {
    "none": ((), ()),
    "panic_shock": (("shock",), ()),
    "asset_sale": (("share", "discount", "settlement_days"), ()),
    "ela": ((), ("cap_share",)),
    "trigger": ((), ()),
    "declare_foltf": ((), ()),
    "moratorium": (("days",), ()),
    "resolution_tool": ((), ()),
    "prepare_payout": (("payout_days",), ()),
    "dgs_payout": ((), ()),
}
# Daily figures that can be listed in an actor's view for the decision log.
DECISION_FIGURES = (
    "deposits_total",
    "cum_deposit_loss_pct",
    "outflow_total",
    "queued_withdrawals",
    "liquidity_buffer",
    "survival_days",
    "lcr_proxy",
    "cet1_ratio",
    "cet1_ratio_projected",
    "unencumbered_collateral",
    "central_bank_funding",
    "ela_funding",
    "dep_insured_retail",
    "illiquid_assets",
    "loss",
)


class DecisionCondition(StrictModel):
    """When a decision becomes possible. Every part that is given must hold."""

    stage: Optional[str] = None  # this stage, or a later one, has been reached
    after: Optional[str] = None  # this other decision has been taken ...
    after_option: Optional[str] = None  # ... with this option (any option if omitted)
    min_day: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _check(self) -> "DecisionCondition":
        if self.stage is not None and self.stage not in DECISION_STAGES:
            raise ValueError(f"unknown stage '{self.stage}'")
        if self.after_option is not None and self.after is None:
            raise ValueError("after_option needs after")
        return self


class DecisionOption(StrictModel):
    name: str = Field(min_length=1)
    description: str = ""
    effect: str
    params: dict[str, Parameter] = Field(default_factory=dict)
    tool: Optional[str] = None  # for effect "resolution_tool"
    # For effect "trigger": the other decision and option that are put into effect.
    trigger_decision: Optional[str] = None
    trigger_option: Optional[str] = None

    @model_validator(mode="after")
    def _check(self) -> "DecisionOption":
        if self.effect not in DECISION_EFFECTS:
            raise ValueError(f"unknown effect '{self.effect}'")
        required, optional = DECISION_EFFECTS[self.effect]
        missing = set(required) - set(self.params)
        unknown = set(self.params) - set(required) - set(optional)
        if missing or unknown:
            raise ValueError(
                f"effect '{self.effect}': missing parameters {sorted(missing)}, "
                f"unknown parameters {sorted(unknown)}"
            )
        for name in ("share", "discount", "cap_share"):
            if name in self.params and not 0 <= self.params[name].value <= 1:
                raise ValueError(f"{name} must be between 0 and 1")
        if "discount" in self.params and self.params["discount"].value >= 1:
            raise ValueError("discount must be < 1")
        for name in ("settlement_days", "days", "payout_days"):
            if name in self.params:
                value = self.params[name].value
                if value < 0 or value != int(value):
                    raise ValueError(f"{name} must be a whole number >= 0")
        if (self.effect == "resolution_tool") != (self.tool is not None):
            raise ValueError("tool is required for, and only for, effect 'resolution_tool'")
        if self.tool is not None and self.tool not in RESOLUTION_TOOLS:
            raise ValueError(f"unknown resolution tool '{self.tool}'")
        has_trigger = self.trigger_decision is not None and self.trigger_option is not None
        if (self.effect == "trigger") != has_trigger:
            raise ValueError("trigger_decision and trigger_option go with effect 'trigger'")
        return self


class Decision(StrictModel):
    name: str = Field(min_length=1)
    actor: str
    condition: DecisionCondition = Field(default_factory=DecisionCondition)
    # The option that stands when nobody decides ("authorities act late").
    default_option: str
    options: dict[str, DecisionOption]

    @model_validator(mode="after")
    def _check(self) -> "Decision":
        if self.actor not in ACTORS:
            raise ValueError(f"unknown actor '{self.actor}'")
        if self.default_option not in self.options:
            raise ValueError(f"default option '{self.default_option}' is not an option")
        if self.options[self.default_option].effect != "none":
            raise ValueError("the default option must have effect 'none'")
        return self


class PathChoice(StrictModel):
    decision: str
    option: str
    day: int = Field(default=0, ge=0)


class DecisionPath(StrictModel):
    name: str = Field(min_length=1)
    description: str = ""
    choices: list[PathChoice] = Field(default_factory=list)


class FasterReporting(StrictModel):
    """A decision option that shortens an actor's delay from the day it is in effect."""

    decision: str
    option: str
    lag_days: int = Field(ge=0)


class ActorInformation(StrictModel):
    """What an actor knows, and how late (SPEC_V2 phase D).

    The delay applies to figures. Stage changes, payments default, FOLTF and resolution are
    notified the same day to every actor that is already informed.
    """

    # Who the information comes from, as it is named in the script.
    source: str = Field(min_length=1)
    # Figures the actor receives (its scope).
    figures: list[str]
    # Age of the figures in days: 0 = same day, 1 = the previous day's close.
    lag_days: int = Field(ge=0)
    # The actor receives nothing before this stage is reached (none = informed from the start).
    informed_from: Optional[str] = None
    faster: Optional[FasterReporting] = None
    # The actor also gets the day's outflow split over time blocks (an allocation).
    intraday: bool = False

    @model_validator(mode="after")
    def _check(self) -> "ActorInformation":
        unknown = set(self.figures) - set(DECISION_FIGURES)
        if unknown:
            raise ValueError(f"unknown figures: {sorted(unknown)}")
        if len(set(self.figures)) != len(self.figures):
            raise ValueError("a figure is listed twice")
        if self.informed_from is not None and self.informed_from not in DECISION_STAGES:
            raise ValueError(f"unknown stage '{self.informed_from}'")
        if self.faster is not None and self.faster.lag_days >= self.lag_days:
            raise ValueError("faster reporting must shorten the delay")
        if self.intraday and (self.lag_days != 0 or "outflow_total" not in self.figures):
            raise ValueError("intraday needs same-day figures that include outflow_total")
        return self


class DecisionsConfig(StrictModel):
    """Decision points of config/decisions.yaml (SPEC_V2 phase B)."""

    # Days between taking a decision (after the close) and its effect.
    lead_days: Parameter
    # Days until covered depositors are paid when the payout was not prepared.
    payout_days_unprepared: Parameter
    # What each actor knows and how late: used for the decision log and the exercise script.
    information: dict[str, ActorInformation]
    decisions: dict[str, Decision]
    default_path: str
    paths: dict[str, DecisionPath]

    @model_validator(mode="after")
    def _check(self) -> "DecisionsConfig":
        for name in ("lead_days", "payout_days_unprepared"):
            value = getattr(self, name).value
            if value < 0 or value != int(value):
                raise ValueError(f"{name} must be a whole number >= 0")
        if self.lead_days.value < 1:
            raise ValueError("lead_days must be at least 1: a decision uses end-of-day figures")
        if set(self.information) != set(ACTORS):
            raise ValueError(f"information must list exactly {list(ACTORS)}")
        for actor, information in self.information.items():
            faster = information.faster
            if faster is not None:
                target = self.decisions.get(faster.decision)
                if target is None or faster.option not in target.options:
                    raise ValueError(f"information of '{actor}': unknown decision or option")
        for key, decision in self.decisions.items():
            condition = decision.condition
            if condition.after is not None:
                if condition.after not in self.decisions or condition.after == key:
                    raise ValueError(f"decision '{key}': unknown decision in 'after'")
                options = self.decisions[condition.after].options
                if condition.after_option is not None and condition.after_option not in options:
                    raise ValueError(f"decision '{key}': unknown option in 'after_option'")
            for option in decision.options.values():
                if option.effect != "trigger":
                    continue
                target = self.decisions.get(option.trigger_decision)
                if target is None or option.trigger_option not in target.options:
                    raise ValueError(f"decision '{key}': trigger points to an unknown option")
                if target.options[option.trigger_option].effect == "trigger":
                    raise ValueError(f"decision '{key}': a trigger cannot point to a trigger")
        if self.default_path not in self.paths:
            raise ValueError(f"unknown default path '{self.default_path}'")
        for key, path in self.paths.items():
            seen = set()
            for choice in path.choices:
                decision = self.decisions.get(choice.decision)
                if decision is None or choice.option not in decision.options:
                    raise ValueError(f"path '{key}': unknown decision or option {choice}")
                if choice.decision in seen:
                    raise ValueError(f"path '{key}': decision '{choice.decision}' appears twice")
                seen.add(choice.decision)
        return self


# --------------------------------------------------------------------------- script

# Kinds of inject and the placeholders their text may use. No placeholder carries a figure:
# every number in a participant's document comes from the actor's information set.
SCRIPT_TEXTS = {
    "opening": ("bank_name", "scenario_name"),
    "news": ("headline",),
    "recovery": ("bank_name", "sender"),
    "early_intervention": ("bank_name", "sender"),
    "likely_to_fail": ("bank_name", "sender"),
    "failure": ("bank_name", "sender"),
    "foltf": ("bank_name",),
    "resolution": ("bank_name", "outcome"),
    "decision_taken": ("decider", "decision_name", "option_name", "effect_day"),
    "decision_due": ("decision_name",),
    "end_open": ("bank_name", "horizon_days"),
}
# "stage": the bank learns of a stage change; "stage_relay": the competent authority is told
# by the bank; "stage_relay_others": the other authorities are told by the competent authority.
SCRIPT_CLOCK = (
    "opening", "news", "stage", "stage_relay", "stage_relay_others", "decision_due",
    "decision_taken", "end",
)
# What an ambient inject can be anchored to.
AMBIENT_ANCHORS = DECISION_STAGES + ("foltf",)


def _check_text(text: str, allowed: tuple[str, ...]) -> None:
    for _, field, spec, conversion in Formatter().parse(text):
        if field is None:
            continue
        if field not in allowed:
            raise ValueError(f"unknown placeholder '{{{field}}}' (allowed: {list(allowed)})")
        if spec or conversion:
            raise ValueError(f"placeholder '{{{field}}}' must not carry a format")


class Coordination(StrictModel):
    """Who the deciding actor should consult before, and inform after, a decision (draft)."""

    consult: list[str] = Field(default_factory=list)
    inform: list[str] = Field(default_factory=list)


class AmbientInject(StrictModel):
    """Background noise for the exercise: no effect on any figure (SPEC_V2 phase C1)."""

    title: str = Field(min_length=1)
    sender: str = Field(min_length=1)
    recipients: list[str]
    anchor: str  # the stage, or "foltf", it is placed after
    offset_days: int = Field(ge=0)
    time: str
    text: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "AmbientInject":
        unknown = set(self.recipients) - set(ACTORS)
        if unknown or not self.recipients:
            raise ValueError(f"ambient '{self.title}': unknown or missing recipients")
        if self.anchor not in AMBIENT_ANCHORS:
            raise ValueError(f"ambient '{self.title}': unknown anchor '{self.anchor}'")
        _check_text(self.text, ("bank_name",))
        if any(character.isdigit() for character in self.text):
            raise ValueError(f"ambient '{self.title}': the text must not carry figures")
        return self


class GlossaryEntry(StrictModel):
    term: str = Field(min_length=1)
    definition: str = Field(min_length=1)


class Showcase(StrictModel):
    """The run the app opens on and the guided demo follows."""

    bank: str
    scenario: str
    path: str
    compare_path: str


class ScriptConfig(StrictModel):
    """Texts and rules of the exercise script, config/script.yaml (SPEC_V2 phase D)."""

    showcase: Showcase
    draft_notice: str = Field(min_length=1)
    disclaimer: str = Field(min_length=1)
    actors: dict[str, str]  # display names
    clock: dict[str, str]  # time of day of each kind of inject
    # Days a decision stays open when the no-decision path has no payments default.
    decision_window_days: Parameter
    texts: dict[str, str]
    # One question per decision, put to the deciding actor.
    questions: dict[str, str]
    coordination: dict[str, Coordination]
    # Optional background injects; the facilitator can switch them off.
    ambient: dict[str, AmbientInject] = Field(default_factory=dict)
    debrief: list[str]
    glossary: list[GlossaryEntry]

    @model_validator(mode="after")
    def _check(self) -> "ScriptConfig":
        if set(self.actors) != set(ACTORS):
            raise ValueError(f"actors must be exactly {list(ACTORS)}")
        if set(self.clock) != set(SCRIPT_CLOCK):
            raise ValueError(f"clock must give a time for exactly {list(SCRIPT_CLOCK)}")
        times = dict(self.clock)
        times.update({f"ambient {key}": item.time for key, item in self.ambient.items()})
        for kind, value in times.items():
            hours, _, minutes = value.partition(":")
            if not (hours.isdigit() and minutes.isdigit() and len(minutes) == 2
                    and int(hours) < 24 and int(minutes) < 60):
                raise ValueError(f"clock '{kind}' must be a time such as 08:00")
        if set(self.texts) != set(SCRIPT_TEXTS):
            raise ValueError(f"texts must be exactly {list(SCRIPT_TEXTS)}")
        for kind, text in self.texts.items():
            _check_text(text, SCRIPT_TEXTS[kind])
        window = self.decision_window_days.value
        if window < 0 or window != int(window):
            raise ValueError("decision_window_days must be a whole number >= 0")
        for decision, coordination in self.coordination.items():
            unknown = (set(coordination.consult) | set(coordination.inform)) - set(ACTORS)
            if unknown:
                raise ValueError(f"coordination of '{decision}': unknown actors {sorted(unknown)}")
        if not self.debrief:
            raise ValueError("debrief needs at least one question")
        return self


class Config(StrictModel):
    banks: dict[str, Bank]
    model: ModelParams
    scenarios: dict[str, Scenario]
    decisions: DecisionsConfig
    script: ScriptConfig

    @model_validator(mode="after")
    def _check_script(self) -> "Config":
        decisions = set(self.decisions.decisions)
        for name in ("questions", "coordination"):
            if set(getattr(self.script, name)) != decisions:
                raise ValueError(f"script.{name} must cover exactly the decisions {sorted(decisions)}")
        showcase = self.script.showcase
        if showcase.bank not in self.banks or showcase.scenario not in self.scenarios:
            raise ValueError("script.showcase names an unknown bank or scenario")
        if not {showcase.path, showcase.compare_path} <= set(self.decisions.paths):
            raise ValueError("script.showcase names an unknown decision path")
        for key, coordination in self.script.coordination.items():
            actor = self.decisions.decisions[key].actor
            if actor in coordination.consult or actor in coordination.inform:
                raise ValueError(f"coordination of '{key}': the deciding actor lists itself")
        return self

    @model_validator(mode="after")
    def _check_overrides(self) -> "Config":
        for key, scenario in self.scenarios.items():
            unknown = set(scenario.overrides) - set(ModelParams.model_fields)
            if unknown:
                raise ValueError(
                    f"scenario '{key}' overrides unknown parameters: {sorted(unknown)}"
                )
        return self
