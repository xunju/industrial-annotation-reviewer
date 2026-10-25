from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np

from annotate_screw_shim import KEYPOINT_NAMES
from repair_missing_shim_inner import matched_indices


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    image_dir = args.dataset / "images"
    rows = []
    errors = []
    totals = {
        "images": 0,
        "screws": 0,
        "keypoints": 0,
        "wai": 0,
        "nei": 0,
    }
    for image_path in sorted(image_dir.glob("*.jpg")):
        totals["images"] += 1
        json_path = image_path.with_suffix(".json")
        if not json_path.exists():
            errors.append(f"{image_path.name}: missing JSON")
            continue
        image = cv2.imdecode(
            np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        height, width = image.shape[:2]
        data = json.loads(json_path.read_text(encoding="utf-8"))
        shapes = data.get("shapes", [])
        screws = [s for s in shapes if s["label"] == "screw"]
        keypoints = [s for s in shapes if s["label"] in KEYPOINT_NAMES]
        outer = [s for s in shapes if s["label"] == "wai"]
        inner = [s for s in shapes if s["label"] == "nei"]
        used_outer, used_inner = matched_indices(outer, inner)
        incomplete_groups = 0
        keypoints_outside_screw = 0
        for screw in screws:
            grouped = [
                p for p in keypoints if p.get("group_id") == screw.get("group_id")
            ]
            names = [p["label"] for p in grouped]
            if sorted(names) != sorted(KEYPOINT_NAMES):
                incomplete_groups += 1
            screw_points = np.asarray(screw["points"], dtype=np.float32)
            x_min, y_min = screw_points.min(axis=0)
            x_max, y_max = screw_points.max(axis=0)
            for point_shape in grouped:
                x, y = point_shape["points"][0]
                if not (x_min <= x <= x_max and y_min <= y <= y_max):
                    keypoints_outside_screw += 1
        out_of_bounds = 0
        invalid_shapes = 0
        for shape in shapes:
            points = np.asarray(shape.get("points", []), dtype=np.float32)
            if len(points) == 0 or not np.isfinite(points).all():
                invalid_shapes += 1
                continue
            if (
                (points[:, 0] < 0).any()
                or (points[:, 0] > width - 1).any()
                or (points[:, 1] < 0).any()
                or (points[:, 1] > height - 1).any()
            ):
                out_of_bounds += 1
        if incomplete_groups:
            errors.append(
                f"{image_path.name}: incomplete screw groups={incomplete_groups}"
            )
        if keypoints_outside_screw:
            errors.append(
                f"{image_path.name}: keypoints outside screw="
                f"{keypoints_outside_screw}"
            )
        if len(used_outer) != len(outer) or len(used_inner) != len(inner):
            errors.append(
                f"{image_path.name}: unmatched wai={len(outer)-len(used_outer)} "
                f"nei={len(inner)-len(used_inner)}"
            )
        if invalid_shapes or out_of_bounds:
            errors.append(
                f"{image_path.name}: invalid={invalid_shapes} "
                f"out_of_bounds={out_of_bounds}"
            )
        row = {
            "image": image_path.name,
            "screws": len(screws),
            "screw_keypoints": len(keypoints),
            "shim_wai": len(outer),
            "shim_nei": len(inner),
            "incomplete_screw_groups": incomplete_groups,
            "keypoints_outside_screw": keypoints_outside_screw,
            "unmatched_wai": len(outer) - len(used_outer),
            "unmatched_nei": len(inner) - len(used_inner),
            "invalid_shapes": invalid_shapes,
            "out_of_bounds_shapes": out_of_bounds,
        }
        rows.append(row)
        totals["screws"] += len(screws)
        totals["keypoints"] += len(keypoints)
        totals["wai"] += len(outer)
        totals["nei"] += len(inner)

    with (args.dataset / "inference_audit.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    report = [
        "Screw and shim dataset validation",
        f"images: {totals['images']}",
        f"screws: {totals['screws']}",
        f"screw keypoints: {totals['keypoints']}",
        f"shim outer (wai): {totals['wai']}",
        f"shim inner (nei): {totals['nei']}",
        f"errors: {len(errors)}",
    ]
    if errors:
        report.append("")
        report.extend(errors)
    (args.dataset / "validation_report.txt").write_text(
        "\n".join(report) + "\n", encoding="utf-8"
    )
    print("\n".join(report[:7]))
    if errors:
        print("\n".join(errors[:30]))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
