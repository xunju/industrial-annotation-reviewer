import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    image = cv2.imdecode(np.fromfile(args.image, np.uint8), cv2.IMREAD_COLOR)
    data = json.loads(args.image.with_suffix(".json").read_text(encoding="utf-8"))
    shape = next(item for item in data["shapes"] if item.get("label") == "circle")
    points = np.asarray(shape["points"], np.float32)
    contour = np.rint(points).astype(np.int32).reshape(-1, 1, 2)
    cv2.polylines(image, [contour], True, (0, 255, 0), 1, cv2.LINE_AA)
    for index in range(0, len(points), 8):
        x, y = np.rint(points[index]).astype(int)
        cv2.circle(image, (x, y), 1, (0, 0, 255), -1)
    crops = {
        "top": image[60:190, 70:640],
        "right": image[80:390, 430:640],
        "bottom": image[270:390, 70:640],
        "left": image[80:390, 70:260],
    }
    args.output.mkdir(parents=True, exist_ok=True)
    for name, crop in crops.items():
        enlarged = cv2.resize(crop, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST)
        cv2.imencode(".png", enlarged)[1].tofile(args.output / f"{name}.png")


if __name__ == "__main__":
    main()
