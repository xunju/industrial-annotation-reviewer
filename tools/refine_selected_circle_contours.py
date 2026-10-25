import argparse
import json
import math
import shutil
from pathlib import Path

import cv2
import numpy as np

from refine_circle_annotations import (
    ellipse_points,
    fit_ellipse,
    resample_closed,
    sample_map,
)


def circular_median(values, radius):
    padded = np.pad(values, (radius, radius), mode="wrap")
    return np.array([
        np.median(padded[i:i + 2 * radius + 1])
        for i in range(len(values))
    ])


def circular_gaussian(values, sigma):
    radius = max(1, int(math.ceil(3.0 * sigma)))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (x / sigma) ** 2)
    kernel /= kernel.sum()
    padded = np.pad(values, (radius, radius), mode="wrap")
    return np.convolve(padded, kernel, mode="valid")


def circular_lowpass(values, harmonics):
    spectrum = np.fft.rfft(values)
    spectrum[harmonics + 1:] = 0
    return np.fft.irfft(spectrum, n=len(values))


def edge_aligned_contour(image, old_points, count=768):
    params = fit_ellipse(old_points)
    base, normals = ellipse_points(params, count)

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lab = cv2.GaussianBlur(lab, (0, 0), 0.85)
    grad_sq = np.zeros(image.shape[:2], np.float32)
    for channel in cv2.split(lab):
        gx = cv2.Sobel(channel, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(channel, cv2.CV_32F, 0, 1, ksize=3)
        grad_sq += gx * gx + gy * gy
    gradient = np.sqrt(grad_sq)

    offsets = np.arange(-5.0, 5.001, 0.25, dtype=np.float64)
    strengths = np.empty((len(offsets), count), np.float32)
    for index, offset in enumerate(offsets):
        strengths[index] = sample_map(gradient, base + normals * offset)

    # Normalize independently around each normal and favor the closest strong edge.
    p20 = np.percentile(strengths, 20, axis=0)
    p90 = np.percentile(strengths, 90, axis=0)
    normalized = (strengths - p20) / np.maximum(p90 - p20, 1e-3)
    score = normalized - 0.15 * np.abs(offsets[:, None])
    best = np.argmax(score, axis=0)
    raw_offsets = offsets[best]

    # Remove isolated selections caused by floor texture or highlights, while
    # retaining genuine low-frequency lens and tabletop deviations.
    smooth_offsets = circular_median(raw_offsets, 8)
    smooth_offsets = circular_gaussian(smooth_offsets, 5.0)
    smooth_offsets = circular_lowpass(smooth_offsets, 6)
    smooth_offsets = np.clip(smooth_offsets, -3.0, 3.0)
    contour = base + normals * smooth_offsets[:, None]
    return resample_closed(contour, 96), raw_offsets, smooth_offsets


def edge_align_path(image, source_contour, count=768):
    base = resample_closed(source_contour, count)
    base[:, 0] = circular_gaussian(base[:, 0], 4.0)
    base[:, 1] = circular_gaussian(base[:, 1], 4.0)
    tangent = np.roll(base, -1, axis=0) - np.roll(base, 1, axis=0)
    normals = np.column_stack([tangent[:, 1], -tangent[:, 0]])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)

    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lab = cv2.GaussianBlur(lab, (0, 0), 0.85)
    grad_sq = np.zeros(image.shape[:2], np.float32)
    for channel in cv2.split(lab):
        gx = cv2.Sobel(channel, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(channel, cv2.CV_32F, 0, 1, ksize=3)
        grad_sq += gx * gx + gy * gy
    gradient = np.sqrt(grad_sq)

    offsets = np.arange(-4.0, 4.001, 0.25, dtype=np.float64)
    strengths = np.empty((len(offsets), count), np.float32)
    for index, offset in enumerate(offsets):
        strengths[index] = sample_map(gradient, base + normals * offset)
    p20 = np.percentile(strengths, 20, axis=0)
    p90 = np.percentile(strengths, 90, axis=0)
    normalized = (strengths - p20) / np.maximum(p90 - p20, 1e-3)
    score = normalized - 0.18 * np.abs(offsets[:, None])
    raw_offsets = offsets[np.argmax(score, axis=0)]
    smooth_offsets = circular_median(raw_offsets, 6)
    smooth_offsets = circular_gaussian(smooth_offsets, 4.0)
    smooth_offsets = circular_lowpass(smooth_offsets, 14)
    smooth_offsets = np.clip(smooth_offsets, -2.5, 2.5)
    contour = base + normals * smooth_offsets[:, None]
    return resample_closed(contour, 96), raw_offsets, smooth_offsets


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("stems", help="Comma-separated image stems")
    parser.add_argument(
        "--path-mode",
        action="store_true",
        help="Refine the supplied contour directly instead of fitting an ellipse first.",
    )
    args = parser.parse_args()

    root = args.dataset_root.resolve()
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stems = [item.strip() for item in args.stems.split(",") if item.strip()]

    found = {}
    for image_path in root.rglob("*.jpg"):
        if image_path.stem in stems:
            found[image_path.stem] = image_path
    missing = sorted(set(stems) - set(found))
    if missing:
        raise SystemExit(f"missing images: {missing}")

    for stem in stems:
        image_path = found[stem]
        json_path = image_path.with_suffix(".json")
        image = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
        data = json.loads(json_path.read_text(encoding="utf-8"))
        circle = next(shape for shape in data["shapes"] if shape.get("label") == "circle")
        old_points = np.asarray(circle["points"], np.float64)
        if args.path_mode:
            points, raw_offsets, smooth_offsets = edge_align_path(image, old_points)
        else:
            points, raw_offsets, smooth_offsets = edge_aligned_contour(image, old_points)
        circle["points"] = [
            [round(float(x), 3), round(float(y), 3)]
            for x, y in points
        ]
        circle["shape_type"] = "polygon"

        shutil.copy2(image_path, output / image_path.name)
        (output / json_path.name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(
            f"{stem}: raw_offset=[{raw_offsets.min():.2f},{raw_offsets.max():.2f}] "
            f"smooth_mean={smooth_offsets.mean():.3f} "
            f"smooth_range=[{smooth_offsets.min():.3f},{smooth_offsets.max():.3f}]"
        )


if __name__ == "__main__":
    main()
