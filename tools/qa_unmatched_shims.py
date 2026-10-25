from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import cv2
import numpy as np

from repair_missing_shim_inner import center_and_radius, matched_indices


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    individual = args.output / "individual"
    individual.mkdir(parents=True, exist_ok=True)

    tiles: list[np.ndarray] = []
    rows: list[dict] = []
    for json_path in sorted((args.dataset / "images").glob("*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        outer = [shape for shape in data["shapes"] if shape["label"] == "wai"]
        inner = [shape for shape in data["shapes"] if shape["label"] == "nei"]
        used_outer, _ = matched_indices(outer, inner)
        unmatched = [
            (index, shape)
            for index, shape in enumerate(outer)
            if index not in used_outer
        ]
        if not unmatched:
            continue
        image = cv2.imdecode(
            np.fromfile(json_path.with_suffix(".jpg"), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        for local_index, (outer_index, shape) in enumerate(unmatched, 1):
            points = np.asarray(shape["points"], dtype=np.float32)
            center, radius = center_and_radius(shape)
            margin = max(24, int(radius * 2.8))
            x0 = max(0, int(points[:, 0].min()) - margin)
            y0 = max(0, int(points[:, 1].min()) - margin)
            x1 = min(image.shape[1], int(points[:, 0].max()) + margin)
            y1 = min(image.shape[0], int(points[:, 1].max()) + margin)
            crop = image[y0:y1, x0:x1].copy()
            local_points = points - np.array([x0, y0], dtype=np.float32)
            cv2.polylines(
                crop,
                [np.rint(local_points).astype(np.int32).reshape(-1, 1, 2)],
                True,
                (255, 0, 255),
                2,
                cv2.LINE_AA,
            )
            tile = np.zeros((330, 300, 3), dtype=np.uint8)
            scale = min(300 / crop.shape[1], 300 / crop.shape[0])
            resized = cv2.resize(
                crop,
                (round(crop.shape[1] * scale), round(crop.shape[0] * scale)),
                interpolation=cv2.INTER_CUBIC,
            )
            ox = (300 - resized.shape[1]) // 2
            oy = (300 - resized.shape[0]) // 2
            tile[oy : oy + resized.shape[0], ox : ox + resized.shape[1]] = resized
            item_id = f"{json_path.stem}__{local_index}"
            cv2.putText(
                tile,
                item_id,
                (3, 320),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (0, 255, 255),
                1,
                cv2.LINE_AA,
            )
            tiles.append(tile)
            cv2.imencode(".png", tile)[1].tofile(individual / f"{item_id}.png")
            rows.append(
                {
                    "item_id": item_id,
                    "image": json_path.name,
                    "outer_index": outer_index,
                    "center_x": round(float(center[0]), 3),
                    "center_y": round(float(center[1]), 3),
                    "radius": round(float(radius), 3),
                    "score": shape.get("score"),
                }
            )

    per_page = 20
    for page_index in range(math.ceil(len(tiles) / per_page)):
        page = np.zeros((5 * 330, 4 * 300, 3), dtype=np.uint8)
        for index, tile in enumerate(
            tiles[page_index * per_page : (page_index + 1) * per_page]
        ):
            row, column = divmod(index, 4)
            page[row * 330 : (row + 1) * 330, column * 300 : (column + 1) * 300] = tile
        cv2.imencode(".jpg", page, [cv2.IMWRITE_JPEG_QUALITY, 96])[1].tofile(
            args.output / f"qa_{page_index + 1:03d}.jpg"
        )
    with (args.output / "unmatched.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys() if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    print(f"unmatched={len(rows)} pages={math.ceil(len(tiles) / per_page)}")


if __name__ == "__main__":
    main()
