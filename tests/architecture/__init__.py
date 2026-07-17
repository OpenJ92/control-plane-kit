"""Typed repository architecture-analysis support."""

from tests.architecture.source import (
    AliasBinding,
    AstPolicy,
    CallFact,
    DecoratorFact,
    ExceptHandlerFact,
    FunctionFact,
    ImportFact,
    ImportKind,
    PolicyFinding,
    ReferenceFact,
    SourceAnalysisError,
    SourceFacts,
    SourceLocation,
    analyze_file,
    analyze_source,
    evaluate_policies,
)
from tests.architecture.policies import (
    PackageDependencyPolicy,
    PackageDependencyRule,
    TransportOwner,
    TransportOwnershipPolicy,
)

__all__ = [
    "AliasBinding",
    "AstPolicy",
    "CallFact",
    "DecoratorFact",
    "ExceptHandlerFact",
    "FunctionFact",
    "ImportFact",
    "ImportKind",
    "PolicyFinding",
    "PackageDependencyPolicy",
    "PackageDependencyRule",
    "ReferenceFact",
    "SourceAnalysisError",
    "SourceFacts",
    "SourceLocation",
    "TransportOwner",
    "TransportOwnershipPolicy",
    "analyze_file",
    "analyze_source",
    "evaluate_policies",
]
