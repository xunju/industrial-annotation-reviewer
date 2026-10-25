from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np

from repair_missing_shim_inner import matched_indices


def fit_ellipse(shape: dict):
    points = np.asarray(shape["points"], dtype=np.float32)
    if len(points) >= 5:
        return cv2.fitEllipse(points.reshape(-1, 1, 2))
    rect = cv2.minAreaRect(points.reshape(-1, 1, 2))
    return rect


def polygon_from_ellipse(ellipse, count: int = 48) -> np.ndarray:
    (cx, cy), (width, height), angle_deg = ellipse
    angle = np.deg2rad(angle_deg)
    values = np.linspace(0, 2 * np.pi, count, endpoint=False)
    x = 0.5 * width * np.cos(values)
    y = 0.5 * height * np.sin(values)
    cos_a, sin_a = np.cos(angle), np.sin(angle)
    return np.column_stack(
        [cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a]
    ).astype(np.float32)


def paired_shapes(outer: list[dict], inner: list[dict]):
    used_outer, used_inner = matched_indices(outer, inner)
    pairs = []
    for outer_index in used_outer:
        outer_ellipse = fit_ellipse(outer[outer_index])
        outer_center = np.asarray(outer_ellipse[0], dtype=np.float32)
        outer_scale = max(np.mean(outer_ellipse[1]), 2.0)
        choices = []
        for inner_index in used_inner:
            inner_ellipse = fit_ellipse(inner[inner_index])
            distance = float(
                np.linalg.norm(np.asarray(inner_ellipse[0]) - outer_center)
                / outer_scale
            )
            choices.append((distance, inner_index))
        if choices:
            _, inner_index = min(choices)
            pairs.append((outer[outer_index], inner[inner_index]))
    return pairs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    ratios: list[float] = []
    for json_path in sorted((args.dataset / "images").glob("*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        outer = [s for s in data["shapes"] if s["label"] == "wai"]
        inner = [s for s in data["shapes"] if s["label"] == "nei"]
        for outer_shape, inner_shape in paired_shapes(outer, inner):
            outer_ellipse = fit_ellipse(outer_shape)
            inner_ellipse = fit_ellipse(inner_shape)
            ratio = float(
                np.sqrt(
                    (inner_ellipse[1][0] * inner_ellipse[1][1])
                    / max(outer_ellipse[1][0] * outer_ellipse[1][1], 1e-6)
                )
            )
            if 0.25 <= ratio <= 0.85:
                ratios.append(ratio)
    scale = float(np.median(ratios))
    print(
        f"paired_ratio_samples={len(ratios)} "
        f"q25={np.quantile(ratios, .25):.3f} median={scale:.3f} "
        f"q75={np.quantile(ratios, .75):.3f}"
    )

    tiles: list[np.ndarray] = []
    added = 0
    for json_path in sorted((args.dataset / "images").glob("*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        outer = [s for s in data["shapes"] if s["label"] == "wai"]
        inner = [s for s in data["shapes"] if s["label"] == "nei"]
        used_outer, _ = matched_indices(outer, inner)
        unmatched = [
            shape for index, shape in enumerate(outer) if index not in used_outer
        ]
        if not unmatched:
            continue
        image = cv2.imdecode(
            np.fromfile(json_path.with_suffix(".jpg"), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        additions = []
        for local_index, shape in enumerate(unmatched, 1):
            ellipse = fit_ellipse(shape)
            inner_ellipse = (
                ellipse[0],
                (ellipse[1][0] * scale, ellipse[1][1] * scale),
                ellipse[2],
            )
            polygon = polygon_from_ellipse(inner_ellipse)
            addition = {
                "label": "nei",
                "score": None,
                "points": [
                    [round(float(x), 3), round(float(y), 3)] for x, y in polygon
                ],
                "group_id": None,
                "description": (
                    "Manually reviewed inner-hole completion using the dataset "
                    "median inner/outer ellipse ratio."
                ),
                "difficult": False,
                "shape_type": "polygon",
                "flags": {},
                "attributes": {},
                "kie_linking": [],
            }
            additions.append(addition)
            points = np.asarray(shape["points"], dtype=np.float32)
            margin = max(24, int(max(ellipse[1]) * 1.5))
            x0 = max(0, int(points[:, 0].min()) - margin)
            y0 = max(0, int(points[:, 1].min()) - margin)
            x1 = min(image.shape[1], int(points[:, 0].max()) + margin)
            y1 = min(image.shape[0], int(points[:, 1].max()) + margin)
            crop = image[y0:y1, x0:x1].copy()
            local_outer = points - np.array([x0, y0], dtype=np.float32)
            local_inner = polygon - np.array([x0, y0], dtype=np.float32)
            cv2.polylines(
                crop,
                [np.rint(local_outer).astype(np.int32).reshape(-1, 1, 2)],
                True,
                (255, 0, 255),
                2,
                cv2.LINE_AA,
            )
            cv2.polylines(
                crop,
                [np.rint(local_inner).astype(np.int32).reshape(-1, 1, 2)],
                True,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
            tile = np.zeros((330, 300, 3), dtype=np.uint8)
            resize_scale = min(300 / crop.shape[1], 300 / crop.shape[0])
            resized = cv2.resize(
                crop,
                (
                    round(crop.shape[1] * resize_scale),
                    round(crop.shape[0] * resize_scale),
                ),
                interpolation=cv2.INTER_CUBIC,
            )
            ox = (300 - resized.shape[1]) // 2
            oy = (300 - resized.shape[0]) // 2
            tile[oy : oy + resized.shape[0], ox : ox + resized.shape[1]] = resized
            cv2.putText(
                tile,
                f"{json_path.stem}__{local_index}",
                (3, 320),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.42,
                (0, 255, 255),
                1,
                cv2.LINE_AA,
            )
            tiles.append(tile)
            added += 1
        if additions and args.apply:
            data["shapes"].extend(additions)
            json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

    for page_index in range(math.ceil(len(tiles) / 20)):
        page = np.zeros((5 * 330, 4 * 300, 3), dtype=np.uint8)
        for index, tile in enumerate(
            tiles[page_index * 20 : (page_index + 1) * 20]
        ):
            row, column = divmod(index, 4)
            page[row * 330 : (row + 1) * 330, column * 300 : (column + 1) * 300] = tile
        cv2.imencode(".jpg", page, [cv2.IMWRITE_JPEG_QUALITY, 96])[1].tofile(
            args.output / f"qa_{page_index + 1:03d}.jpg"
        )
    print(f"proposed={added} apply={args.apply}")


if __name__ == "__main__":
    main()
