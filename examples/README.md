# Examples

These examples are intentionally graph-first.  They print descriptors, diffs,
or activity plans.  They do not require Docker or network access.

```bash
python3 examples/api_blue_green.py
python3 examples/postgres_switch.py
python3 examples/local_cloudflare_auth.py
```

## Blue/Green API

The stable relationship is:

```text
auth -> api-router
```

The mutable relationship is:

```text
api-router -> api-v1
api-router -> api-v2
```

That lets the operator start `api-v2`, switch the router, verify the public edge,
and then retire `api-v1`.

## Postgres Switch

The stable relationship is:

```text
api -> postgres-switch
```

The mutable relationship is:

```text
postgres-switch -> postgres-v1
postgres-switch -> postgres-v2
```

This is only a safe TCP/Postgres target switch.  It does not inspect SQL and does
not replace real database migration verification.
