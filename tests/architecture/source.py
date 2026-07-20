"""Convert Python source into deterministic facts for architecture policies."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


class SourceAnalysisError(ValueError):
    """Raised when Python source cannot become architecture facts."""


@dataclass(frozen=True, order=True)
class SourceLocation:
    """Stable source evidence without retaining source text."""

    path: str
    line: int
    column: int


class ImportKind(StrEnum):
    """Closed Python import forms."""

    IMPORT = "import"
    FROM = "from"


@dataclass(frozen=True, order=True)
class AliasBinding:
    """A local name and the qualified name it denotes."""

    local_name: str
    qualified_name: str


@dataclass(frozen=True, order=True)
class ImportFact:
    """One imported module or member with its local binding."""

    kind: ImportKind
    module: str
    imported_name: str | None
    bound_name: str
    location: SourceLocation

    @property
    def qualified_name(self) -> str:
        if self.imported_name is None:
            return self.module
        separator = "" if self.module.endswith(".") else "."
        return f"{self.module}{separator}{self.imported_name}"


@dataclass(frozen=True, order=True)
class ReferenceFact:
    """A resolved name or attribute reference."""

    qualified_name: str
    location: SourceLocation


@dataclass(frozen=True, order=True)
class BooleanArgumentFact:
    """One boolean literal argument without retaining arbitrary values."""

    position: int
    value: bool


class ExpressionShape(StrEnum):
    """Closed source-level shapes useful for boundary policies."""

    DICTIONARY = "dictionary"
    LIST = "list"
    TUPLE = "tuple"
    CALL = "call"
    NAME = "name"
    OTHER = "other"


@dataclass(frozen=True, order=True)
class KeywordArgumentFact:
    """One named call argument classified without retaining its value."""

    name: str
    shape: ExpressionShape
    literal_mapping_keys: tuple[str, ...] = ()


@dataclass(frozen=True, order=True)
class CallFact:
    """A resolved call target when static qualification is possible."""

    qualified_name: str
    location: SourceLocation
    boolean_arguments: tuple[BooleanArgumentFact, ...]
    keyword_arguments: tuple[KeywordArgumentFact, ...]
    first_two_constants_equal: bool


@dataclass(frozen=True, order=True)
class DecoratorFact:
    """A resolved function or class decorator."""

    qualified_name: str
    location: SourceLocation
    boolean_arguments: tuple[BooleanArgumentFact, ...]


@dataclass(frozen=True, order=True)
class FunctionFact:
    """Structural facts for one function or method definition."""

    qualified_name: str
    decorators: tuple[DecoratorFact, ...]
    statement_count: int
    pass_only: bool
    empty_body: bool
    location: SourceLocation


@dataclass(frozen=True, order=True)
class ClassFact:
    """Structural facts for one class declaration."""

    qualified_name: str
    decorators: tuple[DecoratorFact, ...]
    location: SourceLocation


@dataclass(frozen=True, order=True)
class ExceptHandlerFact:
    """Structural evidence for an exception handler body."""

    exception_name: str | None
    pass_only: bool
    location: SourceLocation


@dataclass(frozen=True, order=True)
class BooleanAssertionFact:
    """One literal boolean ``assert`` statement."""

    value: bool
    location: SourceLocation


@dataclass(frozen=True)
class SourceFacts:
    """Deterministic static facts extracted from one Python module."""

    path: str
    module: str
    aliases: tuple[AliasBinding, ...]
    imports: tuple[ImportFact, ...]
    references: tuple[ReferenceFact, ...]
    calls: tuple[CallFact, ...]
    decorators: tuple[DecoratorFact, ...]
    classes: tuple[ClassFact, ...]
    functions: tuple[FunctionFact, ...]
    except_handlers: tuple[ExceptHandlerFact, ...]
    boolean_assertions: tuple[BooleanAssertionFact, ...]


@dataclass(frozen=True, order=True)
class PolicyFinding:
    """One actionable architecture-policy violation."""

    rule_id: str
    message: str
    location: SourceLocation


class AstPolicy(Protocol):
    """A pure interpretation from source facts to findings."""

    def evaluate(self, facts: SourceFacts) -> tuple[PolicyFinding, ...]: ...


def analyze_file(path: Path, *, root: Path) -> SourceFacts:
    """Analyze a Python file and derive its dotted module name from root."""

    resolved_path = path.resolve()
    resolved_root = root.resolve()
    try:
        relative = resolved_path.relative_to(resolved_root)
    except ValueError as error:
        raise SourceAnalysisError("analyzed file must be beneath its source root") from error
    if relative.suffix != ".py":
        raise SourceAnalysisError("architecture analysis requires a Python source file")

    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    module = ".".join(parts)
    return analyze_source(
        resolved_path.read_text(encoding="utf-8"),
        path=relative.as_posix(),
        module=module,
    )


def analyze_source(source: str, *, path: str, module: str) -> SourceFacts:
    """Parse source without retaining or echoing its potentially sensitive text."""

    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError as error:
        line = error.lineno or 0
        column = error.offset or 0
        raise SourceAnalysisError(
            f"invalid Python source at {path}:{line}:{column}"
        ) from error

    imports = _imports(tree, path)
    aliases = tuple(
        sorted(
            AliasBinding(value.bound_name, _bound_qualified_name(value))
            for value in imports
        )
    )
    alias_map = {value.local_name: value.qualified_name for value in aliases}
    references = tuple(
        sorted(
            ReferenceFact(name, _location(path, node))
            for node in ast.walk(tree)
            if isinstance(node, (ast.Name, ast.Attribute))
            if (name := _qualified_name(node, alias_map)) is not None
        )
    )
    calls = tuple(
        sorted(
            CallFact(
                name,
                _location(path, node),
                _boolean_arguments(node),
                _keyword_arguments(node),
                _first_two_constants_equal(node),
            )
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            if (name := _qualified_name(node.func, alias_map)) is not None
        )
    )
    decorators = tuple(
        sorted(
            _decorator_fact(value, path, alias_map)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for value in node.decorator_list
            if _qualified_name(_decorator_target(value), alias_map) is not None
        )
    )
    functions = tuple(
        sorted(
            _function_fact(node, path, alias_map, parents)
            for node, parents in _definitions(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
    )
    classes = tuple(
        sorted(
            ClassFact(
                qualified_name=".".join((*parents, node.name)),
                decorators=tuple(
                    sorted(
                        _decorator_fact(value, path, alias_map)
                        for value in node.decorator_list
                        if _qualified_name(_decorator_target(value), alias_map)
                        is not None
                    )
                ),
                location=_location(path, node),
            )
            for node, parents in _definitions(tree)
            if isinstance(node, ast.ClassDef)
        )
    )
    handlers = tuple(
        sorted(
            (
                ExceptHandlerFact(
                    _qualified_name(node.type, alias_map),
                    len(node.body) == 1 and isinstance(node.body[0], ast.Pass),
                    _location(path, node),
                )
                for node in ast.walk(tree)
                if isinstance(node, ast.ExceptHandler)
            ),
            key=_except_handler_sort_key,
        )
    )
    assertions = tuple(
        sorted(
            BooleanAssertionFact(node.test.value, _location(path, node))
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert)
            and isinstance(node.test, ast.Constant)
            and isinstance(node.test.value, bool)
        )
    )
    return SourceFacts(
        path=path,
        module=module,
        aliases=aliases,
        imports=imports,
        references=references,
        calls=calls,
        decorators=decorators,
        classes=classes,
        functions=functions,
        except_handlers=handlers,
        boolean_assertions=assertions,
    )


def evaluate_policies(
    facts: tuple[SourceFacts, ...],
    policies: tuple[AstPolicy, ...],
) -> tuple[PolicyFinding, ...]:
    """Evaluate policies deterministically over a set of modules."""

    return tuple(
        sorted(
            finding
            for source in sorted(facts, key=lambda value: (value.module, value.path))
            for policy in policies
            for finding in policy.evaluate(source)
        )
    )


def _imports(tree: ast.AST, path: str) -> tuple[ImportFact, ...]:
    values: list[ImportFact] = []
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=names):
                for alias in names:
                    values.append(
                        ImportFact(
                            ImportKind.IMPORT,
                            alias.name,
                            None,
                            alias.asname or alias.name.split(".", 1)[0],
                            _location(path, node),
                        )
                    )
            case ast.ImportFrom(module=module, names=names, level=level):
                base = f"{'.' * level}{module or ''}"
                for alias in names:
                    values.append(
                        ImportFact(
                            ImportKind.FROM,
                            base,
                            alias.name,
                            alias.asname or alias.name,
                            _location(path, node),
                        )
                    )
    return tuple(sorted(values, key=lambda value: value.location))


def _bound_qualified_name(value: ImportFact) -> str:
    if (
        value.kind is ImportKind.IMPORT
        and "." in value.module
        and value.bound_name == value.module.split(".", 1)[0]
    ):
        return value.bound_name
    return value.qualified_name


def _keyword_arguments(node: ast.Call) -> tuple[KeywordArgumentFact, ...]:
    return tuple(
        sorted(
            KeywordArgumentFact(
                keyword.arg,
                _expression_shape(keyword.value),
                _literal_mapping_keys(keyword.value),
            )
            for keyword in node.keywords
            if keyword.arg is not None
        )
    )


def _expression_shape(node: ast.AST) -> ExpressionShape:
    match node:
        case ast.Dict():
            return ExpressionShape.DICTIONARY
        case ast.List():
            return ExpressionShape.LIST
        case ast.Tuple():
            return ExpressionShape.TUPLE
        case ast.Call():
            return ExpressionShape.CALL
        case ast.Name() | ast.Attribute():
            return ExpressionShape.NAME
        case _:
            return ExpressionShape.OTHER


def _literal_mapping_keys(node: ast.AST) -> tuple[str, ...]:
    if not isinstance(node, ast.Dict):
        return ()
    return tuple(
        sorted(
            key.value
            for key in node.keys
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        )
    )


def _definitions(tree: ast.AST) -> tuple[tuple[ast.AST, tuple[str, ...]], ...]:
    values: list[tuple[ast.AST, tuple[str, ...]]] = []

    def visit(node: ast.AST, parents: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                values.append((child, parents))
                visit(child, (*parents, child.name))
            else:
                visit(child, parents)

    visit(tree, ())
    return tuple(values)


def _function_fact(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    aliases: dict[str, str],
    parents: tuple[str, ...],
) -> FunctionFact:
    return FunctionFact(
        qualified_name=".".join((*parents, node.name)),
        decorators=tuple(
            sorted(
                _decorator_fact(value, path, aliases)
                for value in node.decorator_list
                if _qualified_name(_decorator_target(value), aliases) is not None
            )
        ),
        statement_count=len(node.body),
        pass_only=len(node.body) == 1 and isinstance(node.body[0], ast.Pass),
        empty_body=all(_is_non_behavioral_statement(value) for value in node.body),
        location=_location(path, node),
    )


def _decorator_fact(
    node: ast.expr,
    path: str,
    aliases: dict[str, str],
) -> DecoratorFact:
    name = _qualified_name(_decorator_target(node), aliases)
    if name is None:
        raise SourceAnalysisError("decorator target cannot be represented")
    return DecoratorFact(
        name,
        _location(path, node),
        _boolean_arguments(node) if isinstance(node, ast.Call) else (),
    )


def _decorator_target(node: ast.expr) -> ast.expr:
    return node.func if isinstance(node, ast.Call) else node


def _qualified_name(
    node: ast.AST | None,
    aliases: dict[str, str],
) -> str | None:
    match node:
        case ast.Name(id=name):
            return aliases.get(name, name)
        case ast.Attribute(value=value, attr=attribute):
            owner = _qualified_name(value, aliases)
            return None if owner is None else f"{owner}.{attribute}"
        case _:
            return None


def _location(path: str, node: ast.AST) -> SourceLocation:
    return SourceLocation(path, getattr(node, "lineno", 0), getattr(node, "col_offset", 0))


def _except_handler_sort_key(
    value: ExceptHandlerFact,
) -> tuple[SourceLocation, str, bool]:
    """Give optional exception names a total, deterministic ordering."""

    return (value.location, value.exception_name or "", value.pass_only)


def _is_non_behavioral_statement(node: ast.stmt) -> bool:
    return isinstance(node, ast.Pass) or (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and (isinstance(node.value.value, str) or node.value.value is Ellipsis)
    )


def _first_two_constants_equal(node: ast.Call) -> bool:
    return (
        len(node.args) >= 2
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[1], ast.Constant)
        and node.args[0].value == node.args[1].value
    )


def _boolean_arguments(node: ast.Call) -> tuple[BooleanArgumentFact, ...]:
    return tuple(
        BooleanArgumentFact(index, argument.value)
        for index, argument in enumerate(node.args)
        if isinstance(argument, ast.Constant)
        and isinstance(argument.value, bool)
    )
