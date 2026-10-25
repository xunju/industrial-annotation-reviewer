from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np

from repair_missing_shim_inner import center_and_radius, matched_indices


def ellipse_polygon(ellipse, count: int = 48) -> np.ndarray:
    (cx, cy), (width, height), angle_deg = ellipse
    angle = np.deg2rad(angle_deg)
    t = np.linspace(0, 2 * np.pi, count, endpoint=False)
    x = 0.5 * width * np.cos(t)
    y = 0.5 * height * np.sin(t)
    cos_a, sin_a = np.cos(angle), np.sin(angle)
    return np.column_stack(
        [cx + x * cos_a - y * sin_a, cy + x * sin_a + y * cos_a]
    ).astype(np.float32)


def find_inner_candidate(image: np.ndarray, outer: dict):
    outer_points = np.asarray(outer["points"], dtype=np.float32)
    outer_center, outer_radius = center_and_radius(outer)
    outer_area = abs(float(cv2.contourArea(outer_points.reshape(-1, 1, 2))))
    x, y, width, height = cv2.boundingRect(
        outer_points.astype(np.int32).reshape(-1, 1, 2)
    )
    margin = 5
    x0, y0 = max(0, x - margin), max(0, y - margin)
    x1 = min(image.shape[1], x + width + margin)
    y1 = min(image.shape[0], y + height + margin)
    crop = image[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    median = float(np.median(gray))
    lower = int(max(10, 0.45 * median))
    upper = int(min(240, max(lower + 20, 1.25 * median)))
    edges = cv2.Canny(gray, lower, upper)
    contours, _ = cv2.findContours(
        edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE
    )

    candidates: list[tuple[float, tuple, np.ndarray]] = []
    for contour in contours:
        if len(contour) < 8:
            continue
        contour_global = contour.astype(np.float32)
        contour_global[:, 0, 0] += x0
        contour_global[:, 0, 1] += y0
        area = abs(float(cv2.contourArea(contour_global)))
        area_ratio = area / max(outer_area, 1.0)
        if not 0.035 <= area_ratio <= 0.62:
            continue
        ellipse = cv2.fitEllipse(contour_global)
        (cx, cy), (ew, eh), _ = ellipse
        if min(ew, eh) < 2.0:
            continue
        axis_ratio = min(ew, eh) / max(ew, eh)
        if axis_ratio < 0.35:
            continue
        center_distance = float(
            np.linalg.norm(np.array([cx, cy]) - outer_center)
            / max(outer_radius, 2.0)
        )
        if center_distance > 0.55:
            continue
        polygon = ellipse_polygon(ellipse)
        if any(
            cv2.pointPolygonTest(
                outer_points.reshape(-1, 1, 2),
                (float(px), float(py)),
                False,
            )
            < 0
            for px, py in polygon[::4]
        ):
            continue
        target_ratio = 0.22
        score = (
            3.0 * center_distance
            + abs(math.log(max(area_ratio, 1e-6) / target_ratio))
            + 0.4 * (1.0 - axis_ratio)
        )
        candidates.append((score, ellipse, polygon))
    return min(candidates, key=lambda item: item[0]) if candidates else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    tiles: list[np.ndarray] = []
    proposed = 0
    unresolved = 0
    affected_images = 0

    for json_path in sorted((args.dataset / "images").glob("*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        outer = [shape for shape in data["shapes"] if shape["label"] == "wai"]
        inner = [shape for shape in data["shapes"] if shape["label"] == "nei"]
        used_outer, _ = matched_indices(outer, inner)
        unmatched = [
            shape for index, shape in enumerate(outer) if index not in used_outer
        ]
        if not unmatched:
            continue
        image_path = json_path.with_suffix(".jpg")
        image = cv2.imdecode(
            np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        overlay = image.copy()
        additions: list[dict] = []
        for target in unmatched:
            target_points = np.asarray(target["points"], dtype=np.int32)
            cv2.polylines(
                overlay,
                [target_points.reshape(-1, 1, 2)],
                True,
                (255, 0, 255),
                3,
                cv2.LINE_AA,
            )
            candidate = find_inner_candidate(image, target)
            if candidate is None:
                unresolved += 1
                continue
            _, _, polygon = candidate
            cv2.polylines(
                overlay,
                [np.rint(polygon).astype(np.int32).reshape(-1, 1, 2)],
                True,
                (0, 255, 255),
                3,
                cv2.LINE_AA,
            )
            additions.append(
                {
                    "label": "nei",
                    "score": None,
                    "points": [
                        [round(float(px), 3), round(float(py), 3)]
                        for px, py in polygon
                    ],
                    "group_id": None,
                    "description": (
                        "Inner shim contour fitted from local image edges after "
                        "visual-review candidate generation."
                    ),
                    "difficult": False,
                    "shape_type": "polygon",
                    "flags": {},
                    "attributes": {},
                    "kie_linking": [],
                }
            )
            proposed += 1
            print(
                f"{json_path.stem}: candidate "
                f"center=({polygon[:, 0].mean():.1f},{polygon[:, 1].mean():.1f}) "
                f"score={candidate[0]:.3f}"
            )
        if additions:
            affected_images += 1
            all_unmatched_points = np.concatenate(
                [
                    np.asarray(shape["points"], dtype=np.float32)
                    for shape in unmatched
                ],
                axis=0,
            )
            x0 = max(0, int(all_unmatched_points[:, 0].min()) - 45)
            y0 = max(0, int(all_unmatched_points[:, 1].min()) - 45)
            x1 = min(image.shape[1], int(all_unmatched_points[:, 0].max()) + 45)
            y1 = min(image.shape[0], int(all_unmatched_points[:, 1].max()) + 45)
            crop = overlay[y0:y1, x0:x1]
            scale = min(6.0, 1000.0 / max(crop.shape[:2]))
            enlarged = cv2.resize(
                crop,
                (round(crop.shape[1] * scale), round(crop.shape[0] * scale)),
                interpolation=cv2.INTER_CUBIC,
            )
            individual_dir = args.output / "individual"
            individual_dir.mkdir(parents=True, exist_ok=True)
            cv2.imencode(".png", enlarged)[1].tofile(
                individual_dir / f"{json_path.stem}.png"
            )
            if args.apply:
                data["shapes"].extend(additions)
                json_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        resized = cv2.resize(overlay, (320, 320), interpolation=cv2.INTER_AREA)
        tile = np.zeros((350, 320, 3), dtype=np.uint8)
        tile[:320] = resized
        cv2.putText(
            tile,
            f"{json_path.stem} candidates={len(additions)}/{len(unmatched)}",
            (4, 340),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )
        tiles.append(tile)

    per_page = 20
    for page_index in range(math.ceil(len(tiles) / per_page)):
        page = np.zeros((5 * 350, 4 * 320, 3), dtype=np.uint8)
        for local_index, tile in enumerate(
            tiles[page_index * per_page : (page_index + 1) * per_page]
        ):
            row, column = divmod(local_index, 4)
            page[row * 350 : (row + 1) * 350, column * 320 : (column + 1) * 320] = tile
        cv2.imencode(
            ".jpg", page, [cv2.IMWRITE_JPEG_QUALITY, 95]
        )[1].tofile(args.output / f"qa_{page_index + 1:03d}.jpg")

    print(
        f"unmatched_images={len(tiles)} proposed={proposed} "
        f"unresolved={unresolved} affected_images={affected_images} "
        f"apply={args.apply}"
    )


if __name__ == "__main__":
    main()
