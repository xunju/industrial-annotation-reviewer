from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


COLORS = {
    "circle": (0, 255, 0),
    "screw": (255, 255, 0),
    "top_left": (0, 0, 255),
    "top_right": (0, 128, 255),
    "bot_left": (255, 0, 255),
    "bot_right": (255, 0, 0),
    "wai": (255, 0, 255),
    "nei": (0, 255, 255),
}


def draw_one(image_path: Path, tile_width: int, tile_height: int) -> np.ndarray:
    image = cv2.imdecode(
        np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR
    )
    data = json.loads(
        image_path.with_suffix(".json").read_text(encoding="utf-8")
    )
    counts = {"screw": 0, "wai": 0, "nei": 0}
    for shape in data["shapes"]:
        label = shape["label"]
        points = np.asarray(shape["points"], dtype=np.float32)
        color = COLORS.get(label, (255, 255, 255))
        if label in counts:
            counts[label] += 1
        if shape["shape_type"] in {"polygon", "rectangle"} and len(points) >= 2:
            contour = np.rint(points).astype(np.int32).reshape(-1, 1, 2)
            thickness = 2 if label != "circle" else 1
            cv2.polylines(image, [contour], True, color, thickness, cv2.LINE_AA)
        elif shape["shape_type"] == "point" and len(points) == 1:
            x, y = np.rint(points[0]).astype(int)
            cv2.circle(image, (x, y), 3, color, -1, cv2.LINE_AA)

    height, width = image.shape[:2]
    scale = min(tile_width / width, tile_height / height)
    resized = cv2.resize(
        image,
        (round(width * scale), round(height * scale)),
        interpolation=cv2.INTER_AREA,
    )
    tile = np.zeros((tile_height + 30, tile_width, 3), dtype=np.uint8)
    tile[: resized.shape[0], : resized.shape[1]] = resized
    status = (
        f"{image_path.stem} screw={counts['screw']} "
        f"wai={counts['wai']} nei={counts['nei']}"
    )
    cv2.putText(
        tile,
        status,
        (4, tile_height + 20),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.43,
        (0, 255, 255),
        1,
        cv2.LINE_AA,
    )
    return tile


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("image_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--columns", type=int, default=4)
    parser.add_argument("--rows", type=int, default=5)
    parser.add_argument("--tile-width", type=int, default=480)
    parser.add_argument("--tile-height", type=int, default=360)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    images = sorted(args.image_dir.glob("*.jpg"))
    per_page = args.columns * args.rows
    for page_index in range(math.ceil(len(images) / per_page)):
        subset = images[page_index * per_page : (page_index + 1) * per_page]
        page = np.zeros(
            (
                (args.tile_height + 30) * args.rows,
                args.tile_width * args.columns,
                3,
            ),
            dtype=np.uint8,
        )
        for item_index, image_path in enumerate(subset):
            tile = draw_one(image_path, args.tile_width, args.tile_height)
            row, column = divmod(item_index, args.columns)
            y = row * (args.tile_height + 30)
            x = column * args.tile_width
            page[y : y + tile.shape[0], x : x + tile.shape[1]] = tile
        output_path = args.output_dir / f"qa_{page_index + 1:03d}.jpg"
        cv2.imencode(
            ".jpg", page, [cv2.IMWRITE_JPEG_QUALITY, 94]
        )[1].tofile(output_path)
    print(f"images={len(images)} pages={math.ceil(len(images)/per_page)}")


if __name__ == "__main__":
    main()
