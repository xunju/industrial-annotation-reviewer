from __future__ import annotations

import argparse
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
    tiles = []
    for json_path in sorted((args.dataset / "images").glob("*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        outer = [s for s in data["shapes"] if s["label"] == "wai"]
        inner = [s for s in data["shapes"] if s["label"] == "nei"]
        used_outer, used_inner = matched_indices(outer, inner)
        unmatched_outer = [
            s for index, s in enumerate(outer) if index not in used_outer
        ]
        unmatched_inner = [
            s for index, s in enumerate(inner) if index not in used_inner
        ]
        if not unmatched_outer and not unmatched_inner:
            continue
        image = cv2.imdecode(
            np.fromfile(json_path.with_suffix(".jpg"), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        target_shapes = unmatched_outer + unmatched_inner
        centers = [center_and_radius(shape)[0] for shape in target_shapes]
        center = np.mean(centers, axis=0)
        x0 = max(0, int(center[0]) - 75)
        y0 = max(0, int(center[1]) - 75)
        x1 = min(image.shape[1], int(center[0]) + 75)
        y1 = min(image.shape[0], int(center[1]) + 75)
        crop = image[y0:y1, x0:x1].copy()
        for shape in unmatched_outer:
            points = (
                np.asarray(shape["points"], dtype=np.float32)
                - np.array([x0, y0], dtype=np.float32)
            )
            cv2.polylines(
                crop,
                [np.rint(points).astype(np.int32).reshape(-1, 1, 2)],
                True,
                (255, 0, 255),
                2,
                cv2.LINE_AA,
            )
        for shape in unmatched_inner:
            points = (
                np.asarray(shape["points"], dtype=np.float32)
                - np.array([x0, y0], dtype=np.float32)
            )
            cv2.polylines(
                crop,
                [np.rint(points).astype(np.int32).reshape(-1, 1, 2)],
                True,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
        tile = np.zeros((430, 400, 3), dtype=np.uint8)
        scale = min(400 / crop.shape[1], 400 / crop.shape[0])
        resized = cv2.resize(
            crop,
            (round(crop.shape[1] * scale), round(crop.shape[0] * scale)),
            interpolation=cv2.INTER_CUBIC,
        )
        tile[: resized.shape[0], : resized.shape[1]] = resized
        status = (
            f"{json_path.stem} unmatched wai={len(unmatched_outer)} "
            f"nei={len(unmatched_inner)}"
        )
        cv2.putText(
            tile,
            status,
            (3, 420),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )
        tiles.append(tile)
        cv2.imencode(".png", tile)[1].tofile(
            args.output / f"{json_path.stem}.png"
        )
        print(status)
    for page_index in range(math.ceil(len(tiles) / 9)):
        page = np.zeros((3 * 430, 3 * 400, 3), dtype=np.uint8)
        for index, tile in enumerate(tiles[page_index * 9 : (page_index + 1) * 9]):
            row, column = divmod(index, 3)
            page[row * 430 : (row + 1) * 430, column * 400 : (column + 1) * 400] = tile
        cv2.imencode(".jpg", page, [cv2.IMWRITE_JPEG_QUALITY, 96])[1].tofile(
            args.output / f"qa_{page_index + 1:03d}.jpg"
        )
    print(f"images={len(tiles)}")


if __name__ == "__main__":
    main()
