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
    "ReferenceFact",
    "SourceAnalysisError",
    "SourceFacts",
    "SourceLocation",
    "analyze_file",
    "analyze_source",
    "evaluate_policies",
]
