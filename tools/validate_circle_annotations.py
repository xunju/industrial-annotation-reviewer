import argparse
import json
import math
from collections import Counter
from pathlib import Path

import cv2
import numpy as np


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


def image_size(path):
    image = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("unreadable image")
    return image.shape[1], image.shape[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument(
        "--json-root",
        type=Path,
        help="Optional mirror JSON root; defaults to JSON beside each image.",
    )
    args = parser.parse_args()
    root = args.dataset_root.resolve()
    json_root = args.json_root.resolve() if args.json_root else None
    images = sorted(path for path in root.rglob("*") if path.suffix.lower() in IMAGE_EXTS)
    errors = []
    point_counts = Counter()
    segment_cvs = []
    border_shapes = []

    for image_path in images:
        relative = image_path.relative_to(root)
        json_path = (
            json_root / relative.with_suffix(".json")
            if json_root
            else image_path.with_suffix(".json")
        )
        label = str(relative)
        if not json_path.exists():
            errors.append(f"{label}: missing JSON")
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            width, height = image_size(image_path)
        except Exception as exc:
            errors.append(f"{label}: {exc}")
            continue

        shapes = data.get("shapes", [])
        circles = [shape for shape in shapes if shape.get("label") == "circle"]
        if len(circles) != 1:
            errors.append(f"{label}: circle shape count={len(circles)}")
            continue
        shape = circles[0]
        if shape.get("shape_type") != "polygon":
            errors.append(f"{label}: shape_type={shape.get('shape_type')!r}")
        points = np.asarray(shape.get("points", []), np.float64)
        point_counts[len(points)] += 1
        if points.shape != (96, 2):
            errors.append(f"{label}: points shape={points.shape}")
            continue
        if not np.all(np.isfinite(points)):
            errors.append(f"{label}: non-finite point")
        if (
            np.any(points[:, 0] < -1e-6)
            or np.any(points[:, 0] > width - 1 + 1e-6)
            or np.any(points[:, 1] < -1e-6)
            or np.any(points[:, 1] > height - 1 + 1e-6)
        ):
            errors.append(f"{label}: out-of-bounds point")
        closed = np.vstack([points, points[0]])
        segments = np.linalg.norm(np.diff(closed, axis=0), axis=1)
        if np.min(segments) < 0.05:
            errors.append(f"{label}: duplicate/near-duplicate adjacent point")
        cv = float(np.std(segments) / max(np.mean(segments), 1e-12))
        segment_cvs.append(cv)
        if cv > 0.02:
            errors.append(f"{label}: nonuniform spacing cv={cv:.5f}")
        area = abs(float(cv2.contourArea(points.astype(np.float32))))
        if area < 1000:
            errors.append(f"{label}: implausible polygon area={area:.1f}")
        if np.any(
            (points[:, 0] < 1e-3)
            | (points[:, 0] > width - 1 - 1e-3)
            | (points[:, 1] < 1e-3)
            | (points[:, 1] > height - 1 - 1e-3)
        ):
            border_shapes.append(label)
        if data.get("imageWidth") not in (None, width):
            errors.append(f"{label}: imageWidth mismatch")
        if data.get("imageHeight") not in (None, height):
            errors.append(f"{label}: imageHeight mismatch")

    expected_jsons = {
        (
            json_root / path.relative_to(root).with_suffix(".json")
            if json_root
            else path.with_suffix(".json")
        ).resolve()
        for path in images
    }
    actual_jsons = set((json_root or root).rglob("*.json"))
    extras = actual_jsons - expected_jsons
    if extras:
        errors.append(f"extra JSON files={len(extras)}")

    print(f"images={len(images)}")
    print(f"json_expected={len(expected_jsons)} json_actual={len(actual_jsons)}")
    print(f"point_counts={dict(sorted(point_counts.items()))}")
    print(f"border_touching_shapes={len(border_shapes)}")
    if segment_cvs:
        print(
            "segment_cv "
            f"mean={np.mean(segment_cvs):.6f} "
            f"p95={np.percentile(segment_cvs, 95):.6f} "
            f"max={np.max(segment_cvs):.6f}"
        )
    print(f"errors={len(errors)}")
    for error in errors[:100]:
        print(f"ERROR {error}")
    if len(errors) > 100:
        print(f"... {len(errors) - 100} more errors")
    raise SystemExit(1 if errors else 0)


if __name__ == "__main__":
    main()
