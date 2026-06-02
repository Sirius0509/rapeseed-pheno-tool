import base64
import re
from typing import Optional
from typing import Literal

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


app = FastAPI(title="Rapeseed seed candidate service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class Roi(BaseModel):
    x: float
    y: float
    width: float
    height: float


class SeedCandidateRequest(BaseModel):
    imageDataUrl: str
    roi: Optional[Roi] = None
    foregroundMode: Literal["light", "dark"] = "light"
    minArea: float = Field(default=12, ge=1)
    maxArea: float = Field(default=1800, ge=2)
    minCircularity: float = Field(default=0.18, ge=0, le=1)
    minRoundness: float = Field(default=0.12, ge=0, le=1)
    useWatershed: bool = True
    edgeMarginRatio: float = Field(default=0.03, ge=0, le=0.3)


def decode_data_url(data_url: str) -> np.ndarray:
    payload = re.sub(r"^data:image/[^;]+;base64,", "", data_url)
    raw = base64.b64decode(payload)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Unable to decode image")
    return img


def clamp_roi(roi: Optional[Roi], width: int, height: int) -> tuple:
    if roi is None or roi.width < 2 or roi.height < 2:
        return 0, 0, width, height
    x0 = max(0, min(width - 1, int(round(roi.x))))
    y0 = max(0, min(height - 1, int(round(roi.y))))
    x1 = max(0, min(width, int(round(roi.x + roi.width))))
    y1 = max(0, min(height, int(round(roi.y + roi.height))))
    if x1 <= x0 or y1 <= y0:
        return 0, 0, width, height
    return x0, y0, x1, y1


def make_mask(gray: np.ndarray, foreground_mode: str) -> np.ndarray:
    background = cv2.GaussianBlur(gray, (0, 0), 35)
    corrected = cv2.divide(gray, background, scale=255)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(corrected)
    blurred = cv2.GaussianBlur(enhanced, (5, 5), 0)
    threshold_type = cv2.THRESH_BINARY if foreground_mode == "light" else cv2.THRESH_BINARY_INV
    _, otsu = cv2.threshold(blurred, 0, 255, threshold_type + cv2.THRESH_OTSU)
    adaptive = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        threshold_type,
        35,
        -3 if foreground_mode == "light" else 3,
    )
    mask = cv2.bitwise_or(otsu, adaptive) if foreground_mode == "light" else cv2.bitwise_and(otsu, adaptive)

    kernel = np.ones((3, 3), np.uint8)
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel, iterations=1)
    return closed


def remove_edge_margin(mask: np.ndarray, ratio: float) -> np.ndarray:
    if ratio <= 0:
        return mask
    h, w = mask.shape[:2]
    margin_x = int(w * ratio)
    margin_y = int(h * ratio)
    if margin_x * 2 >= w or margin_y * 2 >= h:
        return mask
    result = np.zeros_like(mask)
    result[margin_y : h - margin_y, margin_x : w - margin_x] = mask[margin_y : h - margin_y, margin_x : w - margin_x]
    return result


def watershed_labels(mask: np.ndarray) -> np.ndarray:
    binary = (mask > 0).astype(np.uint8)
    distance = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    if distance.max() <= 0:
        return np.zeros(mask.shape, dtype=np.int32)
    dilated = cv2.dilate(distance, np.ones((3, 3), np.uint8))
    peaks = ((distance == dilated) & (distance > distance.max() * 0.22) & (binary > 0)).astype(np.uint8)
    _, markers = cv2.connectedComponents(peaks)
    if markers.max() <= 0:
        return np.zeros(mask.shape, dtype=np.int32)
    markers = markers.astype(np.int32)
    markers[binary == 0] = -1
    color = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
    labels = cv2.watershed(color, markers)
    labels[labels < 1] = 0
    return labels


def contours_from_labels(labels: np.ndarray, mask: np.ndarray, use_watershed: bool):
    if not use_watershed:
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        return contours
    contours = []
    for label_id in np.unique(labels):
        if label_id <= 0:
            continue
        component = np.where(labels == label_id, 255, 0).astype(np.uint8)
        found, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours.extend(found)
    return contours


def contour_candidates(mask: np.ndarray, params: SeedCandidateRequest, offset: tuple[int, int]):
    mask = remove_edge_margin(mask, params.edgeMarginRatio)
    labels = watershed_labels(mask) if params.useWatershed else np.zeros(mask.shape, dtype=np.int32)
    contours = contours_from_labels(labels, mask, params.useWatershed and labels.max() > 0)
    raw_candidates = []
    ox, oy = offset
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < params.minArea:
            continue
        perimeter = float(cv2.arcLength(contour, True))
        if perimeter <= 0:
            continue
        circularity = (4 * np.pi * area) / (perimeter * perimeter)
        x, y, w, h = cv2.boundingRect(contour)
        if w <= 0 or h <= 0:
            continue
        aspect = max(w, h) / max(1, min(w, h))
        roundness = area / max(1, w * h)
        if circularity < params.minCircularity or roundness < params.minRoundness or aspect > 3.2:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            cx = x + w / 2
            cy = y + h / 2
        else:
            cx = moments["m10"] / moments["m00"]
            cy = moments["m01"] / moments["m00"]
        raw_candidates.append(
            {
                "x": round(cx + ox, 2),
                "y": round(cy + oy, 2),
                "area": round(area, 2),
                "circularity": round(float(circularity), 3),
                "roundness": round(float(roundness), 3),
                "aspect": round(float(aspect), 3),
                "estimatedSeeds": 1,
                "review": area > params.maxArea,
            }
        )

    normal_areas = [item["area"] for item in raw_candidates if params.minArea <= item["area"] <= params.maxArea]
    median_area = float(np.median(normal_areas)) if normal_areas else 0
    points = []
    warnings = []
    for item in raw_candidates:
        estimated = 1
        if median_area > 0 and item["area"] > params.maxArea:
            estimated = max(1, min(6, int(round(item["area"] / median_area))))
            item["estimatedSeeds"] = estimated
            item["review"] = True
            warnings.append(
                {
                    "type": "touching_seed",
                    "x": item["x"],
                    "y": item["y"],
                    "estimatedSeeds": estimated,
                    "area": item["area"],
                }
            )
        if item["area"] <= params.maxArea or estimated <= 1:
            points.append(item)
        else:
            # Add review points near the same component center so the frontend count reflects the estimate.
            radius = max(8, np.sqrt(item["area"] / np.pi) / 3)
            for idx in range(estimated):
                angle = (2 * np.pi * idx) / estimated
                points.append({**item, "x": round(item["x"] + radius * np.cos(angle), 2), "y": round(item["y"] + radius * np.sin(angle), 2)})

    return points, warnings, median_area


@app.get("/api/health")
def health():
    return {"ok": True, "engine": "opencv-watershed-seed-candidates"}


@app.post("/api/seed-candidates")
def seed_candidates(payload: SeedCandidateRequest):
    img = decode_data_url(payload.imageDataUrl)
    height, width = img.shape[:2]
    x0, y0, x1, y1 = clamp_roi(payload.roi, width, height)
    crop = img[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mask = make_mask(gray, payload.foregroundMode)
    points, warnings, median_area = contour_candidates(mask, payload, (x0, y0))
    points.sort(key=lambda item: (item["y"], item["x"]))
    confidence = "high"
    review_count = sum(1 for item in points if item.get("review"))
    if warnings or review_count:
        confidence = "medium"
    if len(points) == 0 or len(points) > 120:
        confidence = "low"
    return {
        "engine": "opencv-watershed-seed-candidates",
        "count": len(points),
        "points": points,
        "confidence": confidence,
        "medianArea": round(median_area, 2) if median_area else None,
        "warnings": warnings,
        "reviewCount": review_count,
        "roi": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
    }
