"""Generate synthetic LabelMe examples using only Python's standard library.

No photographs, real annotations, or third-party assets are included.
Existing examples are preserved, so a second launch keeps review edits.
"""
import argparse
import json
import math
from pathlib import Path
import struct
import zlib


def polygon(cx, cy, rx, ry, count=96):
    return [[round(cx + rx * math.cos(i * 2 * math.pi / count), 3),
             round(cy + ry * math.sin(i * 2 * math.pi / count), 3)] for i in range(count)]


def shape(label, points, kind='polygon', group=None):
    return dict(label=label, points=points, shape_type=kind, group_id=group, flags={})


def png(path, shader, side=640):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    data = bytearray()
    for y in range(side):
        data.append(0)
        for x in range(side):
            data.extend(shader(x, y))
    header = struct.pack('!2I5B', side, side, 8, 2, 0, 0, 0)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header)
                     + chunk(b'IDAT', zlib.compress(bytes(data))) + chunk(b'IEND', b''))


def generate(root):
    root = Path(root)
    background = (229, 236, 231)
    table = (173, 114, 104)

    def round_scene(x, y):
        return table if ((x - 320) / 238) ** 2 + ((y - 320) / 180) ** 2 <= 1 else background

    def square_scene(x, y):
        # A trapezoid, independent of real competition imagery.
        t = (y - 120) / 390
        return table if 0 <= t <= 1 and 155 - 65 * t <= x <= 490 + 65 * t else background

    def parts_scene(x, y):
        color = table if 40 <= x <= 600 and 40 <= y <= 600 else background
        if 125 <= x <= 245 and 125 <= y <= 180 or 158 <= x <= 212 and 180 <= y <= 350:
            color = (77, 92, 95)
        if 158 <= x <= 212 and 200 <= y <= 350 and y % 14 < 3:
            color = (139, 151, 147)
        ring = ((x - 420) / 72) ** 2 + ((y - 325) / 48) ** 2
        hole = ((x - 420) / 31) ** 2 + ((y - 325) / 21) ** 2
        if ring <= 1 and hole >= 1:
            color = (168, 182, 180)
        return color

    square = [[155, 120], [490, 120], [555, 510], [90, 510]]
    bolt = [[125, 125], [245, 125], [245, 350], [125, 350]]
    square_shapes = [shape('desk', [[90, 120], [555, 510]], 'rectangle', 1)]
    square_shapes += [shape(label, [p], 'point', 1) for label, p in zip(('lt', 'rt', 'rb', 'lb'), square)]
    part_shapes = [shape('screw', [[125, 125], [245, 350]], 'rectangle', 1)]
    part_shapes += [shape(label, [p], 'point', 1) for label, p in zip(('top_left', 'top_right', 'bot_right', 'bot_left'), bolt)]
    part_shapes += [shape('wai', polygon(420, 325, 72, 48, 16), group=2),
                    shape('nei', polygon(420, 325, 31, 21, 16), group=2)]
    specs = {'round': (round_scene, [shape('circle', polygon(320, 320, 238, 180))]),
             'square': (square_scene, square_shapes), 'parts': (parts_scene, part_shapes)}
    for name, (shader, shapes) in specs.items():
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        image, annotation = folder / 'synthetic.png', folder / 'synthetic.json'
        if not image.exists():
            png(image, shader)
        if not annotation.exists():
            doc = dict(version='5.0.0', flags={}, shapes=shapes, imagePath=image.name,
                       imageData=None, imageWidth=640, imageHeight=640,
                       description='Synthetic demonstration. No real dataset or personal information.')
            annotation.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / 'demo_data')
    generate(parser.parse_args().output)
