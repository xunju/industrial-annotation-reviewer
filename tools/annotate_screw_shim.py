from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort


KEYPOINT_NAMES = ["top_left", "top_right", "bot_left", "bot_right"]


def sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


def letterbox(image: np.ndarray, size: int = 640):
    height, width = image.shape[:2]
    gain = min(size / height, size / width)
    new_w, new_h = int(round(width * gain)), int(round(height * gain))
    resized = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    left = int(round((size - new_w) / 2 - 0.1))
    top = int(round((size - new_h) / 2 - 0.1))
    right = size - new_w - left
    bottom = size - new_h - top
    padded = cv2.copyMakeBorder(
        resized, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(114,) * 3
    )
    rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
    blob = np.ascontiguousarray(rgb.transpose(2, 0, 1)[None]).astype(np.float32)
    return blob / 255.0, gain, (left, top)


def xywh_to_xyxy(boxes: np.ndarray) -> np.ndarray:
    result = boxes.copy()
    result[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    result[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    result[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    result[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    return result


def box_iou_one(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    left_top = np.maximum(box[:2], boxes[:, :2])
    right_bottom = np.minimum(box[2:], boxes[:, 2:])
    wh = np.maximum(0.0, right_bottom - left_top)
    intersection = wh[:, 0] * wh[:, 1]
    area_a = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
    area_b = np.maximum(0.0, boxes[:, 2] - boxes[:, 0]) * np.maximum(
        0.0, boxes[:, 3] - boxes[:, 1]
    )
    return intersection / np.maximum(area_a + area_b - intersection, 1e-9)


def nms(
    boxes: np.ndarray,
    scores: np.ndarray,
    classes: np.ndarray,
    iou_threshold: float,
    max_det: int = 300,
) -> np.ndarray:
    kept: list[int] = []
    for class_id in np.unique(classes):
        indices = np.flatnonzero(classes == class_id)
        order = indices[np.argsort(scores[indices])[::-1]]
        while len(order) and len(kept) < max_det:
            current = int(order[0])
            kept.append(current)
            if len(order) == 1:
                break
            overlaps = box_iou_one(boxes[current], boxes[order[1:]])
            order = order[1:][overlaps <= iou_threshold]
    return np.asarray(sorted(kept, key=lambda i: scores[i], reverse=True), dtype=int)


def scale_xyxy(
    boxes: np.ndarray, gain: float, pad: tuple[int, int], width: int, height: int
) -> np.ndarray:
    scaled = boxes.copy()
    scaled[:, [0, 2]] = (scaled[:, [0, 2]] - pad[0]) / gain
    scaled[:, [1, 3]] = (scaled[:, [1, 3]] - pad[1]) / gain
    scaled[:, [0, 2]] = np.clip(scaled[:, [0, 2]], 0, width - 1)
    scaled[:, [1, 3]] = np.clip(scaled[:, [1, 3]], 0, height - 1)
    return scaled


def inside_table(x: float, y: float, table: np.ndarray) -> bool:
    contour = table.astype(np.float32).reshape(-1, 1, 2)
    return cv2.pointPolygonTest(contour, (float(x), float(y)), False) >= 0


def base_shape(
    label: str,
    score: float,
    points: list[list[float]],
    shape_type: str,
    group_id: int | None = None,
) -> dict:
    return {
        "label": label,
        "score": round(float(score), 6),
        "points": [[round(float(x), 3), round(float(y), 3)] for x, y in points],
        "group_id": group_id,
        "description": None,
        "difficult": False,
        "shape_type": shape_type,
        "flags": {},
        "attributes": {},
        "kie_linking": [],
    }


def infer_pose(
    session: ort.InferenceSession,
    image: np.ndarray,
    table: np.ndarray,
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.60,
    keypoint_threshold: float = 0.25,
) -> list[dict]:
    height, width = image.shape[:2]
    blob, gain, pad = letterbox(image)
    raw = session.run(None, {session.get_inputs()[0].name: blob})[0][0].T
    scores = raw[:, 4]
    valid = scores >= conf_threshold
    raw, scores = raw[valid], scores[valid]
    if not len(raw):
        return []
    boxes_input = xywh_to_xyxy(raw[:, :4])
    classes = np.zeros(len(raw), dtype=np.int32)
    keep = nms(boxes_input, scores, classes, iou_threshold)
    raw, scores, boxes_input = raw[keep], scores[keep], boxes_input[keep]
    boxes = scale_xyxy(boxes_input, gain, pad, width, height)

    shapes: list[dict] = []
    group_id = 0
    for row, score, box in zip(raw, scores, boxes):
        x1, y1, x2, y2 = box
        if not inside_table((x1 + x2) / 2, (y1 + y2) / 2, table):
            continue
        shapes.append(
            base_shape(
                "screw",
                score,
                [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
                "rectangle",
                group_id,
            )
        )
        keypoints = row[5:].reshape(4, 3)
        for name, (x, y, visibility) in zip(KEYPOINT_NAMES, keypoints):
            sx = (float(x) - pad[0]) / gain
            sy = (float(y) - pad[1]) / gain
            sx = float(np.clip(sx, 0, width - 1))
            sy = float(np.clip(sy, 0, height - 1))
            if (
                visibility >= keypoint_threshold
                and x1 <= sx <= x2
                and y1 <= sy <= y2
            ):
                shapes.append(
                    base_shape(
                        name,
                        visibility,
                        [[sx, sy]],
                        "point",
                        group_id,
                    )
                )
        group_id += 1
    return shapes


def crop_masks(masks: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    count, height, width = masks.shape
    x1, y1, x2, y2 = np.split(boxes[:, :, None], 4, axis=1)
    rows = np.arange(width, dtype=np.float32)[None, None, :]
    cols = np.arange(height, dtype=np.float32)[None, :, None]
    return masks * ((rows >= x1) & (rows < x2) & (cols >= y1) & (cols < y2))


def infer_shims(
    session: ort.InferenceSession,
    image: np.ndarray,
    table: np.ndarray,
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.45,
) -> list[dict]:
    height, width = image.shape[:2]
    blob, gain, pad = letterbox(image)
    output0, proto_output = session.run(
        None, {session.get_inputs()[0].name: blob}
    )
    raw = output0[0].T
    class_scores = raw[:, 4:6]
    classes = class_scores.argmax(axis=1)
    scores = class_scores[np.arange(len(raw)), classes]
    valid = scores >= conf_threshold
    raw, scores, classes = raw[valid], scores[valid], classes[valid]
    if not len(raw):
        return []
    boxes_input = xywh_to_xyxy(raw[:, :4])
    keep = nms(boxes_input, scores, classes, iou_threshold)
    raw, scores, classes, boxes_input = (
        raw[keep],
        scores[keep],
        classes[keep],
        boxes_input[keep],
    )

    proto = proto_output[0]
    mask_h, mask_w = proto.shape[1:]
    masks = sigmoid(raw[:, 6:] @ proto.reshape(32, -1)).reshape(
        -1, mask_h, mask_w
    )
    proto_boxes = boxes_input.copy()
    proto_boxes[:, [0, 2]] *= mask_w / 640.0
    proto_boxes[:, [1, 3]] *= mask_h / 640.0
    masks = crop_masks(masks, proto_boxes)

    shapes: list[dict] = []
    labels = ["wai", "nei"]
    for mask, score, class_id, box in zip(masks, scores, classes, boxes_input):
        center_x = ((box[0] + box[2]) / 2 - pad[0]) / gain
        center_y = ((box[1] + box[3]) / 2 - pad[1]) / gain
        if not inside_table(center_x, center_y, table):
            continue
        full_mask = cv2.resize(mask, (640, 640), interpolation=cv2.INTER_LINEAR)
        binary = (full_mask > 0.5).astype(np.uint8)
        contours, _ = cv2.findContours(
            binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
        )
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        epsilon = 0.002 * cv2.arcLength(contour, True)
        polygon = cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2)
        polygon = polygon.astype(np.float64)
        polygon[:, 0] = (polygon[:, 0] - pad[0]) / gain
        polygon[:, 1] = (polygon[:, 1] - pad[1]) / gain
        polygon[:, 0] = np.clip(polygon[:, 0], 0, width - 1)
        polygon[:, 1] = np.clip(polygon[:, 1], 0, height - 1)
        if len(polygon) < 3:
            continue
        shapes.append(
            base_shape(
                labels[int(class_id)],
                score,
                polygon.tolist(),
                "polygon",
            )
        )
    return shapes


def select_images(paths: list[Path], limit: int | None) -> list[Path]:
    if limit is None or limit >= len(paths):
        return paths
    indices = np.linspace(0, len(paths) - 1, limit, dtype=int)
    return [paths[index] for index in indices]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pose-model", type=Path, required=True)
    parser.add_argument("--shim-model", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--max-new", type=int)
    args = parser.parse_args()

    if args.output.exists() and not args.resume:
        raise RuntimeError(f"Output already exists: {args.output}")
    output_images = args.output / "images"
    output_images.mkdir(parents=True, exist_ok=True)

    pose_session = ort.InferenceSession(
        str(args.pose_model), providers=["CPUExecutionProvider"]
    )
    shim_session = ort.InferenceSession(
        str(args.shim_model), providers=["CPUExecutionProvider"]
    )

    source_images = sorted((args.source / "images").glob("*.jpg"))
    selected = select_images(source_images, args.limit)
    audit_rows: list[dict[str, int | str]] = []
    new_count = 0

    for index, image_path in enumerate(selected, 1):
        output_json = output_images / image_path.with_suffix(".json").name
        if args.resume and output_json.exists():
            existing = json.loads(output_json.read_text(encoding="utf-8"))
            existing_shapes = existing.get("shapes", [])
            screw_count = sum(s["label"] == "screw" for s in existing_shapes)
            audit_rows.append(
                {
                    "image": image_path.name,
                    "screws": screw_count,
                    "screw_keypoints": sum(
                        s["shape_type"] == "point" for s in existing_shapes
                    ),
                    "shim_wai": sum(s["label"] == "wai" for s in existing_shapes),
                    "shim_nei": sum(s["label"] == "nei" for s in existing_shapes),
                }
            )
            continue
        if args.max_new is not None and new_count >= args.max_new:
            break

        image = cv2.imdecode(
            np.fromfile(image_path, dtype=np.uint8), cv2.IMREAD_COLOR
        )
        data = json.loads(
            image_path.with_suffix(".json").read_text(encoding="utf-8")
        )
        circle = next(
            (
                shape
                for shape in data["shapes"]
                if shape["label"] == "circle"
            ),
            None,
        )
        if circle is None:
            angles = np.linspace(0, 2 * np.pi, 128, endpoint=False)
            table = np.column_stack(
                [320.0 + 318.0 * np.cos(angles), 320.0 + 318.0 * np.sin(angles)]
            ).astype(np.float32)
        else:
            table = np.asarray(circle["points"], dtype=np.float32)
        pose_shapes = infer_pose(pose_session, image, table)
        shim_shapes = infer_shims(shim_session, image, table)
        data["shapes"].extend(pose_shapes)
        data["shapes"].extend(shim_shapes)
        data["description"] = (
            "Round table plus model-assisted screw pose and shim contours; "
            "awaiting visual review."
        )

        shutil.copy2(image_path, output_images / image_path.name)
        output_json.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        new_count += 1
        screw_count = sum(s["label"] == "screw" for s in pose_shapes)
        audit_rows.append(
            {
                "image": image_path.name,
                "screws": screw_count,
                "screw_keypoints": len(pose_shapes) - screw_count,
                "shim_wai": sum(s["label"] == "wai" for s in shim_shapes),
                "shim_nei": sum(s["label"] == "nei" for s in shim_shapes),
            }
        )
        print(
            f"[{index}/{len(selected)} new={new_count}] {image_path.name} "
            f"screws={audit_rows[-1]['screws']} "
            f"wai={audit_rows[-1]['shim_wai']} "
            f"nei={audit_rows[-1]['shim_nei']}",
            flush=True,
        )

    with (args.output / "inference_audit.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["image", "screws", "screw_keypoints", "shim_wai", "shim_nei"],
        )
        writer.writeheader()
        writer.writerows(audit_rows)


if __name__ == "__main__":
    main()
