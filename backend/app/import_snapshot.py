"""Import an existing private organizer snapshot without a network request."""

import argparse
import json
from pathlib import Path

from fastapi import HTTPException

from backend.app.team_data import store_group, validate_group


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--group-id", default="DEV")
    args = parser.parse_args()
    try:
        payload = json.loads(args.snapshot.read_text(encoding="utf-8-sig"))
        result = store_group(args.group_id, validate_group(args.group_id, payload))
    except HTTPException as error:
        parser.exit(1, f"Import rejected ({error.status_code}): {error.detail}\n")
    except (OSError, ValueError):
        parser.exit(1, "Cannot read a valid local JSON snapshot.\n")
    print(result.model_dump_json())


if __name__ == "__main__":
    main()
