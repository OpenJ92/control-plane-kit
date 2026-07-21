from __future__ import annotations

import json
import os
from pathlib import Path


def main() -> int:
    behavior_path = Path(os.environ["CPK_PARITY_BEHAVIOR_PATH"])
    port = int(os.environ["SCENARIO_PORT"])
    response = os.environ.get("SCENARIO_RESPONSE", "hello")
    artifact_path = Path(os.environ["SCENARIO_ARTIFACT_PATH"])

    artifact_path.write_text(f"response={response}\n", encoding="utf-8")
    behavior_path.write_text(
        json.dumps(
            {
                "response": response,
                "allocated_port": {
                    "$incidental": {
                        "kind": "allocated-port",
                        "value": port,
                    },
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
