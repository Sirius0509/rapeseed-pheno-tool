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
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    blurred = cv2.GaussianBlur(enhanced, (5, 5), 0)
    adaptive = cv2.adaptiveThreshold(
        blurred,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35,
        -3 if foreground_mode == "light" else 3,
    )
    if foreground_mode == "dark":
        adaptive = cv2.bitwise_not(adaptive)

    kernel = np.ones((3, 3), np.uint8)
    opened = cv2.morphologyEx(adaptive, cv2.MORPH_OPEN, kernel, iterations=1)
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel, iterations=1)
    return closed


def contour_candidates(mask: np.ndarray, params: SeedCandidateRequest, offset: tuple[int, int]):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    points = []
    ox, oy = offset
    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < params.minArea or area > params.maxArea:
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
        if circularity < params.minCircularity or roundness < params.minRoundness or aspect > 3.0:
            continue
        moments = cv2.moments(contour)
        if moments["m00"] == 0:
            cx = x + w / 2
            cy = y + h / 2
        else:
            cx = moments["m10"] / moments["m00"]
            cy = moments["m01"] / moments["m00"]
        points.append(
            {
                "x": round(cx + ox, 2),
                "y": round(cy + oy, 2),
                "area": round(area, 2),
                "circularity": round(float(circularity), 3),
                "roundness": round(float(roundness), 3),
            }
        )
    return points


@app.get("/api/health")
def health():
    return {"ok": True, "engine": "opencv-adaptive-threshold-contours"}


@app.post("/api/seed-candidates")
def seed_candidates(payload: SeedCandidateRequest):
    img = decode_data_url(payload.imageDataUrl)
    height, width = img.shape[:2]
    x0, y0, x1, y1 = clamp_roi(payload.roi, width, height)
    crop = img[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mask = make_mask(gray, payload.foregroundMode)
    points = contour_candidates(mask, payload, (x0, y0))
    points.sort(key=lambda item: (item["y"], item["x"]))
    return {
        "engine": "opencv-adaptive-threshold-contours",
        "count": len(points),
        "points": points,
        "roi": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
    }
