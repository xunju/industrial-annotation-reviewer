from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path

import cv2
import numpy as np


SOURCE = Path(
    r"E:\robocup\工业测量\数据集\个人标注"
    r"\round_table_part_05_xanylabeling_96pt\images"
)
OUTPUT = Path(
    r"E:\robocup\工业测量\数据集\个人标注"
    r"\round_table_part_05_roi_640"
)
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
    if OUTPUT.exists():
        raise RuntimeError(f"Output already exists: {OUTPUT}")
    output_images = OUTPUT / "images"
    output_images.mkdir(parents=True)

    rows: list[dict[str, str | float]] = []
    images = sorted(SOURCE.glob("*.jpg"))
    if len(images) != 720:
        raise RuntimeError(f"Expected 720 source images, found {len(images)}")

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

    with (OUTPUT / "transforms.csv").open(
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
    (OUTPUT / "classes.txt").write_text(
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
