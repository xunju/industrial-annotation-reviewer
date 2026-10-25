import argparse
import csv
import json
import math
from pathlib import Path

import cv2
import numpy as np


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


def fit_ellipse(points):
    pts = np.asarray(points, np.float32).reshape(-1, 1, 2)
    if len(pts) < 5:
        raise ValueError("ellipse fitting requires at least five points")
    (cx, cy), (w, h), angle = cv2.fitEllipseAMS(pts)
    if w > h:
        w, h = h, w
        angle = (angle + 90.0) % 180.0
    return np.array([cx, cy, w / 2.0, h / 2.0, angle], np.float64)


def ellipse_points(params, count=384):
    cx, cy, a, b, angle = params
    theta = np.linspace(0.0, 2.0 * math.pi, count, endpoint=False)
    ca, sa = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    ct, st = np.cos(theta), np.sin(theta)
    x0, y0 = a * ct, b * st
    x = cx + ca * x0 - sa * y0
    y = cy + sa * x0 + ca * y0
    points = np.column_stack([x, y])

    # Unit outward normal of the implicit ellipse, rotated into image coordinates.
    nx0, ny0 = ct / max(a, 1e-6), st / max(b, 1e-6)
    nx = ca * nx0 - sa * ny0
    ny = sa * nx0 + ca * ny0
    normals = np.column_stack([nx, ny])
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-9)
    return points, normals


def sample_map(image, xy):
    map_x = xy[:, 0].astype(np.float32).reshape(-1, 1)
    map_y = xy[:, 1].astype(np.float32).reshape(-1, 1)
    return cv2.remap(
        image,
        map_x,
        map_y,
        cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    ).reshape(-1)


def refine_ellipse(image, initial, search_px=12, iterations=3):
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lab = cv2.GaussianBlur(lab, (0, 0), 1.1)
    channels = cv2.split(lab)
    grad_sq = np.zeros(image.shape[:2], np.float32)
    for channel in channels:
        gx = cv2.Sobel(channel, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(channel, cv2.CV_32F, 0, 1, ksize=3)
        grad_sq += gx * gx + gy * gy
    gradient = np.sqrt(grad_sq)

    params = initial.copy()
    chosen_offsets = np.zeros(384, np.float64)
    chosen_strength = np.zeros(384, np.float64)

    for iteration in range(iterations):
        base, normals = ellipse_points(params, 384)
        radius = max(5, search_px - iteration * 2)
        offsets = np.arange(-radius, radius + 0.01, 0.5, dtype=np.float64)
        strengths = np.empty((len(offsets), len(base)), np.float32)
        for i, offset in enumerate(offsets):
            strengths[i] = sample_map(gradient, base + normals * offset)

        # Prefer a strong nearby edge. The proximity term prevents shadows or
        # texture farther inside the tabletop from stealing the contour.
        local_scale = np.percentile(strengths, 80, axis=0) + 1e-3
        proximity = np.exp(-0.5 * (offsets[:, None] / 4.0) ** 2)
        score = strengths / local_scale[None, :] + 0.42 * proximity
        best = np.argmax(score, axis=0)
        chosen_offsets = offsets[best]
        chosen_strength = strengths[best, np.arange(len(base))]
        edge_points = base + normals * chosen_offsets[:, None]

        # Robustly reject weak selections and large discontinuous jumps.
        med = np.median(chosen_strength)
        mad = np.median(np.abs(chosen_strength - med)) + 1e-6
        good = chosen_strength >= max(np.percentile(chosen_strength, 28), med - 1.5 * mad)
        offset_med = np.median(chosen_offsets)
        offset_mad = np.median(np.abs(chosen_offsets - offset_med)) + 0.75
        good &= np.abs(chosen_offsets - offset_med) <= max(5.0, 3.5 * offset_mad)
        if np.count_nonzero(good) < 80:
            good[:] = True

        candidate = fit_ellipse(edge_points[good])
        # Damped updates are more stable on low-contrast and shadowed frames.
        params[:4] = params[:4] * 0.30 + candidate[:4] * 0.70
        da = ((candidate[4] - params[4] + 90.0) % 180.0) - 90.0
        params[4] = (params[4] + 0.70 * da) % 180.0

    return params, chosen_offsets, chosen_strength


def resample_closed(points, count=96):
    pts = np.asarray(points, np.float64)
    closed = np.vstack([pts, pts[0]])
    lengths = np.linalg.norm(np.diff(closed, axis=0), axis=1)
    cumulative = np.concatenate([[0.0], np.cumsum(lengths)])
    targets = np.linspace(0.0, cumulative[-1], count, endpoint=False)
    result = np.empty((count, 2), np.float64)
    for i, target in enumerate(targets):
        seg = min(np.searchsorted(cumulative, target, side="right") - 1, len(pts) - 1)
        fraction = (target - cumulative[seg]) / max(lengths[seg], 1e-9)
        result[i] = closed[seg] + fraction * (closed[seg + 1] - closed[seg])
    return result


def clip_polygon_to_image(points, width, height):
    polygon = [np.asarray(point, np.float64) for point in points]

    def clip(poly, inside, intersect):
        if not poly:
            return []
        output = []
        previous = poly[-1]
        previous_inside = inside(previous)
        for current in poly:
            current_inside = inside(current)
            if current_inside:
                if not previous_inside:
                    output.append(intersect(previous, current))
                output.append(current)
            elif previous_inside:
                output.append(intersect(previous, current))
            previous, previous_inside = current, current_inside
        return output

    xmax, ymax = float(width - 1), float(height - 1)

    def cross_x(value):
        def intersect(p, q):
            t = (value - p[0]) / max(abs(q[0] - p[0]), 1e-12)
            if q[0] < p[0]:
                t = (value - p[0]) / min(q[0] - p[0], -1e-12)
            return p + t * (q - p)
        return intersect

    def cross_y(value):
        def intersect(p, q):
            t = (value - p[1]) / max(abs(q[1] - p[1]), 1e-12)
            if q[1] < p[1]:
                t = (value - p[1]) / min(q[1] - p[1], -1e-12)
            return p + t * (q - p)
        return intersect

    polygon = clip(polygon, lambda p: p[0] >= 0.0, cross_x(0.0))
    polygon = clip(polygon, lambda p: p[0] <= xmax, cross_x(xmax))
    polygon = clip(polygon, lambda p: p[1] >= 0.0, cross_y(0.0))
    polygon = clip(polygon, lambda p: p[1] <= ymax, cross_y(ymax))
    if len(polygon) < 3:
        raise ValueError("ellipse does not intersect the image as a valid polygon")
    return np.asarray(polygon, np.float64)


def ellipse_distance_summary(old_points, new_points):
    old = np.asarray(old_points, np.float64)
    new = np.asarray(new_points, np.float64)
    distances = np.linalg.norm(old[:, None, :] - new[None, :, :], axis=2)
    nearest = distances.min(axis=1)
    return float(np.mean(nearest)), float(np.percentile(nearest, 95)), float(np.max(nearest))


def process_image(image_path, root, output_root, writer):
    json_path = image_path.with_suffix(".json")
    if not json_path.exists():
        writer.writerow([str(image_path.relative_to(root)), "missing_json"])
        return
    data = json.loads(json_path.read_text(encoding="utf-8"))
    circle_shapes = [
        shape for shape in data.get("shapes", [])
        if shape.get("label") == "circle" and len(shape.get("points", [])) >= 5
    ]
    if len(circle_shapes) != 1:
        writer.writerow([
            str(image_path.relative_to(root)), f"circle_shapes={len(circle_shapes)}"
        ])
        return

    # cv2.imread can fail on non-ASCII Windows paths; imdecode is Unicode-safe.
    image = cv2.imdecode(np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        writer.writerow([str(image_path.relative_to(root)), "unreadable_image"])
        return

    shape = circle_shapes[0]
    old_points = np.asarray(shape["points"], np.float64)
    initial = fit_ellipse(old_points)
    refined, offsets, strengths = refine_ellipse(image, initial)
    h, w = image.shape[:2]
    dense, _ = ellipse_points(refined, 1536)
    visible_contour = clip_polygon_to_image(dense, w, h)
    new_points = resample_closed(visible_contour, 96)

    shape["points"] = [[round(float(x), 3), round(float(y), 3)] for x, y in new_points]
    shape["shape_type"] = "polygon"
    relative_json = json_path.relative_to(root)
    target = output_root / relative_json
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    mean_d, p95_d, max_d = ellipse_distance_summary(old_points, new_points)
    segments = np.linalg.norm(np.diff(np.vstack([new_points, new_points[0]]), axis=0), axis=1)
    writer.writerow([
        str(image_path.relative_to(root)),
        "ok",
        len(old_points),
        96,
        round(mean_d, 3),
        round(p95_d, 3),
        round(max_d, 3),
        round(float(np.mean(offsets)), 3),
        round(float(np.std(offsets)), 3),
        round(float(np.mean(strengths)), 3),
        round(float(np.std(segments) / max(np.mean(segments), 1e-9)), 6),
        *[round(float(v), 3) for v in initial],
        *[round(float(v), 3) for v in refined],
    ])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    root = args.dataset_root.resolve()
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    images = sorted(path for path in root.rglob("*") if path.suffix.lower() in IMAGE_EXTS)
    with args.report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "image", "status", "old_points", "new_points",
            "old_new_mean_px", "old_new_p95_px", "old_new_max_px",
            "edge_offset_mean_px", "edge_offset_std_px", "edge_strength_mean",
            "segment_cv", "old_cx", "old_cy", "old_a", "old_b", "old_angle",
            "new_cx", "new_cy", "new_a", "new_b", "new_angle",
        ])
        for index, image_path in enumerate(images, 1):
            process_image(image_path, root, output, writer)
            if index % 25 == 0 or index == len(images):
                print(f"{index}/{len(images)}")


if __name__ == "__main__":
    main()
