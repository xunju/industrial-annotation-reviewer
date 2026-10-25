from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

import cv2
import numpy as np


LETTERBOX_SHAPE = (736, 1280)
ROI_SIZE = 640


def letterbox(image: np.ndarray):
    src_h, src_w = image.shape[:2]
    dst_h, dst_w = LETTERBOX_SHAPE
    scale = min(dst_h / src_h, dst_w / src_w)
    new_w, new_h = int(round(src_w * scale)), int(round(src_h * scale))
    dw, dh = dst_w - new_w, dst_h - new_h
    left = int(round(dw / 2 - 0.1))
    right = int(round(dw / 2 + 0.1))
    top = int(round(dh / 2 - 0.1))
    bottom = int(round(dh / 2 + 0.1))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    padded = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114,) * 3
    )
    return padded, scale, left, top


def order_box_points(points: np.ndarray) -> np.ndarray:
    y_sorted = points[points[:, 1].argsort()]
    top_two, bottom_two = y_sorted[:2], y_sorted[2:]
    top_left, top_right = top_two[top_two[:, 0].argsort()]
    bottom_left, bottom_right = bottom_two[bottom_two[:, 0].argsort()]
    return np.asarray(
        [top_left, top_right, bottom_right, bottom_left], dtype=np.float32
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate 640x640 round-table crops from circle polygon annotations.")
    parser.add_argument("source", type=Path, help="Directory containing JPG images and matching circle JSON files")
    parser.add_argument("output", type=Path, help="New output dataset directory")
    parser.add_argument("--expected-count", type=int, help="Optional expected number of source images")
    args = parser.parse_args()
    source = args.source.resolve()
    output = args.output.resolve()
    if not source.is_dir():
        parser.error(f"Source directory does not exist: {source}")
    images = sorted(path for path in source.iterdir() if path.suffix.lower() == ".jpg")
    if not images:
        parser.error(f"No JPG images found in {source}")
    if args.expected_count is not None and len(images) != args.expected_count:
        parser.error(f"Expected {args.expected_count} source images, found {len(images)}")
    if output.exists():
        raise RuntimeError(f"Output already exists: {output}")
    output_images = output / "images"
    output_images.mkdir(parents=True)

    rows: list[dict[str, str | float]] = []

    destination = np.float32(
        [[0, 0], [ROI_SIZE, 0], [ROI_SIZE, ROI_SIZE], [0, ROI_SIZE]]
    )
    for index, image_path in enumerate(images, 1):
        image = cv2.imdecode(
            np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        data = json.loads(
            image_path.with_suffix(".json").read_text(encoding="utf-8")
        )
        circle = next(
            shape for shape in data["shapes"] if shape["label"] == "circle"
        )
        source_points = np.asarray(circle["points"], dtype=np.float32)

        padded, scale, pad_left, pad_top = letterbox(image)
        padded_points = source_points * scale
        padded_points[:, 0] += pad_left
        padded_points[:, 1] += pad_top

        ellipse = cv2.fitEllipse(padded_points.reshape(-1, 1, 2))
        ellipse_mask = np.zeros(padded.shape[:2], dtype=np.uint8)
        cv2.ellipse(ellipse_mask, ellipse, 255, -1)
        masked = cv2.bitwise_and(padded, padded, mask=ellipse_mask)
        source_box = order_box_points(cv2.boxPoints(ellipse).astype(np.float32))
        transform = cv2.getPerspectiveTransform(source_box, destination)
        roi = cv2.warpPerspective(masked, transform, (ROI_SIZE, ROI_SIZE))

        output_image = output_images / image_path.name
        cv2.imencode(
            ".jpg", roi, [cv2.IMWRITE_JPEG_QUALITY, 96]
        )[1].tofile(output_image)
        roi_json = {
            "version": data.get("version", "4.0.0-beta.13"),
            "flags": {},
            "checked": False,
            "shapes": [],
            "imagePath": image_path.name,
            "imageData": None,
            "imageHeight": ROI_SIZE,
            "imageWidth": ROI_SIZE,
            "description": (
                "640x640 round-table ROI generated with the repository-compatible "
                "ellipse-box perspective mapping; awaiting screw/shim annotation."
            ),
        }
        output_image.with_suffix(".json").write_text(
            json.dumps(roi_json, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        warped_boundary = cv2.perspectiveTransform(
            padded_points[None], transform
        )[0]
        radii = np.linalg.norm(
            warped_boundary - np.array([320.0, 320.0], dtype=np.float32), axis=1
        )
        radial_p95 = float(np.percentile(np.abs(radii / 320.0 - 1.0), 95))
        rows.append(
            {
                "image": image_path.name,
                "source_scale": round(float(scale), 8),
                "pad_left": pad_left,
                "pad_top": pad_top,
                "radial_error_p95": round(radial_p95, 6),
                "homography": " ".join(
                    f"{value:.10g}" for value in transform.reshape(-1)
                ),
            }
        )
        if index % 50 == 0 or index == len(images):
            print(f"[{index}/{len(images)}] {image_path.name}", flush=True)

    with (output / "transforms.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "image",
                "source_scale",
                "pad_left",
                "pad_top",
                "radial_error_p95",
                "homography",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    (output / "classes.txt").write_text(
        "\n".join(
            [
                "screw",
                "top_left",
                "top_right",
                "bot_left",
                "bot_right",
                "wai",
                "nei",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"generated={len(images)} max_radial_error_p95="
        f"{max(float(row['radial_error_p95']) for row in rows):.6f}"
    )


if __name__ == "__main__":
    main()
