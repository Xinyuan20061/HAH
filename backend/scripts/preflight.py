"""Configuration-only preflight: does not connect to or mutate the database."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings


def main() -> int:
    try:
        config = Settings()
        config.validate_configuration()
        print(json.dumps(config.safe_summary(), ensure_ascii=False))
        print("[OK] configuration")
        return 0
    except ValueError as exc:
        # Settings validation can contain submitted secrets: only show field names for Pydantic errors.
        if hasattr(exc, "errors"):
            for error in exc.errors():
                print("[ERROR]", ".".join(map(str, error["loc"])), error["type"])
        else:
            print(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
