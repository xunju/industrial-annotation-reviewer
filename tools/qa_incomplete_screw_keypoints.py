from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from annotate_screw_shim import KEYPOINT_NAMES


COLORS = {
    "top_left": (0, 0, 255),
    "top_right": (0, 180, 255),
    "bot_left": (255, 0, 255),
    "bot_right": (255, 0, 0),
}


def main() -> None:
    parser = argparse.ArgumentParser(description="Render screws with missing or flagged keypoints for review.")
    parser.add_argument("dataset", type=Path, help="Directory containing JPG images and matching JSON files")
    parser.add_argument("output", type=Path, help="Directory for generated review images")
    args = parser.parse_args()
    dataset = args.dataset.resolve()
    output = args.output.resolve()
    if not dataset.is_dir():
        parser.error(f"Dataset directory does not exist: {dataset}")
    output.mkdir(parents=True, exist_ok=True)
    for json_path in sorted(dataset.glob("*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        points = [s for s in data["shapes"] if s["label"] in KEYPOINT_NAMES]
        incomplete = []
        for screw in [s for s in data["shapes"] if s["label"] == "screw"]:
            grouped = [p for p in points if p.get("group_id") == screw.get("group_id")]
            if len(grouped) < 4 or any(
                point.get("description") for point in grouped
            ):
                incomplete.append((screw, grouped))
        if not incomplete:
            continue
        image = cv2.imdecode(
            np.fromfile(json_path.with_suffix(".jpg"), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        for screw, grouped in incomplete:
            contour = np.rint(np.asarray(screw["points"])).astype(np.int32)
            cv2.polylines(image, [contour.reshape(-1, 1, 2)], True, (0, 255, 255), 2)
            for point in grouped:
                x, y = np.rint(point["points"][0]).astype(int)
                cv2.circle(image, (x, y), 5, COLORS[point["label"]], -1)
                cv2.putText(
                    image,
                    point["label"],
                    (x + 5, max(12, y - 5)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.42,
                    COLORS[point["label"]],
                    1,
                    cv2.LINE_AA,
                )
        all_box_points = np.concatenate(
            [np.asarray(screw["points"], dtype=np.float32) for screw, _ in incomplete],
            axis=0,
        )
        x0 = max(0, int(all_box_points[:, 0].min()) - 60)
        y0 = max(0, int(all_box_points[:, 1].min()) - 60)
        x1 = min(image.shape[1], int(all_box_points[:, 0].max()) + 60)
        y1 = min(image.shape[0], int(all_box_points[:, 1].max()) + 60)
        crop = image[y0:y1, x0:x1]
        scale = min(5.0, 1000.0 / max(crop.shape[:2]))
        enlarged = cv2.resize(
            crop,
            (round(crop.shape[1] * scale), round(crop.shape[0] * scale)),
            interpolation=cv2.INTER_CUBIC,
        )
        cv2.imencode(".png", enlarged)[1].tofile(output / f"{json_path.stem}.png")
    print(f"output={output}")


if __name__ == "__main__":
    main()
