"""Independent quadrilateral checks and a standard OpenCV homography.

Coordinates are image pixels (positive Y points down). This module contains no
model inference, training code, or private repository dependency.
"""
import cv2
import numpy as np

MIN_AREA_RATIO = 0.05
MAX_AREA_RATIO = 0.90
MIN_CORNER_DISTANCE = 20
MAX_SIDE_RATIO = 4
MAX_HOMOGRAPHY_COND = 1e7


class QuadRejected(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def order_corners_clockwise(points):
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    center = points.mean(axis=0)
    angles = np.arctan2(points[:, 1] - center[1], points[:, 0] - center[0])
    ordered = points[np.argsort(angles)]
    return np.roll(ordered, -int(np.argmin(ordered.sum(axis=1))), axis=0)


def validate_quad(points, image_shape):
    points = np.asarray(points, dtype=np.float32)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise QuadRejected('non_finite')
    height, width = image_shape[:2]
    if height <= 0 or width <= 0:
        raise QuadRejected('out_of_frame')
    if np.any(points < 0) or np.any(points[:, 0] >= width) or np.any(points[:, 1] >= height):
        raise QuadRejected('out_of_frame')
    ordered = order_corners_clockwise(points)
    if not cv2.isContourConvex(ordered):
        raise QuadRejected('not_convex')
    area = abs(cv2.contourArea(ordered))
    if area < 1:
        raise QuadRejected('degenerate_area')
    distances = np.linalg.norm(ordered[:, None, :] - ordered[None, :, :], axis=2)
    if distances[np.triu_indices(4, 1)].min() < MIN_CORNER_DISTANCE:
        raise QuadRejected('degenerate_spacing')
    sides = np.linalg.norm(np.roll(ordered, -1, axis=0) - ordered, axis=1)
    if sides.max() / sides.min() > MAX_SIDE_RATIO:
        raise QuadRejected('side_ratio')
    if not MIN_AREA_RATIO <= area / (width * height) <= MAX_AREA_RATIO:
        raise QuadRejected('area_ratio')
    destination = np.array([[0, 0], [639, 0], [639, 639], [0, 639]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(ordered, destination)
    if not np.isfinite(matrix).all():
        raise QuadRejected('homography_non_finite')
    if np.linalg.cond(matrix) > MAX_HOMOGRAPHY_COND:
        raise QuadRejected('homography_cond')
    return ordered, matrix
