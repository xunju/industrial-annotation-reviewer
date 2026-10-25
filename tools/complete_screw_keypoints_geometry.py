from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from annotate_screw_shim import KEYPOINT_NAMES, base_shape


MANUAL_POINTS = {
    "0717_color_000705": {
        5: {
            "bot_left": [511.5, 224.0],
            "bot_right": [529.0, 226.5],
        }
    },
    "rgbd_color_001345": {
        4: {
            "top_left": [310.0, 8.0],
            "top_right": [309.5, 24.0],
        }
    },
}


def parallelogram_point(points: dict[str, np.ndarray], missing: str) -> np.ndarray:
    if missing == "top_left":
        return points["top_right"] + points["bot_right"] - points["bot_left"]
    if missing == "top_right":
        return points["top_left"] + points["bot_left"] - points["bot_right"]
    if missing == "bot_left":
        return points["top_left"] + points["bot_right"] - points["top_right"]
    return points["top_left"] + points["bot_left"] - points["top_right"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    added = 0
    remaining = 0
    for json_path in sorted((args.dataset / "images").glob("*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        changed = False
        for screw in [shape for shape in data["shapes"] if shape["label"] == "screw"]:
            group_id = screw.get("group_id")
            grouped = {
                shape["label"]: shape
                for shape in data["shapes"]
                if shape.get("group_id") == group_id
                and shape["label"] in KEYPOINT_NAMES
            }
            missing = [name for name in KEYPOINT_NAMES if name not in grouped]
            if not missing:
                continue
            additions: dict[str, list[float]] = {}
            manual = MANUAL_POINTS.get(json_path.stem, {}).get(group_id, {})
            additions.update(manual)
            if len(missing) == 1 and missing[0] not in additions:
                known = {
                    name: np.asarray(shape["points"][0], dtype=np.float32)
                    for name, shape in grouped.items()
                }
                additions[missing[0]] = parallelogram_point(
                    known, missing[0]
                ).tolist()
            for name in missing:
                if name not in additions:
                    remaining += 1
                    continue
                point = additions[name]
                shape = base_shape(name, 1.0, [point], "point", group_id)
                shape["score"] = None
                shape["description"] = (
                    "Manually reviewed completion from visible screw corners; "
                    "single missing points use the parallelogram relation."
                )
                data["shapes"].append(shape)
                added += 1
                changed = True
                print(f"{json_path.stem} group={group_id} {name}={point}")
        if changed and args.apply:
            json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
    print(f"added={added} remaining={remaining} apply={args.apply}")


if __name__ == "__main__":
    main()
