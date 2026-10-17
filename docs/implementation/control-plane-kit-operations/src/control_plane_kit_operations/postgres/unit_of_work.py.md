Source: [unit_of_work.py](../../../../../../control-plane-kit-operations/src/control_plane_kit_operations/postgres/unit_of_work.py).
Maintain this companion alongside its source.

Ordinary UoW entry retains its commit-request/rollback behavior. The explicit
`read_snapshot()` capability is fresh/unentered-only and requires an idle factory
connection. It begins `REPEATABLE READ READ ONLY` before any data selector and
vends graph-owned bounded receiver reads on that connection. Setup failures,
projection failures and successful reads all rollback and close; none commit.

Consistency is the complete committed database snapshot at first data read.
There is no lifecycle/advisory lock, retry, end-of-read freshness claim or provider
I/O. A later mutation must independently revalidate its captured expectations.
