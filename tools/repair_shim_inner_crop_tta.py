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

    tiles: list[np.ndarray] = []
    proposed = 0
    unresolved = 0
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
        image = cv2.imdecode(
            np.fromfile(json_path.with_suffix(".jpg"), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        additions = []
        for local_index, target in enumerate(unmatched, 1):
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
                    if shape["label"] == "nei"
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
                    if distance > 1.4 or not 0.18 <= radius_ratio <= 1.1:
                        continue
                    rank = (
                        distance
                        + 0.8 * abs(math.log(max(radius_ratio, 1e-6) / 0.46))
                        - 0.15 * float(candidate.get("score") or 0.0)
                    )
                    if best is None or rank < best[0]:
                        best = (rank, mapped, crop_size)
            points = np.asarray(target["points"], dtype=np.float32)
            margin = max(24, int(target_radius * 3.0))
            x0 = max(0, int(points[:, 0].min()) - margin)
            y0 = max(0, int(points[:, 1].min()) - margin)
            x1 = min(image.shape[1], int(points[:, 0].max()) + margin)
            y1 = min(image.shape[0], int(points[:, 1].max()) + margin)
            crop = image[y0:y1, x0:x1].copy()
            local_outer = points - np.array([x0, y0], dtype=np.float32)
            cv2.polylines(
                crop,
                [np.rint(local_outer).astype(np.int32).reshape(-1, 1, 2)],
                True,
                (255, 0, 255),
                2,
                cv2.LINE_AA,
            )
            if best is None:
                unresolved += 1
                status = "none"
            else:
                _, addition, chosen_size = best
                addition["description"] = (
                    "Inner shim contour recovered by visually reviewed "
                    "multi-scale crop inference."
                )
                additions.append(addition)
                proposed += 1
                status = f"crop={chosen_size}"
                local_inner = (
                    np.asarray(addition["points"], dtype=np.float32)
                    - np.array([x0, y0], dtype=np.float32)
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
                f"{json_path.stem}__{local_index} {status}",
                (3, 320),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.38,
                (0, 255, 255),
                1,
                cv2.LINE_AA,
            )
            tiles.append(tile)
        if additions and args.apply:
            data["shapes"].extend(additions)
            json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        print(
            f"{json_path.stem}: proposed={len(additions)}/{len(unmatched)}",
            flush=True,
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
    print(f"proposed={proposed} unresolved={unresolved} apply={args.apply}")


if __name__ == "__main__":
    main()
