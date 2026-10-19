"""LabelMe grouping, independent geometry checks, and perspective preview."""
import json
import math
from pathlib import Path

import cv2
import numpy as np

import quad_geometry

LABELS = ('lt', 'rt', 'rb', 'lb')


def group(shape):
    return shape.get('group_id')


def group_key(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def tables(doc):
    shapes = doc['shapes']
    boxes = [(i, s) for i, s in enumerate(shapes) if s.get('label') == 'desk']
    corners = [(i, s) for i, s in enumerate(shapes) if s.get('label') in LABELS]
    used, result = set(), []
    for index, box in boxes:
        peers = [(i, b) for i, b in boxes if group_key(group(b)) == group_key(group(box))]
        members = [(i, c) for i, c in corners if group_key(group(c)) == group_key(group(box))] if len(peers) == 1 else []
        used.update(i for i, _ in members)
        uid = 'table-' + group_key(group(box)) + '-' + str([i for i, _ in peers].index(index))
        result.append(dict(id=uid, box=box, members=members,
                           initial=[] if len(peers) == 1 else ['同一 group_id 对应多个 desk 框，无法确定角点所属桌面']))
    remaining = [(i, s) for i, s in corners if i not in used]
    keys = dict.fromkeys(group_key(group(s)) for _, s in remaining)
    for key in keys:
        result.append(dict(id='orphan-' + key, box=None,
                           members=[(i, s) for i, s in remaining if group_key(group(s)) == key],
                           initial=['四角没有唯一对应的 desk 框']))
    return result


def point(value):
    return isinstance(value, list) and len(value) == 2 and all(
        type(v) in (int, float) and math.isfinite(v) for v in value)


def check_table(table, doc):
    errors = list(table['initial'])
    box = table['box']
    bounds = None
    if (box and box.get('shape_type') == 'rectangle' and
            len(box.get('points', [])) in (2, 4) and all(point(p) for p in box['points'])):
        p = np.asarray(box['points'], dtype=float)
        bounds = (p[:, 0].min(), p[:, 1].min(), p[:, 0].max(), p[:, 1].max())
        if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
            errors.append('desk 检测框退化')
    else:
        errors.append('desk 必须是两点或四点 rectangle 检测框')
    coords = []
    for label in LABELS:
        members = [s for _, s in table['members'] if s['label'] == label]
        if len(members) != 1:
            errors.append(('缺少 ' if not members else '重复 ') + label + ' 角点')
            continue
        shape = members[0]
        if shape.get('shape_type') != 'point' or len(shape.get('points', [])) != 1 or not point(shape['points'][0]):
            errors.append(label + ' 必须是单点标注')
        else:
            coords.append(shape['points'][0])
    result = dict(id=table['id'], ok=False, issues=errors)
    if len(coords) == 4:
        pts = np.asarray(coords, dtype=np.float32)
        edges = np.roll(pts, -1, axis=0) - pts
        crosses = edges[:, 0] * np.roll(edges[:, 1], -1) - edges[:, 1] * np.roll(edges[:, 0], -1)
        if not np.all(crosses > 0):
            errors.append('按点名 lt → rt → rb → lb 的连线必须为顺时针凸四边形，不能交叉')
        canonical = quad_geometry.order_corners_clockwise(pts)
        if not np.allclose(pts, canonical, atol=1e-4, rtol=0):
            errors.append('角点名称与约定顺序不一致：lt 应为 x+y 最小的角，再按顺时针连接')
        if bounds and any(x < bounds[0]-1 or x > bounds[2]+1 or y < bounds[1]-1 or y > bounds[3]+1 for x, y in coords):
            errors.append('检测框必须包含全部四角')
        try:
            ordered, matrix = quad_geometry.validate_quad(pts, (doc['imageHeight'], doc['imageWidth']))
            side = np.linalg.norm(np.roll(ordered, -1, axis=0)-ordered, axis=1)
            area = abs(float(np.dot(ordered[:, 0], np.roll(ordered[:, 1], -1)) -
                             np.dot(ordered[:, 1], np.roll(ordered[:, 0], -1)))) / 2
            result.update(ordered=ordered.tolist(), area_ratio=area/(doc['imageHeight']*doc['imageWidth']),
                          side_ratio=float(side.max()/side.min()), homography_condition=float(np.linalg.cond(matrix)))
        except quad_geometry.QuadRejected as exc:
            reasons = {
                'zero_keypoint': '存在 (0, 0) 无效角点占位值', 'non_finite': '角点坐标非有限值',
                'out_of_frame': '角点超出允许的图像边界', 'not_convex': '四角不能形成凸四边形',
                'degenerate_area': '四边形面积退化', 'degenerate_spacing': '相邻角或对角间距不足 20 像素',
                'side_ratio': '最长边与最短边的比值超过 4', 'area_ratio': '桌面面积应占整图 5%–90%',
                'homography_cond': '透视变换不稳定，条件数超过 1e7', 'homography_non_finite': '透视矩阵无效',
            }
            errors.append(reasons.get(exc.reason.split(':')[0], '几何校验未通过') + ' [' + exc.reason + ']')
    result['issues'] = list(dict.fromkeys(errors))
    result['ok'] = not result['issues']
    return result


def check(doc):
    checked = [check_table(table, doc) for table in tables(doc)]
    errors = [] if checked else ['本图没有方桌检测框和四角，请补画后再确认']
    return dict(ok=bool(checked) and all(t['ok'] for t in checked), tables=checked, issues=errors)


def preview(image_path, table_id, doc):
    report = check(doc)
    table = next((t for t in report['tables'] if t['id'] == table_id), None)
    if not table or not table['ok']:
        raise ValueError('该方桌未通过几何校验，请先修正检测框和四角')
    data = np.fromfile(image_path, dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None or image.shape[:2] != (doc['imageHeight'], doc['imageWidth']):
        raise ValueError('图片无法解码，或尺寸与 JSON 不一致')
    _, matrix = quad_geometry.validate_quad(table['ordered'], image.shape)
    warped = cv2.warpPerspective(image, matrix, (640, 640))
    ok, encoded = cv2.imencode('.png', warped)
    if not ok:
        raise ValueError('预览图编码失败')
    return encoded.tobytes(), table
