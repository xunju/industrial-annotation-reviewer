import argparse
import json
from pathlib import Path

import numpy as np

from refine_circle_annotations import resample_closed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("json_path", type=Path)
    args = parser.parse_args()
    data = json.loads(args.json_path.read_text(encoding="utf-8"))
    circle = next(shape for shape in data["shapes"] if shape.get("label") == "circle")
    points = np.asarray(circle["points"], np.float64)
    points = resample_closed(points, 96)
    circle["points"] = [
        [round(float(x), 3), round(float(y), 3)] for x, y in points
    ]
    circle["shape_type"] = "polygon"
    args.json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
