from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from annotate_screw_shim import infer_shims
from repair_missing_shim_inner import center_and_radius, matched_indices


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    session = ort.InferenceSession(str(args.model), providers=["CPUExecutionProvider"])
    table = np.array(
        [[0.0, 0.0], [639.0, 0.0], [639.0, 639.0], [0.0, 639.0]],
        dtype=np.float32,
    )

    tiles = []
    proposed = 0
    unresolved = 0
    replacements = 0
    additions_count = 0
    for json_path in sorted((args.dataset / "images").glob("*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        shapes = data["shapes"]
        outer = [s for s in shapes if s["label"] == "wai"]
        inner = [s for s in shapes if s["label"] == "nei"]
        used_outer, used_inner = matched_indices(outer, inner)
        unmatched_outer = [
            (index, shape)
            for index, shape in enumerate(outer)
            if index not in used_outer
        ]
        unmatched_inner = [
            shape for index, shape in enumerate(inner) if index not in used_inner
        ]
        if not unmatched_inner:
            continue
        image = cv2.imdecode(
            np.fromfile(json_path.with_suffix(".jpg"), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        additions = []
        replacements_for_image: list[tuple[dict, dict]] = []
        for local_index, target in enumerate(unmatched_inner, 1):
            target_center, target_radius = center_and_radius(target)
            best = None
            for crop_size in (160, 224, 288):
                half = crop_size / 2
                x0 = max(0, min(image.shape[1] - crop_size, int(target_center[0] - half)))
                y0 = max(0, min(image.shape[0] - crop_size, int(target_center[1] - half)))
                x1 = min(image.shape[1], x0 + crop_size)
                y1 = min(image.shape[0], y0 + crop_size)
                crop = image[y0:y1, x0:x1]
                enlarged = cv2.resize(crop, (640, 640), interpolation=cv2.INTER_CUBIC)
                candidates = [
                    shape
                    for shape in infer_shims(
                        session,
                        enlarged,
                        table,
                        conf_threshold=0.03,
                        iou_threshold=0.45,
                    )
                    if shape["label"] == "wai"
                ]
                factor_x = crop.shape[1] / 640.0
                factor_y = crop.shape[0] / 640.0
                for candidate in candidates:
                    mapped = dict(candidate)
                    mapped["points"] = [
                        [
                            round(float(x0 + px * factor_x), 3),
                            round(float(y0 + py * factor_y), 3),
                        ]
                        for px, py in candidate["points"]
                    ]
                    center, radius = center_and_radius(mapped)
                    distance = float(
                        np.linalg.norm(center - target_center)
                        / max(target_radius, 2.0)
                    )
                    radius_ratio = radius / max(target_radius, 2.0)
                    if distance > 2.2 or not 1.05 <= radius_ratio <= 4.5:
                        continue
                    rank = (
                        distance
                        + 0.8 * abs(math.log(max(radius_ratio, 1e-6) / 2.18))
                        - 0.15 * float(candidate.get("score") or 0.0)
                    )
                    if best is None or rank < best[0]:
                        best = (rank, mapped, crop_size)

            target_points = np.asarray(target["points"], dtype=np.float32)
            margin = max(28, int(target_radius * 7.0))
            x0 = max(0, int(target_points[:, 0].min()) - margin)
            y0 = max(0, int(target_points[:, 1].min()) - margin)
            x1 = min(image.shape[1], int(target_points[:, 0].max()) + margin)
            y1 = min(image.shape[0], int(target_points[:, 1].max()) + margin)
            crop = image[y0:y1, x0:x1].copy()
            local_inner = target_points - np.array([x0, y0], dtype=np.float32)
            cv2.polylines(
                crop,
                [np.rint(local_inner).astype(np.int32).reshape(-1, 1, 2)],
                True,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )
            if best is None:
                unresolved += 1
                status = "none"
            else:
                _, candidate, crop_size = best
                candidate["description"] = (
                    "Outer shim contour recovered by visually reviewed "
                    "multi-scale crop inference."
                )
                nearby = []
                for outer_index, old_outer in unmatched_outer:
                    center, radius = center_and_radius(old_outer)
                    normalized = float(
                        np.linalg.norm(center - target_center) / max(radius, 2.0)
                    )
                    if normalized <= 1.6:
                        nearby.append((normalized, outer_index, old_outer))
                if nearby:
                    _, _, old_outer = min(nearby)
                    replacements_for_image.append((old_outer, candidate))
                    replacements += 1
                    action = "replace"
                else:
                    additions.append(candidate)
                    additions_count += 1
                    action = "add"
                proposed += 1
                status = f"{action} crop={crop_size}"
                local_outer = (
                    np.asarray(candidate["points"], dtype=np.float32)
                    - np.array([x0, y0], dtype=np.float32)
                )
                cv2.polylines(
                    crop,
                    [np.rint(local_outer).astype(np.int32).reshape(-1, 1, 2)],
                    True,
                    (255, 0, 255),
                    2,
                    cv2.LINE_AA,
                )
            tile = np.zeros((430, 400, 3), dtype=np.uint8)
            resize_scale = min(400 / crop.shape[1], 400 / crop.shape[0])
            resized = cv2.resize(
                crop,
                (
                    round(crop.shape[1] * resize_scale),
                    round(crop.shape[0] * resize_scale),
                ),
                interpolation=cv2.INTER_CUBIC,
            )
            tile[: resized.shape[0], : resized.shape[1]] = resized
            cv2.putText(
                tile,
                f"{json_path.stem} {status}",
                (3, 420),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.4,
                (0, 255, 255),
                1,
                cv2.LINE_AA,
            )
            tiles.append(tile)
        if args.apply and (additions or replacements_for_image):
            for old_outer, candidate in replacements_for_image:
                index = next(
                    index for index, shape in enumerate(shapes) if shape is old_outer
                )
                shapes[index] = candidate
            shapes.extend(additions)
            json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(
            f"{json_path.stem}: proposed={len(additions)+len(replacements_for_image)}"
            f"/{len(unmatched_inner)}",
            flush=True,
        )

    for page_index in range(math.ceil(len(tiles) / 9)):
        page = np.zeros((3 * 430, 3 * 400, 3), dtype=np.uint8)
        for index, tile in enumerate(tiles[page_index * 9 : (page_index + 1) * 9]):
            row, column = divmod(index, 3)
            page[row * 430 : (row + 1) * 430, column * 400 : (column + 1) * 400] = tile
        cv2.imencode(".jpg", page, [cv2.IMWRITE_JPEG_QUALITY, 96])[1].tofile(
            args.output / f"qa_{page_index + 1:03d}.jpg"
        )
    print(
        f"proposed={proposed} replacements={replacements} "
        f"additions={additions_count} unresolved={unresolved} apply={args.apply}"
    )


if __name__ == "__main__":
    main()
