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
