from __future__ import annotations

import argparse
import csv
import json
import math
import shutil
from collections import Counter
from pathlib import Path


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}


def resample_closed_polygon(points: list[list[float]], count: int = 96) -> list[list[float]]:
    polygon = [(float(point[0]), float(point[1])) for point in points]
    if len(polygon) > 1 and polygon[0] == polygon[-1]:
        polygon.pop()
    if len(polygon) < 3:
        raise ValueError("polygon has fewer than three distinct vertices")

    edges: list[tuple[tuple[float, float], tuple[float, float], float]] = []
    perimeter = 0.0
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        if length > 0:
            edges.append((start, end, length))
            perimeter += length
    if perimeter == 0:
        raise ValueError("polygon perimeter is zero")

    result: list[list[float]] = []
    edge_index = 0
    edge_start_distance = 0.0
    for sample_index in range(count):
        target_distance = perimeter * sample_index / count
        while (
            edge_index < len(edges) - 1
            and edge_start_distance + edges[edge_index][2] < target_distance
        ):
            edge_start_distance += edges[edge_index][2]
            edge_index += 1
        start, end, length = edges[edge_index]
        ratio = (target_distance - edge_start_distance) / length
        x = start[0] + ratio * (end[0] - start[0])
        y = start[1] + ratio * (end[1] - start[1])
        result.append([round(x, 6), round(y, 6)])
    return result


def build_dataset(source: Path, destination: Path) -> None:
    source = source.resolve()
    destination = destination.resolve()
    building = destination.with_name(destination.name + ".building")

    if destination.exists():
        raise FileExistsError(f"destination already exists: {destination}")
    if building.exists():
        raise FileExistsError(f"temporary destination already exists: {building}")

    images = sorted(
        (
            path
            for path in source.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
        ),
        key=lambda path: str(path.relative_to(source)).lower(),
    )
    if not images:
        raise RuntimeError("no images found")

    duplicate_names = [
        name for name, count in Counter(path.name.lower() for path in images).items() if count > 1
    ]
    if duplicate_names:
        raise RuntimeError(f"duplicate image basenames found: {duplicate_names[:10]}")

    output_images = building / "images"
    output_images.mkdir(parents=True)
    rows: list[dict[str, str | int]] = []
    original_point_counts: Counter[int] = Counter()
    resampled_count = 0

    for index, image_path in enumerate(images, start=1):
        annotation_path = image_path.with_suffix(".json")
        if not annotation_path.exists():
            raise FileNotFoundError(f"missing annotation for {image_path}")

        annotation = json.loads(annotation_path.read_text(encoding="utf-8-sig"))
        shapes = annotation.get("shapes")
        if not isinstance(shapes, list) or len(shapes) != 1:
            raise ValueError(f"expected exactly one shape in {annotation_path}")
        shape = shapes[0]
        if shape.get("label") != "circle" or shape.get("shape_type") != "polygon":
            raise ValueError(f"unexpected shape schema in {annotation_path}")

        points = shape.get("points")
        if not isinstance(points, list):
            raise ValueError(f"missing polygon points in {annotation_path}")
        original_count = len(points)
        original_point_counts[original_count] += 1
        if original_count != 96:
            shape["points"] = resample_closed_polygon(points, 96)
            resampled_count += 1

        annotation["imagePath"] = image_path.name
        annotation["imageData"] = None
        annotation.setdefault("flags", {})

        output_image = output_images / image_path.name
        output_annotation = output_images / annotation_path.name
        shutil.copy2(image_path, output_image)
        output_annotation.write_text(
            json.dumps(annotation, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        rows.append(
            {
                "index": index,
                "source_dataset": image_path.relative_to(source).parts[0],
                "source_image": str(image_path.relative_to(source)),
                "output_image": str(output_image.relative_to(building)),
                "original_points": original_count,
                "output_points": 96,
                "resampled": "yes" if original_count != 96 else "no",
            }
        )

    with (building / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    (building / "classes.txt").write_text("circle\n", encoding="utf-8")
    summary = {
        "format": "XAnyLabeling",
        "images": len(images),
        "annotations": len(images),
        "class_names": ["circle"],
        "shapes_per_annotation": 1,
        "shape_type": "polygon",
        "points_per_polygon": 96,
        "original_point_counts": dict(sorted(original_point_counts.items())),
        "resampled_annotations": resampled_count,
        "source_directory": str(source),
    }
    (building / "dataset_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (building / "README.md").write_text(
        "# 第一轮圆桌统一标注数据\n\n"
        "- 格式：XAnyLabeling JSON\n"
        f"- 图片：{len(images)} 张\n"
        f"- 标注：{len(images)} 份，与图片同名并位于 `images/`\n"
        "- 类别：`circle`\n"
        "- 轮廓：闭合 `polygon`，统一为 96 点\n"
        f"- 经闭合弧长均匀重采样的标注：{resampled_count} 份\n"
        "- `manifest.csv` 记录每个文件的原始来源和点数\n"
        "- 原始数据目录未修改\n",
        encoding="utf-8",
    )

    building.rename(destination)
    print(json.dumps(summary, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    build_dataset(args.source, args.destination)


if __name__ == "__main__":
    main()
