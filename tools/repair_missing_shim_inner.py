from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from annotate_screw_shim import infer_shims


def center_and_radius(shape: dict) -> tuple[np.ndarray, float]:
    points = np.asarray(shape["points"], dtype=np.float32)
    center = points.mean(axis=0)
    area = abs(float(cv2.contourArea(points.reshape(-1, 1, 2))))
    radius = float(np.sqrt(max(area, 1.0) / np.pi))
    return center, radius


def matched_indices(
    outer_shapes: list[dict], inner_shapes: list[dict]
) -> tuple[set[int], set[int]]:
    pairs: list[tuple[float, int, int]] = []
    for outer_index, outer in enumerate(outer_shapes):
        outer_center, outer_radius = center_and_radius(outer)
        for inner_index, inner in enumerate(inner_shapes):
            inner_center, _ = center_and_radius(inner)
            normalized = float(
                np.linalg.norm(outer_center - inner_center)
                / max(outer_radius, 2.0)
            )
            if normalized <= 0.65:
                pairs.append((normalized, outer_index, inner_index))
    used_outer: set[int] = set()
    used_inner: set[int] = set()
    for _, outer_index, inner_index in sorted(pairs):
        if outer_index in used_outer or inner_index in used_inner:
            continue
        used_outer.add(outer_index)
        used_inner.add(inner_index)
    return used_outer, used_inner


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    image_dir = args.dataset / "images"
    session = ort.InferenceSession(
        str(args.model), providers=["CPUExecutionProvider"]
    )
    angles = np.linspace(0, 2 * np.pi, 128, endpoint=False)
    table = np.column_stack(
        [320.0 + 318.0 * np.cos(angles), 320.0 + 318.0 * np.sin(angles)]
    ).astype(np.float32)

    mismatch_images = 0
    repaired_images = 0
    added_inner = 0
    unresolved_outer = 0

    for json_path in sorted(image_dir.glob("*.json")):
        data = json.loads(json_path.read_text(encoding="utf-8"))
        outer = [shape for shape in data["shapes"] if shape["label"] == "wai"]
        inner = [shape for shape in data["shapes"] if shape["label"] == "nei"]
        used_outer, _ = matched_indices(outer, inner)
        unmatched_outer = [
            shape for index, shape in enumerate(outer) if index not in used_outer
        ]
        if not unmatched_outer:
            continue
        mismatch_images += 1

        image_path = json_path.with_suffix(".jpg")
        image = cv2.imdecode(
            np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        low_shapes = infer_shims(
            session,
            image,
            table,
            conf_threshold=0.05,
            iou_threshold=0.45,
        )
        candidates = [shape for shape in low_shapes if shape["label"] == "nei"]

        additions: list[dict] = []
        used_candidates: set[int] = set()
        for target in unmatched_outer:
            target_center, target_radius = center_and_radius(target)
            choices: list[tuple[float, float, int]] = []
            for candidate_index, candidate in enumerate(candidates):
                if candidate_index in used_candidates:
                    continue
                candidate_center, _ = center_and_radius(candidate)
                normalized = float(
                    np.linalg.norm(target_center - candidate_center)
                    / max(target_radius, 2.0)
                )
                if normalized <= 0.65:
                    choices.append(
                        (
                            normalized,
                            -float(candidate.get("score") or 0.0),
                            candidate_index,
                        )
                    )
            if choices:
                _, _, best_index = min(choices)
                best = candidates[best_index]
                # Do not duplicate an already present low-confidence contour.
                if all(
                    np.linalg.norm(
                        center_and_radius(best)[0] - center_and_radius(existing)[0]
                    )
                    > 2.0
                    for existing in inner + additions
                ):
                    additions.append(best)
                    used_candidates.add(best_index)

        if additions:
            repaired_images += 1
            added_inner += len(additions)
            if args.apply:
                data["shapes"].extend(additions)
                data["description"] = (
                    "ROI screw/shim annotations; missing shim inner contours "
                    "recovered by low-threshold model inference paired to reviewed "
                    "outer contours."
                )
                json_path.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        unresolved_outer += len(unmatched_outer) - len(additions)

    print(
        f"mismatch_images={mismatch_images} repaired_images={repaired_images} "
        f"added_inner={added_inner} unresolved_outer={unresolved_outer} "
        f"apply={args.apply}"
    )


if __name__ == "__main__":
    main()
