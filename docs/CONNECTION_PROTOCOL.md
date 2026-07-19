# Connection Protocol

Socket compatibility uses a closed product rather than a flat protocol label:

```text
Protocol = Transport x ApplicationProtocol
```

`Transport` currently contains TCP and UDP. `ApplicationProtocol` contains the
semantic protocols required by the package catalogue: raw traffic, HTTP,
Postgres, DNS, Redis, SMTP, OTLP over HTTP or gRPC, NATS, AMQP, Kafka, and S3.

The product rejects invalid combinations at construction. HTTP over UDP and
Postgres over UDP therefore cannot enter a graph. DNS and raw traffic explicitly
support both transports and remain distinct values:

```python
Protocol.DNS_TCP
Protocol.DNS_UDP
Protocol.TCP
Protocol.UDP
```

Compatibility is exact equality of both factors:

```text
compatible(a, b)
  iff a.transport = b.transport
  and a.application = b.application
```

Transport reachability does not imply application health. Runtime interpreters
may prove that a TCP connection or bounded UDP exchange succeeded, while a
product-specific verification interpreter separately proves DNS, Postgres,
Redis, broker, object-storage, SMTP, or telemetry semantics.

The compact `value` names temporarily preserve the current descriptor boundary
within issue #451. Issue #452 replaces those durable scalar fields with the
structured product in place; no second legacy protocol language is retained.
