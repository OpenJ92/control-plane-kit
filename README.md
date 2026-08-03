# control-plane-kit-servers

Reusable OCI server products and descriptors for `control-plane-kit`.

## Validation

The authoritative clean-checkout package gate is:

```bash
./test.sh
```

It checks generated coordinates, applies the shared package-integrity contract,
discovers every product test package, compiles current source and tests, runs
all unittests in Docker, verifies a clean installed import, exercises a real
cpk-server image/process smoke, and rejects exact-owned Docker residue. Source-
live and published/provider-mutating scenarios remain separate acceptance
evidence and are not silently substituted by this package gate.

This repository owns independently packaged server-product lanes and the
catalogue that publishes their immutable descriptors and OCI coordinates.
Current lanes include:

```text
products/cpk_server
products/hello_server
products/http_active_router
products/http_multiplexer
products/cpk_local_gateway
products/cloudflared_connector
products/postgres_server
products/secrets_server
```

`control-plane-kit-core` never imports this repository. Server products may
import the pinned core release candidate to express descriptors, socket
contracts, and process handoff contracts.

See:

- `coordination/core-release-candidate.json`
- `coordination/product-inventory.json`
- `coordination/extract-f-804-cpk-server-handoff.json`
- `docs/issue-transfer-strategy.md`
- `docs/decisions/0001-repository-foundation.md`
- `docs/security/0001-foundation-review.md`

Current package surface:

```python
from control_plane_kit_servers import load_catalogue

product_ids = {item.product_id for item in load_catalogue()}
assert {"cpk-server", "hello-server", "postgres-server"} <= product_ids
```

The catalogue contains completed-product publication records only. Loading it
does not import product process code.

Publication source and generated artifacts are intentionally separate:

```text
catalogue/products.json
  -> scripts/publish_catalogue.py
    -> dist/server-products.json
    -> dist/server-products.json.sha256
```

`load_catalogue()` reads completed publication records only. It never imports
product process code, FastAPI apps, Docker clients, stores, or entrypoints.


## cpk-server Image Foundation

`products/cpk_server` now contains a runnable image definition for the
control-plane process wrapper. This is now paired with a published descriptor in
`products/cpk_server/product.cpk.json` and a catalogue declaration containing
descriptor, image, and source digest evidence.

Local smoke:

```bash
sh scripts/cpk_server_image_smoke.sh
```


## Publishing Product Images

The first product image is published at:

```text
ghcr.io/openj92/control-plane-kit-servers/cpk-server@sha256:dacf70bb1dac886d24a7abdf101cf9a95bfd5ed54cef036a59fce810c7b76d9e
```

Per-product publication uses:

```bash
sh scripts/publish_product_image.sh cpk-server extract-ops-848
```

Each product must be admitted explicitly by the script. There is no broad
publish-all path.


Current GHCR visibility:

```text
https://github.com/users/OpenJ92/packages/container/package/control-plane-kit-servers%2Fcpk-server
visibility: private
```

Authenticated Docker Desktop and GitHub Actions can pull the digest. Public
unauthenticated pulls require an explicit package visibility decision.
