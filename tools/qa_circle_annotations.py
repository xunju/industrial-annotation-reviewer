from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


def imread(path: Path) -> np.ndarray:
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


def load_points(json_path: Path):
    data = json.loads(json_path.read_text(encoding="utf-8"))
    shapes = data.get("shapes", [])
    points = (
        np.asarray(shapes[0].get("points", []), dtype=np.float32)
        if shapes
        else np.empty((0, 2), dtype=np.float32)
    )
    return shapes, points


def draw_tile(
    image_path: Path,
    dataset_root: Path,
    width: int,
    height: int,
    json_root: Path | None,
) -> np.ndarray:
    image = imread(image_path)
    shapes, old_points = load_points(image_path.with_suffix(".json"))
    if json_root is None:
        points = old_points
    else:
        relative = image_path.relative_to(dataset_root).with_suffix(".json")
        _, points = load_points(json_root / relative)
        if len(old_points) >= 2:
            old_contour = np.rint(old_points).astype(np.int32).reshape(-1, 1, 2)
            cv2.polylines(image, [old_contour], True, (0, 0, 255), 2, cv2.LINE_AA)
    if len(points) >= 2:
        contour = np.rint(points).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(image, [contour], True, (0, 255, 0), 2, cv2.LINE_AA)
        for index in range(0, len(points), 8):
            x, y = np.rint(points[index]).astype(int)
            cv2.circle(image, (x, y), 2, (0, 0, 255), -1, cv2.LINE_AA)

    scale = min(width / image.shape[1], height / image.shape[0])
    resized = cv2.resize(
        image,
        (round(image.shape[1] * scale), round(image.shape[0] * scale)),
        interpolation=cv2.INTER_AREA,
    )
    tile = np.zeros((height + 30, width, 3), dtype=np.uint8)
    tile[: resized.shape[0], : resized.shape[1]] = resized
    status = (
        f"{image_path.parent.name}/{image_path.stem} "
        f"shapes={len(shapes)} points={len(points)}"
    )
    cv2.putText(
        tile,
        status,
        (4, height + 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.42,
        (0, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return tile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--rows", type=int, default=5)
    parser.add_argument("--tile-width", type=int, default=400)
    parser.add_argument("--tile-height", type=int, default=300)
    parser.add_argument(
        "--json-root",
        type=Path,
        help="Optional mirror tree of proposed JSON files. Old=red, proposed=green.",
    )
    parser.add_argument(
        "--stems",
        help="Optional comma-separated image stems to include.",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    images = sorted(args.dataset.rglob("*.jpg"))
    if args.stems:
        selected = {item.strip() for item in args.stems.split(",") if item.strip()}
        images = [path for path in images if path.stem in selected]
    per_page = args.columns * args.rows
    for page_index in range(math.ceil(len(images) / per_page)):
        page = np.zeros(
            (
                (args.tile_height + 30) * args.rows,
                args.tile_width * args.columns,
                3,
            ),
            dtype=np.uint8,
        )
        subset = images[page_index * per_page : (page_index + 1) * per_page]
        for local_index, image_path in enumerate(subset):
            tile = draw_tile(
                image_path,
                args.dataset,
                args.tile_width,
                args.tile_height,
                args.json_root,
            )
            row, column = divmod(local_index, args.columns)
            y0 = row * (args.tile_height + 30)
            x0 = column * args.tile_width
            page[y0 : y0 + tile.shape[0], x0 : x0 + tile.shape[1]] = tile
        cv2.imencode(".jpg", page, [cv2.IMWRITE_JPEG_QUALITY, 96])[1].tofile(
            args.output / f"qa_{page_index + 1:03d}.jpg"
        )
    print(f"images={len(images)} pages={math.ceil(len(images) / per_page)}")


if __name__ == "__main__":
    main()
