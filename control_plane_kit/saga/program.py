"""Immutable saga syntax whose leaves are typed effect values."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Generic, TypeAlias, TypeVar


EffectT = TypeVar("EffectT")


class SagaProgramError(ValueError):
    """Raised when saga syntax cannot represent a coherent program."""


@dataclass(frozen=True, order=True)
class SagaStepId:
    """Stable identity shared by programs, schedules, and durable events."""

    value: str

    def __post_init__(self) -> None:
        if not self.value.strip():
            raise SagaProgramError("saga step id must not be empty")


@dataclass(frozen=True)
class SagaStep(Generic[EffectT]):
    """One forward effect and its optional compensating effect."""

    step_id: SagaStepId
    effect: EffectT
    compensation: EffectT | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.step_id, SagaStepId):
            raise TypeError("saga step requires SagaStepId")
        if self.effect is None:
            raise SagaProgramError("saga step effect must not be None")


@dataclass(frozen=True)
class End:
    """Identity value for sequential saga composition."""


@dataclass(frozen=True)
class StepNode(Generic[EffectT]):
    """One saga step followed by another program."""

    step: SagaStep[EffectT]
    next: SagaProgram[EffectT] = End()

    def __post_init__(self) -> None:
        if not isinstance(self.step, SagaStep):
            raise TypeError("step node requires SagaStep")
        _require_program(self.next)


@dataclass(frozen=True)
class ParallelNode(Generic[EffectT]):
    """Declared fan-out followed by one shared continuation."""

    branches: tuple[SagaProgram[EffectT], ...]
    next: SagaProgram[EffectT] = End()

    def __post_init__(self) -> None:
        if len(self.branches) < 2:
            raise SagaProgramError("parallel saga nodes require at least two branches")
        for branch in self.branches:
            _require_program(branch)
            if isinstance(branch, End):
                raise SagaProgramError("parallel saga branches must not be empty")
        _require_program(self.next)


SagaProgram: TypeAlias = End | StepNode[EffectT] | ParallelNode[EffectT]


def step(value: SagaStep[EffectT]) -> StepNode[EffectT]:
    """Lift one typed step into saga syntax."""

    return StepNode(value)


def chain(*values: SagaStep[EffectT]) -> SagaProgram[EffectT]:
    """Compose typed steps sequentially without mutating an existing value."""

    program: SagaProgram[EffectT] = End()
    for value in reversed(values):
        program = StepNode(value, program)
    return program


def parallel(
    *branches: SagaProgram[EffectT] | SagaStep[EffectT],
) -> ParallelNode[EffectT]:
    """Compose at least two declared branches into one fan-out node."""

    return ParallelNode(tuple(_coerce_program(branch) for branch in branches))


def then(
    program: SagaProgram[EffectT] | SagaStep[EffectT],
    continuation: SagaProgram[EffectT] | SagaStep[EffectT],
) -> SagaProgram[EffectT]:
    """Append a continuation while preserving the original syntax tree."""

    current = _coerce_program(program)
    following = _coerce_program(continuation)
    match current:
        case End():
            return following
        case StepNode():
            return replace(current, next=then(current.next, following))
        case ParallelNode():
            return replace(current, next=then(current.next, following))
    raise TypeError(f"unsupported saga program {current!r}")


def program_steps(program: SagaProgram[EffectT]) -> tuple[SagaStep[EffectT], ...]:
    """Flatten syntax deterministically and reject duplicate stable identities."""

    _require_program(program)
    values = _walk(program)
    seen: set[SagaStepId] = set()
    for value in values:
        if value.step_id in seen:
            raise SagaProgramError(
                f"duplicate saga step id {value.step_id.value!r}"
            )
        seen.add(value.step_id)
    return values


def _walk(program: SagaProgram[EffectT]) -> tuple[SagaStep[EffectT], ...]:
    match program:
        case End():
            return ()
        case StepNode(step=value, next=following):
            return (value, *_walk(following))
        case ParallelNode(branches=branches, next=following):
            branch_steps = tuple(
                value for branch in branches for value in _walk(branch)
            )
            return (*branch_steps, *_walk(following))
    raise TypeError(f"unsupported saga program {program!r}")


def _coerce_program(
    value: SagaProgram[EffectT] | SagaStep[EffectT],
) -> SagaProgram[EffectT]:
    if isinstance(value, SagaStep):
        return step(value)
    _require_program(value)
    return value


def _require_program(value: object) -> None:
    if not isinstance(value, (End, StepNode, ParallelNode)):
        raise TypeError("saga program must be End, StepNode, or ParallelNode")
