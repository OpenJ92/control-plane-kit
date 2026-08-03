# Current Backend Validation

This directory owns the current multi-repository backend validation tooling.
It never imports or executes the mutable root `control_plane_kit` package.

`current-backend.lock.json` contains one root source coordinate: an immutable
`control-plane-kit-servers` commit. That commit's
`coordinates/server-products.json` supplies the exact core/operations,
interpreters, and secrets commits. This preserves the server-products
coordinate manifest as the source of truth instead of copying every upstream
pin into another file.

Source resolution has two explicit forms:

- local mode reads immutable Git objects from supplied repositories and ignores
  mutable checkout contents;
- clone mode fetches only the locked commits into temporary Git object stores.

Both forms materialize validated source archives into temporary directories and
remove them on success or failure. They never switch, stash, reset, clean, or
write into a developer checkout.

`current-backend.contracts.json` is the closed architecture and composition
contract for those exact trees. `current_backend.contracts.validate_backend`
checks:

- exhaustive ownership of current Python source files;
- declared and parsed internal dependency edges, including cycles and reverse
  dependencies;
- forbidden concrete SDK, provider, and transport imports;
- server-product coordinate pins and generated descriptor/catalogue checksums;
- structural runtime, ingress, gateway-probe, and secret-provider protocols;
- named live-acceptance classification and cpk-server caller ownership.

The contract validator is static evidence. It does not import application
packages or perform Docker, provider, network, or filesystem effects against a
runtime. The executable backend gate owns package execution and the separately
named source-live and residue stages.
