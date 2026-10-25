from __future__ import annotations

import argparse
import json
from pathlib import Path


REVIEWED_FIXES = {
    ("rgbd_color_001384", 4, "top_right"): [293.2, 12.9],
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    changed = 0
    for json_path in sorted((args.dataset / "images").glob("*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        for shape in data["shapes"]:
            key = (json_path.stem, shape.get("group_id"), shape.get("label"))
            if key not in REVIEWED_FIXES:
                continue
            shape["points"] = [REVIEWED_FIXES[key]]
            shape["score"] = None
            shape["description"] = (
                "Manually corrected after enlarged visual review of the "
                "table-boundary screw."
            )
            changed += 1
        if any(key[0] == json_path.stem for key in REVIEWED_FIXES):
            json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    print(f"changed={changed}")


if __name__ == "__main__":
    main()
