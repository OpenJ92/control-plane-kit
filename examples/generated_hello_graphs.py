"""Deterministic valid and invalid Hello deployment graph fixtures."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TypeAlias

from control_plane_kit import (
    BlockSockets,
    BlockSpec,
    DataBlock,
    DeploymentGraph,
    DeploymentRecipe,
    DockerPostgresImplementation,
    DockerRuntime,
    ProviderSocket,
    SocketConnection,
    compile_recipe,
)
from control_plane_kit.servers import HelloDependency, hello_server_block
from control_plane_kit.types import Protocol


@dataclass(frozen=True)
class HelloGraphShape:
    """Finite tree shape used to generate a deployment graph."""

    branching_factor: int
    depth: int
    runtime_id: str = "hello-stress-runtime"
    network_name: str = "cpk-hello-stress"
    image: str = "control-plane-kit-live-test:local"
    root_host_port: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.branching_factor, int) or not 0 <= self.branching_factor <= 4:
            raise ValueError("branching_factor must be between 0 and 4")
        if not isinstance(self.depth, int) or not 0 <= self.depth <= 4:
            raise ValueError("depth must be between 0 and 4")
        if self.depth and not self.branching_factor:
            raise ValueError("a positive depth requires a positive branching_factor")
        if self.application_count > 128:
            raise ValueError("generated Hello graph exceeds the 128-application bound")
        for name, value in (
            ("runtime_id", self.runtime_id),
            ("network_name", self.network_name),
            ("image", self.image),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.root_host_port is not None and not 1 <= self.root_host_port <= 65_535:
            raise ValueError("root_host_port must be between 1 and 65535")

    @property
    def application_count(self) -> int:
        if self.branching_factor == 0:
            return 1
        return sum(self.branching_factor**level for level in range(self.depth + 1))

    @property
    def database_count(self) -> int:
        return self.application_count - 1

    @property
    def edge_count(self) -> int:
        return 2 * self.database_count


@dataclass(frozen=True)
class MissingHttpConnection:
    consumer_id: str = "hello-root"
    dependency_name: str = "branch-1"


@dataclass(frozen=True)
class MissingDatabaseConnection:
    consumer_id: str = "hello-root"
    dependency_name: str = "branch-1"


@dataclass(frozen=True)
class DuplicateRequirementConnection:
    consumer_id: str = "hello-root"
    dependency_name: str = "branch-1"


@dataclass(frozen=True)
class CorruptEnvironmentAssignment:
    consumer_id: str = "hello-root"
    dependency_name: str = "branch-1"


HelloGraphInvalidity: TypeAlias = (
    MissingHttpConnection
    | MissingDatabaseConnection
    | DuplicateRequirementConnection
    | CorruptEnvironmentAssignment
)


def generated_hello_recipe(shape: HelloGraphShape) -> DeploymentRecipe:
    """Generate one valid Docker recipe from a finite tree shape."""

    if not isinstance(shape, HelloGraphShape):
        raise TypeError("shape must be HelloGraphShape")
    children: list[object] = []

    def add_application(path: tuple[int, ...], level: int) -> str:
        node_id = _application_id(path)
        dependencies = (
            tuple(
                HelloDependency(f"branch-{branch}")
                for branch in range(1, shape.branching_factor + 1)
            )
            if level < shape.depth
            else ()
        )
        children.append(
            hello_server_block(
                node_id,
                message=f"Hello from {node_id}!",
                image=shape.image,
                host_port=shape.root_host_port if not path else None,
                dependencies=dependencies,
            )
        )
        for branch, dependency in enumerate(dependencies, start=1):
            child_id = add_application((*path, branch), level + 1)
            database_id = f"{child_id}-database"
            children.append(_database(database_id))
            children.extend(
                (
                    SocketConnection(
                        child_id,
                        "internal",
                        node_id,
                        dependency.http_socket,
                        edge_id=_http_edge_id(node_id, dependency.name),
                    ),
                    SocketConnection(
                        database_id,
                        "internal",
                        node_id,
                        dependency.database_socket,
                        edge_id=_database_edge_id(node_id, dependency.name),
                    ),
                )
            )
        return node_id

    add_application((), 0)
    return DeploymentRecipe(
        f"generated-hello-{shape.branching_factor}x{shape.depth}",
        DockerRuntime(
            runtime_id=shape.runtime_id,
            network_name=shape.network_name,
            children=tuple(children),
        ),
    )


def generated_hello_graph(
    shape: HelloGraphShape,
    invalidity: HelloGraphInvalidity | None = None,
) -> DeploymentGraph:
    """Compile a generated recipe and optionally apply one explicit defect."""

    graph = compile_recipe(generated_hello_recipe(shape))
    if invalidity is None:
        return graph
    if shape.depth == 0:
        raise ValueError("generated invalidity requires at least one dependency")
    match invalidity:
        case MissingHttpConnection(consumer_id=consumer, dependency_name=name):
            return _without_edge(graph, _http_edge_id(consumer, name))
        case MissingDatabaseConnection(consumer_id=consumer, dependency_name=name):
            return _without_edge(graph, _database_edge_id(consumer, name))
        case DuplicateRequirementConnection(
            consumer_id=consumer,
            dependency_name=name,
        ):
            edge = _edge(graph, _http_edge_id(consumer, name))
            return graph.add_edge(replace(edge, edge_id=f"{edge.edge_id}.duplicate"))
        case CorruptEnvironmentAssignment(
            consumer_id=consumer,
            dependency_name=name,
        ):
            edge = _edge(graph, _http_edge_id(consumer, name))
            malformed = replace(
                edge,
                env_assignments={"UNDECLARED_HTTP_URL": next(iter(edge.env_assignments.values()))},
            )
            return replace(graph, edges={**graph.edges, edge.edge_id: malformed})
    raise TypeError("invalidity must be a HelloGraphInvalidity")


def _database(database_id: str) -> DataBlock:
    return DataBlock(
        BlockSpec(database_id, f"Database for {database_id}"),
        DockerPostgresImplementation(database="hello"),
        BlockSockets(providers=(ProviderSocket("internal", Protocol.POSTGRES),)),
    )


def _application_id(path: tuple[int, ...]) -> str:
    return "hello-root" if not path else "hello-" + "-".join(map(str, path))


def _http_edge_id(consumer_id: str, dependency_name: str) -> str:
    return f"{consumer_id}.{dependency_name}.http"


def _database_edge_id(consumer_id: str, dependency_name: str) -> str:
    return f"{consumer_id}.{dependency_name}.database"


def _edge(graph: DeploymentGraph, edge_id: str):
    try:
        return graph.edges[edge_id]
    except KeyError as error:
        raise ValueError(f"generated graph has no edge {edge_id!r}") from error


def _without_edge(graph: DeploymentGraph, edge_id: str) -> DeploymentGraph:
    _edge(graph, edge_id)
    return replace(
        graph,
        edges={key: value for key, value in graph.edges.items() if key != edge_id},
    )

