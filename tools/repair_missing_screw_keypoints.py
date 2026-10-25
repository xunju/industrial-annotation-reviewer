from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from annotate_screw_shim import KEYPOINT_NAMES, infer_pose


def rectangle_box(shape: dict) -> np.ndarray:
    points = np.asarray(shape["points"], dtype=np.float32)
    return np.array(
        [points[:, 0].min(), points[:, 1].min(), points[:, 0].max(), points[:, 1].max()],
        dtype=np.float32,
    )


def iou(a: np.ndarray, b: np.ndarray) -> float:
    lt = np.maximum(a[:2], b[:2])
    rb = np.minimum(a[2:], b[2:])
    wh = np.maximum(0.0, rb - lt)
    intersection = float(wh[0] * wh[1])
    area_a = float(max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1]))
    area_b = float(max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1]))
    return intersection / max(area_a + area_b - intersection, 1e-9)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    session = ort.InferenceSession(str(args.model), providers=["CPUExecutionProvider"])
    angles = np.linspace(0, 2 * np.pi, 128, endpoint=False)
    table = np.column_stack(
        [320.0 + 318.0 * np.cos(angles), 320.0 + 318.0 * np.sin(angles)]
    ).astype(np.float32)

    affected = 0
    added = 0
    unresolved = 0
    details: list[str] = []
    for json_path in sorted((args.dataset / "images").glob("*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        screws = [s for s in data["shapes"] if s["label"] == "screw"]
        point_shapes = [
            s for s in data["shapes"] if s["label"] in KEYPOINT_NAMES
        ]
        missing_total = sum(
            4 - len([p for p in point_shapes if p.get("group_id") == screw.get("group_id")])
            for screw in screws
        )
        if missing_total <= 0:
            continue

        image = cv2.imdecode(
            np.fromfile(json_path.with_suffix(".jpg"), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        low_shapes = infer_pose(
            session,
            image,
            table,
            conf_threshold=0.25,
            iou_threshold=0.60,
            keypoint_threshold=0.02,
        )
        low_screws = [s for s in low_shapes if s["label"] == "screw"]
        low_points = [s for s in low_shapes if s["label"] in KEYPOINT_NAMES]

        additions: list[dict] = []
        for screw in screws:
            group_id = screw.get("group_id")
            existing_names = {
                p["label"] for p in point_shapes if p.get("group_id") == group_id
            }
            missing_names = [name for name in KEYPOINT_NAMES if name not in existing_names]
            if not missing_names:
                continue
            current_box = rectangle_box(screw)
            choices = [
                (iou(current_box, rectangle_box(candidate)), candidate)
                for candidate in low_screws
            ]
            best_iou, best_screw = max(choices, key=lambda item: item[0])
            if best_iou < 0.75:
                unresolved += len(missing_names)
                continue
            low_group = best_screw.get("group_id")
            by_name = {
                p["label"]: p
                for p in low_points
                if p.get("group_id") == low_group
            }
            for name in missing_names:
                candidate = by_name.get(name)
                if candidate is None:
                    unresolved += 1
                    continue
                candidate = dict(candidate)
                candidate["group_id"] = group_id
                candidate["description"] = (
                    "Recovered from the same pose detection using a lower "
                    "keypoint-visibility threshold, then matched by screw-box IoU."
                )
                additions.append(candidate)

        if additions:
            affected += 1
            added += len(additions)
            details.append(f"{json_path.stem}: +{len(additions)}/{missing_total}")
            if args.apply:
                data["shapes"].extend(additions)
                json_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

    print("\n".join(details))
    print(
        f"affected_images={affected} added={added} unresolved={unresolved} "
        f"apply={args.apply}"
    )


if __name__ == "__main__":
    main()
