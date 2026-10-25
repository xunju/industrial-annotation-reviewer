import argparse
import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort

from refine_selected_circle_contours import edge_align_path


def sigmoid(x):
    return 1.0 / (1.0 + np.exp(-np.clip(x, -30.0, 30.0)))


def letterbox(image, size=640):
    h, w = image.shape[:2]
    gain = min(size / h, size / w)
    nw, nh = round(w * gain), round(h * gain)
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_LINEAR)
    left = round((size - nw) / 2 - 0.1)
    top = round((size - nh) / 2 - 0.1)
    padded = cv2.copyMakeBorder(
        resized, top, size - nh - top, left, size - nw - left,
        cv2.BORDER_CONSTANT, value=(114, 114, 114),
    )
    blob = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB)
    blob = blob.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return blob, gain, left, top


def infer_contour(session, image):
    h, w = image.shape[:2]
    blob, gain, left, top = letterbox(image)
    detections, proto_output = session.run(
        None, {session.get_inputs()[0].name: blob}
    )
    rows = detections[0]
    row = rows[np.argmax(rows[:, 4])]
    if row[4] < 0.20:
        raise ValueError(f"low circle confidence: {row[4]:.4f}")
    box = row[:4]
    coeff = row[6:]
    proto = proto_output[0]
    mask = sigmoid(coeff @ proto.reshape(32, -1)).reshape(160, 160)
    x1, y1, x2, y2 = box / 4.0
    yy, xx = np.mgrid[:160, :160]
    mask *= (xx >= x1) & (xx < x2) & (yy >= y1) & (yy < y2)
    mask = cv2.resize(mask, (640, 640), interpolation=cv2.INTER_LINEAR)

    x0 = int(round(left))
    y0 = int(round(top))
    x_end = int(round(left + w * gain))
    y_end = int(round(top + h * gain))
    mask = mask[y0:y_end, x0:x_end]
    mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_LINEAR)
    binary = (mask >= 0.50).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        raise ValueError("circle model returned no mask contour")
    contour = max(contours, key=cv2.contourArea).reshape(-1, 2).astype(np.float64)
    return contour, float(row[4])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_root", type=Path)
    parser.add_argument("model", type=Path)
    parser.add_argument("output_root", type=Path)
    parser.add_argument("stems")
    args = parser.parse_args()

    root = args.dataset_root.resolve()
    output = args.output_root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stems = [item.strip() for item in args.stems.split(",") if item.strip()]
    paths = {
        path.stem: path
        for path in root.rglob("*.jpg")
        if path.stem in stems
    }
    session = ort.InferenceSession(
        str(args.model.resolve()), providers=["CPUExecutionProvider"]
    )
    for stem in stems:
        image_path = paths[stem]
        image = cv2.imdecode(np.fromfile(image_path, np.uint8), cv2.IMREAD_COLOR)
        model_contour, confidence = infer_contour(session, image)
        points, _, smooth_offsets = edge_align_path(image, model_contour)
        data = json.loads(image_path.with_suffix(".json").read_text(encoding="utf-8"))
        circle = next(shape for shape in data["shapes"] if shape.get("label") == "circle")
        circle["points"] = [
            [round(float(x), 3), round(float(y), 3)] for x, y in points
        ]
        circle["shape_type"] = "polygon"
        shutil.copy2(image_path, output / image_path.name)
        (output / f"{stem}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"{stem}: confidence={confidence:.6f} model_points={len(model_contour)} "
            f"edge_offset_mean={smooth_offsets.mean():.3f}"
        )


if __name__ == "__main__":
    main()
