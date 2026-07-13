# Design

`control-plane-kit` is organized around one product form:

```text
DeployBlock = Spec x RuntimeImplementation x RoleSockets
```

## Spec

A spec identifies the thing being deployed. Different block families can use
different specs: `AppSpec`, `DataSpec`, `ProxySpec`. Specs should contain stable
identity and metadata, not runtime effects.

## RuntimeImplementation

An implementation says how the block exists under a runtime. Examples:

- Docker image
- local source command
- external HTTP URL
- external Postgres URL
- Docker Postgres
- plan-only router

Implementations do not own the runtime. They are interpreted by the enclosing
runtime context.

## RoleSockets

Sockets are the communication contract.

- `ProviderSocket`: an endpoint or capability the node provides.
- `EnvironmentRequirementSocket`: a startup requirement fulfilled by assigning
  one or more environment variables from a provider endpoint.
- `RuntimeRequirementSocket`: a live requirement fulfilled after startup through
  a control route, such as registering router targets.

A socket connection wires a provider socket to a requirement socket.

## Runtime Contexts

Runtime contexts are topology, not invisible implementation details.  A
deployment can contain several runtime contexts at once:

```text
DeploymentRecipe
  root:
    RuntimeContext("local-docker-a")
      blocks...
      socket connections...

    RuntimeContext("local-docker-b")
      blocks...
      socket connections...

    RuntimeContext("aws-ecs-prod")
      blocks...
      socket connections...

    RuntimeContext("external-rds")
      data blocks...

    cross-runtime SocketConnection(...)
```

The invariant is:

```text
RuntimeContext is topology.
Docker is only one interpreter of one runtime kind.
```

A `SocketConnection` connects provider node + provider socket to consumer node +
requirement socket.  It should not care whether the provider and consumer live
inside one Docker network, two Docker networks, ECS, EC2, RDS, Kubernetes, or an
externally managed service.  Runtime interpreters decide how compiled graph
connections become usable addresses, environment values, control-route calls,
network policies, service-discovery names, or observe-only references.

That means the Docker runtime must prove the pattern without owning it:

```text
DockerRuntimeInterpreter
  -> Docker network names / published ports / container env

EcsRuntimeInterpreter
  -> service discovery / task env / security groups

ExternalRuntimeInterpreter
  -> predeclared URLs / secrets / observe-only nodes
```

Interpreters should operate over one runtime record at a time while still seeing
the whole graph, because cross-runtime edges are first-class topology.

## Compiler

The compiler walks the recipe tree, materializes blocks into graph nodes, then
applies socket connections. Connections validate protocol compatibility and
write environment assignments into the consumer node.

## Graph

The compiled graph is pure data. It has nodes, edges, environment assignments,
runtime context records, and descriptors. Runtime interpreters can act on this
later.
