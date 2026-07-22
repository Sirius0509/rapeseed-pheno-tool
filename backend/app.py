import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Optional
from typing import Literal

import cv2
import numpy as np
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field


app = FastAPI(title="Rapeseed seed candidate service")
BASE_DIR = Path(__file__).resolve().parent
RUNS_DIR = BASE_DIR / "runs"
DATA_DIR = BASE_DIR / "data"
SILIQUE_RECORDS_PATH = DATA_DIR / "silique_records.json"
TRAIN_JOBS = {}
TRAIN_LOCK = threading.Lock()
RECORDS_LOCK = threading.Lock()

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
    foregroundMode: Literal["auto", "light", "dark"] = "auto"
    minArea: float = Field(default=12, ge=1)
    maxArea: float = Field(default=1800, ge=2)
    minCircularity: float = Field(default=0.18, ge=0, le=1)
    minRoundness: float = Field(default=0.12, ge=0, le=1)
    maxAspect: float = Field(default=3.2, ge=1, le=10)
    touchingAreaMultiplier: float = Field(default=4.0, ge=1, le=10)
    useWatershed: bool = True
    edgeMarginRatio: float = Field(default=0.03, ge=0, le=0.3)
    useYolo: bool = True
    analysisEngine: Literal["auto", "imagej", "yolo"] = "auto"
    threshold: int = Field(default=73, ge=0, le=255)


class SeedPoint(BaseModel):
    x: float
    y: float
    area: Optional[float] = None
    source: Optional[str] = None
    review: Optional[bool] = None


class ViviparyRequest(BaseModel):
    imageDataUrl: str
    roi: Optional[Roi] = None
    mmPerPixel: Optional[float] = Field(default=None, gt=0)
    minProtrusionMm: float = Field(default=0.5, ge=0.05, le=20)
    minSeedArea: float = Field(default=20, ge=2)
    maxSeedArea: float = Field(default=5000, ge=20)
    foregroundMode: Literal["auto", "light", "dark"] = "auto"


class TrainingRecord(BaseModel):
    id: str
    imageDataUrl: str
    imageWidth: int
    imageHeight: int
    sampleId: Optional[str] = ""
    siliqueId: Optional[str] = ""
    quality: Optional[str] = "good"
    seedPoints: list[SeedPoint] = Field(default_factory=list)
    autoSeedPoints: list[SeedPoint] = Field(default_factory=list)


class TrainRequest(BaseModel):
    records: list[TrainingRecord] = Field(default_factory=list)
    model: str = "yolo11n.pt"
    epochs: int = Field(default=50, ge=1, le=300)
    imgsz: int = Field(default=1024, ge=320, le=2048)
    batch: int = Field(default=4, ge=1, le=64)
    trainRatio: float = Field(default=0.8, ge=0.5, le=0.95)
    dryRun: bool = False


class StoredRecordsRequest(BaseModel):
    records: list[dict] = Field(default_factory=list)


class TrainStoredRequest(BaseModel):
    model: str = "yolo11n.pt"
    epochs: int = Field(default=50, ge=1, le=300)
    imgsz: int = Field(default=1024, ge=320, le=2048)
    batch: int = Field(default=4, ge=1, le=64)
    trainRatio: float = Field(default=0.8, ge=0.5, le=0.95)
    dryRun: bool = False


class TrainCloudRequest(BaseModel):
    supabaseUrl: str
    anonKey: str
    accessToken: str
    userId: str
    model: str = "yolo11n.pt"
    epochs: int = Field(default=50, ge=1, le=300)
    imgsz: int = Field(default=1024, ge=320, le=2048)
    batch: int = Field(default=4, ge=1, le=64)
    trainRatio: float = Field(default=0.8, ge=0.5, le=0.95)
    dryRun: bool = False


def decode_data_url(data_url: str) -> np.ndarray:
    payload = re.sub(r"^data:image/[^;]+;base64,", "", data_url)
    raw = base64.b64decode(payload)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Unable to decode image")
    return img


def decode_data_url_bytes(data_url: str) -> tuple[bytes, str]:
    match = re.match(r"^data:image/([^;]+);base64,(.+)$", data_url)
    if not match:
        raise ValueError("Invalid image data URL")
    ext = "jpg" if match.group(1).lower() in {"jpeg", "jpg"} else match.group(1).lower()
    return base64.b64decode(match.group(2)), ext


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


def make_mask(gray: np.ndarray, foreground_mode: Literal["light", "dark"]) -> np.ndarray:
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


def preprocess_gray(gray: np.ndarray) -> np.ndarray:
    background = cv2.GaussianBlur(gray, (0, 0), 35)
    corrected = cv2.divide(gray, background, scale=255)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return cv2.GaussianBlur(clahe.apply(corrected), (5, 5), 0)


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


def imagej_particle_candidates(
    img: np.ndarray,
    params: SeedCandidateRequest,
    offset: tuple[int, int],
) -> tuple[list[dict], list[dict], Optional[float]]:
    """ImageJ-style particle analysis implemented with deployable OpenCV primitives.

    The sequence mirrors the common Fiji workflow: 8-bit conversion, rolling-ball-like
    background subtraction, thresholding, hole filling, watershed separation and
    Analyze Particles shape filters.
    """
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # Fiji's Color Threshold equivalent is much more stable on the blue boards
    # used by this project because it rejects white grid lines before watershed.
    color_mask = (
        (((hsv[:, :, 0] <= 35) | (hsv[:, :, 0] >= 172)))
        & (hsv[:, :, 1] >= 40)
        & (hsv[:, :, 2] <= 245)
    ).astype(np.uint8) * 255
    color_fraction = float(np.count_nonzero(color_mask)) / max(1, color_mask.size)
    if 0.001 <= color_fraction <= 0.25:
        mask = color_mask
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    else:
        mode = params.foregroundMode
        if mode == "auto":
            dark_score = float(np.mean(gray <= params.threshold))
            light_score = float(np.mean(gray >= 255 - params.threshold))
            mode = "dark" if dark_score <= light_score else "light"
        threshold_type = cv2.THRESH_BINARY_INV if mode == "dark" else cv2.THRESH_BINARY
        threshold_value = params.threshold if mode == "dark" else 255 - params.threshold
        _, mask = cv2.threshold(cv2.medianBlur(gray, 3), threshold_value, 255, threshold_type)
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=1)
    flood = mask.copy()
    flood_mask = np.zeros((mask.shape[0] + 2, mask.shape[1] + 2), np.uint8)
    cv2.floodFill(flood, flood_mask, (0, 0), 255)
    mask = cv2.bitwise_or(mask, cv2.bitwise_not(flood))

    points, warnings, median_area = contour_candidates(mask, params, offset)
    for point in points:
        point["source"] = "imagej_particle_analysis"
    return points, warnings, median_area


def component_mask_from_background(img: np.ndarray, mode: str) -> np.ndarray:
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    border = np.concatenate((lab[0], lab[-1], lab[:, 0], lab[:, -1]), axis=0)
    background = np.median(border, axis=0)
    color_distance = np.linalg.norm(lab - background, axis=2)
    _, lab_mask = cv2.threshold(np.clip(color_distance, 0, 255).astype(np.uint8), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    border_gray = np.concatenate((gray[0], gray[-1], gray[:, 0], gray[:, -1]))
    background_gray = float(np.median(border_gray))
    if mode == "auto":
        mode = "dark" if float(np.median(gray)) >= background_gray * 0.85 else "light"
    median_border = float(np.median(border_gray))
    mad = float(np.median(np.abs(border_gray.astype(np.float32) - median_border)))
    delta = min(40, max(15, mad * 3.0))
    intensity_mask = gray < background_gray - delta if mode == "dark" else gray > background_gray + delta
    border_hsv = np.concatenate((hsv[0], hsv[-1], hsv[:, 0], hsv[:, -1]), axis=0)
    background_hue = float(np.median(border_hsv[:, 0]))
    background_saturation = float(np.median(border_hsv[:, 1]))
    hue_distance = np.abs(hsv[:, :, 0].astype(np.float32) - background_hue)
    hue_distance = np.minimum(hue_distance, 180 - hue_distance)
    hue_mask = ((hue_distance >= 18) & (hsv[:, :, 1] >= 35)).astype(np.uint8) * 255
    if background_saturation >= 45:
        # Black rapeseed has low saturation and may be missed by hue alone.
        mask = cv2.bitwise_or(cv2.bitwise_and(hue_mask, lab_mask), intensity_mask.astype(np.uint8) * 255)
    else:
        mask = cv2.bitwise_and(lab_mask, intensity_mask.astype(np.uint8) * 255)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)


def dense_seed_peaks(mask: np.ndarray) -> tuple[list[tuple[float, float, float]], np.ndarray]:
    binary = (mask > 0).astype(np.uint8)
    distance_map = cv2.distanceTransform(binary, cv2.DIST_L2, 5)
    preliminary = ((distance_map == cv2.dilate(distance_map, np.ones((3, 3), np.uint8))) & (distance_map >= 1.5)).astype(np.uint8)
    count, _, stats, centroids = cv2.connectedComponentsWithStats(preliminary, 8)
    radii = []
    for index in range(1, count):
        if stats[index, cv2.CC_STAT_AREA] > 30:
            continue
        x, y = centroids[index]
        radii.append(float(distance_map[min(distance_map.shape[0] - 1, int(round(y))), min(distance_map.shape[1] - 1, int(round(x)))]))
    expected_radius = float(np.median(radii)) if radii else 3.0
    expected_radius = min(20.0, max(2.0, expected_radius))
    kernel_size = int(round(expected_radius * 2))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel_size = min(41, max(5, kernel_size))
    local_max = cv2.dilate(distance_map, np.ones((kernel_size, kernel_size), np.uint8))
    peaks = ((distance_map == local_max) & (distance_map >= max(1.5, expected_radius * 0.35))).astype(np.uint8)
    peak_count, _, peak_stats, peak_centroids = cv2.connectedComponentsWithStats(peaks, 8)
    result = []
    for index in range(1, peak_count):
        if peak_stats[index, cv2.CC_STAT_AREA] > max(30, kernel_size * kernel_size):
            continue
        x, y = peak_centroids[index]
        radius = float(distance_map[min(distance_map.shape[0] - 1, int(round(y))), min(distance_map.shape[1] - 1, int(round(x)))])
        result.append((float(x), float(y), radius))
    return result, distance_map


def vivipary_candidates(img: np.ndarray, payload: ViviparyRequest, offset: tuple[int, int]) -> list[dict]:
    mask = component_mask_from_background(img, payload.foregroundMode)
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    ox, oy = offset
    candidates = []
    peaks, _ = dense_seed_peaks(mask)
    peaks_by_label: dict[int, list[tuple[float, float, float]]] = {}
    for peak in peaks:
        px, py, _ = peak
        label_id = int(labels[min(labels.shape[0] - 1, int(round(py))), min(labels.shape[1] - 1, int(round(px)))])
        if label_id > 0:
            peaks_by_label.setdefault(label_id, []).append(peak)

    for label_id, component_peaks in peaks_by_label.items():
        area = float(stats[label_id, cv2.CC_STAT_AREA])
        estimated_area = area / max(1, len(component_peaks))
        if estimated_area < payload.minSeedArea:
            continue
        if len(component_peaks) > 1 or area > payload.maxSeedArea:
            for center_x, center_y, radius in component_peaks:
                candidates.append({
                    "x": round(center_x + ox, 2), "y": round(center_y + oy, 2),
                    "tipX": None, "tipY": None, "area": round(estimated_area, 2),
                    "bodyRadiusPx": round(radius, 2), "protrusionLengthPx": None,
                    "protrusionLengthMm": None, "vivipary": False, "aspect": None,
                    "circularity": None, "review": True, "source": "dense-seed-estimate",
                })
            continue
        component = np.where(labels == label_id, 255, 0).astype(np.uint8)
        contours, _ = cv2.findContours(component, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        if not contours:
            continue
        contour = max(contours, key=cv2.contourArea)
        distance_map = cv2.distanceTransform((component > 0).astype(np.uint8), cv2.DIST_L2, 5)
        _, radius, _, center = cv2.minMaxLoc(distance_map)
        if radius < 1:
            continue
        points = contour[:, 0, :].astype(np.float32)
        center_array = np.array(center, dtype=np.float32)
        distances = np.linalg.norm(points - center_array, axis=1)
        farthest_index = int(np.argmax(distances))
        farthest = points[farthest_index]
        protrusion_px = max(0.0, float(distances[farthest_index]) - radius * 1.35)
        protrusion_mm = protrusion_px * payload.mmPerPixel if payload.mmPerPixel else None
        threshold_px = payload.minProtrusionMm / payload.mmPerPixel if payload.mmPerPixel else radius * 0.35
        is_vivipary = protrusion_px >= threshold_px
        x, y, width, height = cv2.boundingRect(contour)
        aspect = max(width, height) / max(1, min(width, height))
        perimeter = float(cv2.arcLength(contour, True))
        circularity = 4 * np.pi * area / (perimeter * perimeter) if perimeter else 0
        candidates.append({
            "x": round(float(center[0] + ox), 2),
            "y": round(float(center[1] + oy), 2),
            "tipX": round(float(farthest[0] + ox), 2),
            "tipY": round(float(farthest[1] + oy), 2),
            "area": round(area, 2),
            "bodyRadiusPx": round(float(radius), 2),
            "protrusionLengthPx": round(protrusion_px, 2),
            "protrusionLengthMm": round(float(protrusion_mm), 3) if protrusion_mm is not None else None,
            "vivipary": bool(is_vivipary),
            "aspect": round(float(aspect), 3),
            "circularity": round(float(circularity), 3),
            "review": bool(is_vivipary or aspect > 1.8 or circularity < 0.35),
            "source": "imagej-vivipary-analysis",
        })
    return sorted(candidates, key=lambda item: (item["y"], item["x"]))


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
        if circularity < params.minCircularity or roundness < params.minRoundness or aspect > params.maxAspect:
            continue
        if area > params.maxArea * params.touchingAreaMultiplier:
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


def blob_candidates(gray: np.ndarray, params: SeedCandidateRequest, offset: tuple[int, int], foreground_mode: Literal["light", "dark"]):
    processed = preprocess_gray(gray)
    if foreground_mode == "dark":
        processed = cv2.bitwise_not(processed)
    detector_params = cv2.SimpleBlobDetector_Params()
    detector_params.filterByArea = True
    detector_params.minArea = max(3, params.minArea * 0.45)
    detector_params.maxArea = params.maxArea * 2.8
    detector_params.filterByCircularity = True
    detector_params.minCircularity = max(0.08, params.minCircularity * 0.45)
    detector_params.filterByConvexity = False
    detector_params.filterByInertia = True
    detector_params.minInertiaRatio = 0.08
    detector_params.minThreshold = 5
    detector_params.maxThreshold = 245
    detector_params.thresholdStep = 8
    detector = cv2.SimpleBlobDetector_create(detector_params)
    keypoints = detector.detect(processed)
    ox, oy = offset
    result = []
    for keypoint in keypoints:
        area = float(np.pi * (keypoint.size / 2) ** 2)
        result.append(
            {
                "x": round(keypoint.pt[0] + ox, 2),
                "y": round(keypoint.pt[1] + oy, 2),
                "area": round(area, 2),
                "circularity": None,
                "roundness": None,
                "aspect": 1,
                "estimatedSeeds": 1,
                "review": area > params.maxArea,
                "source": f"blob_{foreground_mode}",
            }
        )
    return result


def hough_candidates(gray: np.ndarray, params: SeedCandidateRequest, offset: tuple[int, int]):
    processed = preprocess_gray(gray)
    min_radius = max(3, int(np.sqrt(params.minArea / np.pi) * 0.7))
    max_radius = max(min_radius + 2, int(np.sqrt(params.maxArea / np.pi) * 1.35))
    circles = cv2.HoughCircles(
        processed,
        cv2.HOUGH_GRADIENT,
        dp=1.2,
        minDist=max(6, min_radius * 2),
        param1=80,
        param2=12,
        minRadius=min_radius,
        maxRadius=max_radius,
    )
    if circles is None:
        return []
    ox, oy = offset
    result = []
    for x, y, radius in np.round(circles[0, :]).astype("int"):
        area = float(np.pi * radius * radius)
        result.append(
            {
                "x": round(float(x + ox), 2),
                "y": round(float(y + oy), 2),
                "area": round(area, 2),
                "circularity": None,
                "roundness": None,
                "aspect": 1,
                "estimatedSeeds": 1,
                "review": area > params.maxArea,
                "source": "hough",
            }
        )
    return result


def merge_points(point_groups: list[list[dict]], params: SeedCandidateRequest) -> list[dict]:
    points = [point for group in point_groups for point in group]
    if not points:
        return []
    areas = [point.get("area") for point in points if point.get("area")]
    median_area = float(np.median(areas)) if areas else params.minArea * 4
    merge_distance = max(8, min(36, np.sqrt(max(median_area, params.minArea) / np.pi) * 1.45))
    merged = []
    for point in sorted(points, key=lambda item: (item["y"], item["x"])):
        best = None
        best_distance = None
        for item in merged:
            d = float(np.hypot(point["x"] - item["x"], point["y"] - item["y"]))
            if d <= merge_distance and (best_distance is None or d < best_distance):
                best = item
                best_distance = d
        if best is None:
            merged.append({**point, "votes": 1, "sources": [point.get("source", "contour")]})
        else:
            votes = best["votes"] + 1
            best["x"] = round((best["x"] * best["votes"] + point["x"]) / votes, 2)
            best["y"] = round((best["y"] * best["votes"] + point["y"]) / votes, 2)
            best["area"] = round(max(best.get("area") or 0, point.get("area") or 0), 2)
            best["votes"] = votes
            best["sources"].append(point.get("source", "contour"))
            best["review"] = best.get("review") or point.get("review")
    filtered = []
    for point in merged:
        sources = set(point.get("sources", []))
        has_contour = any(source.startswith("contour_") for source in sources)
        has_blob = any(source.startswith("blob_") for source in sources)
        has_hough = "hough" in sources
        if point["votes"] >= 2 or (has_contour and has_blob):
            filtered.append(point)
        elif has_hough and point.get("area", 0) <= params.maxArea and point["votes"] >= 2:
            filtered.append(point)
    return filtered


def auto_seed_candidates(gray: np.ndarray, params: SeedCandidateRequest, offset: tuple[int, int]):
    modes = ["dark", "light"] if params.foregroundMode == "auto" else [params.foregroundMode]
    mode_results = []
    for mode in modes:
        groups = []
        warnings = []
        medians = []
        mask = make_mask(gray, mode)
        contour_points, contour_warnings, median_area = contour_candidates(mask, params, offset)
        for point in contour_points:
            point["source"] = f"contour_{mode}"
        groups.append(contour_points)
        groups.append(blob_candidates(gray, params, offset, mode))
        warnings.extend(contour_warnings)
        if median_area:
            medians.append(median_area)
        groups.append(hough_candidates(gray, params, offset))
        points = merge_points(groups, params)
        score = score_candidate_set(points)
        mode_results.append((score, points, warnings, medians))
    if not mode_results:
        return [], [], None
    _, points, warnings, medians = sorted(mode_results, key=lambda item: item[0], reverse=True)[0]
    points.sort(key=lambda item: (item["y"], item["x"]))
    median_area = float(np.median(medians)) if medians else None
    return points, warnings, median_area


def score_candidate_set(points: list[dict]) -> float:
    if not points:
        return -1000
    count = len(points)
    votes = sum(point.get("votes", 1) for point in points)
    review = sum(1 for point in points if point.get("review"))
    count_penalty = max(0, count - 80) * 4
    review_ratio_penalty = (review / max(1, count)) * 30
    return votes * 2 + count * 0.4 - review * 8 - review_ratio_penalty - count_penalty


def latest_trained_model() -> Optional[Path]:
    env_path = os.environ.get("RAPESEED_SEED_MODEL")
    if env_path and Path(env_path).exists():
        return Path(env_path)
    candidates = sorted(RUNS_DIR.glob("*/train/seed_detector/weights/best.pt"), key=lambda path: path.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def yolo_seed_candidates(img: np.ndarray, params: SeedCandidateRequest, offset: tuple[int, int]):
    model_path = latest_trained_model()
    if not model_path or not params.useYolo:
        return None
    try:
        from ultralytics import YOLO

        model = YOLO(str(model_path))
        result = model.predict(img, imgsz=1024, conf=0.15, iou=0.45, verbose=False)[0]
        ox, oy = offset
        points = []
        for box in result.boxes:
            x1, y1, x2, y2 = [float(value) for value in box.xyxy[0].tolist()]
            area = max(1.0, (x2 - x1) * (y2 - y1))
            conf = float(box.conf[0])
            points.append(
                {
                    "x": round((x1 + x2) / 2 + ox, 2),
                    "y": round((y1 + y2) / 2 + oy, 2),
                    "area": round(area, 2),
                    "confidence": round(conf, 3),
                    "source": "trained_yolo",
                    "review": conf < 0.35,
                    "estimatedSeeds": 1,
                }
            )
        return points
    except Exception:
        return None


@app.get("/api/health")
def health():
    model_path = latest_trained_model()
    return {
        "ok": True,
        "engine": "opencv-multimethod-seed-candidates",
        "training": "local-yolo",
        "trainedModel": str(model_path) if model_path else None,
    }


@app.get("/api/silique-records")
def get_silique_records():
    return {"ok": True, "records": load_stored_records()}


@app.put("/api/silique-records")
def replace_silique_records(payload: StoredRecordsRequest):
    records = merge_records(load_stored_records(), payload.records)
    save_stored_records(records)
    return {"ok": True, "records": records, "count": len(records)}


@app.post("/api/silique-records")
def upsert_silique_records(payload: StoredRecordsRequest):
    records = merge_records(load_stored_records(), payload.records)
    save_stored_records(records)
    return {"ok": True, "records": records, "count": len(records)}


@app.delete("/api/silique-records/{record_id}")
def delete_silique_record(record_id: str):
    records = [record for record in load_stored_records() if str(record.get("id")) != record_id]
    save_stored_records(records)
    return {"ok": True, "records": records, "count": len(records)}


@app.delete("/api/silique-records")
def clear_silique_records():
    save_stored_records([])
    return {"ok": True, "records": [], "count": 0}


@app.post("/api/train-yolo-stored")
def train_yolo_stored(payload: TrainStoredRequest):
    records = []
    for item in load_stored_records():
        try:
            records.append(TrainingRecord(**item))
        except Exception:
            continue
    return train_yolo(
        TrainRequest(
            records=records,
            model=payload.model,
            epochs=payload.epochs,
            imgsz=payload.imgsz,
            batch=payload.batch,
            trainRatio=payload.trainRatio,
            dryRun=payload.dryRun,
        )
    )


@app.post("/api/train-yolo-cloud")
def train_yolo_cloud(payload: TrainCloudRequest):
    records = fetch_supabase_training_records(payload)
    return train_yolo(
        TrainRequest(
            records=records,
            model=payload.model,
            epochs=payload.epochs,
            imgsz=payload.imgsz,
            batch=payload.batch,
            trainRatio=payload.trainRatio,
            dryRun=payload.dryRun,
        )
    )


@app.post("/api/seed-candidates")
def seed_candidates(payload: SeedCandidateRequest):
    img = decode_data_url(payload.imageDataUrl)
    height, width = img.shape[:2]
    x0, y0, x1, y1 = clamp_roi(payload.roi, width, height)
    crop = img[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    yolo_points = yolo_seed_candidates(crop, payload, (x0, y0)) if payload.analysisEngine != "imagej" else None
    if payload.analysisEngine == "imagej":
        points, warnings, median_area = imagej_particle_candidates(crop, payload, (x0, y0))
        engine = "imagej-particle-analysis"
    elif yolo_points is not None and yolo_points:
        points = yolo_points
        warnings = []
        median_area = None
        engine = "trained-yolo-seed-detector"
    else:
        points, warnings, median_area = auto_seed_candidates(gray, payload, (x0, y0))
        engine = "opencv-multimethod-seed-candidates"
    points.sort(key=lambda item: (item["y"], item["x"]))
    confidence = "high"
    review_count = sum(1 for item in points if item.get("review"))
    if warnings or review_count:
        confidence = "medium"
    if len(points) == 0 or len(points) > 120:
        confidence = "low"
    return {
        "engine": engine,
        "count": len(points),
        "points": points,
        "confidence": confidence,
        "medianArea": round(median_area, 2) if median_area else None,
        "warnings": warnings,
        "reviewCount": review_count,
        "roi": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
    }


@app.post("/api/vivipary-candidates")
def detect_vivipary(payload: ViviparyRequest):
    img = decode_data_url(payload.imageDataUrl)
    height, width = img.shape[:2]
    x0, y0, x1, y1 = clamp_roi(payload.roi, width, height)
    points = vivipary_candidates(img[y0:y1, x0:x1], payload, (x0, y0))
    vivipary_count = sum(1 for point in points if point["vivipary"])
    dense_count = sum(1 for point in points if point.get("source") == "dense-seed-estimate")
    return {
        "engine": "imagej-vivipary-analysis",
        "count": len(points),
        "viviparyCount": vivipary_count,
        "viviparyRate": round(vivipary_count / len(points) * 100, 2) if points else 0,
        "points": points,
        "reviewCount": sum(1 for point in points if point["review"]),
        "denseEstimateCount": dense_count,
        "warnings": (["检测到密集或粘连种子；总数为中心峰估算值，遮挡区域不能可靠判断胎萌。"] if dense_count else []),
        "roi": {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0},
    }


@app.post("/api/train-yolo")
def train_yolo(payload: TrainRequest):
    usable = [record for record in payload.records if record.imageDataUrl and record.seedPoints and record.quality != "exclude"]
    if len(usable) < 2:
        return {
            "ok": False,
            "error": "至少需要 2 张已保存原图且有最终点位的记录，才能划分 train/val 并启动训练。",
        }

    job_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    job_dir = RUNS_DIR / job_id
    job = {
        "id": job_id,
        "status": "queued",
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "message": "训练任务已创建。",
        "datasetDir": str(job_dir / "dataset"),
        "runDir": str(job_dir / "train"),
        "trainImages": 0,
        "valImages": 0,
        "seedAnnotations": 0,
        "command": None,
        "logTail": "",
        "metrics": None,
    }
    with TRAIN_LOCK:
        TRAIN_JOBS[job_id] = job

    thread = threading.Thread(target=run_training_job, args=(job_id, payload), daemon=True)
    thread.start()
    return {"ok": True, "job": job}


@app.get("/api/train-yolo/{job_id}")
def train_status(job_id: str):
    with TRAIN_LOCK:
        job = TRAIN_JOBS.get(job_id)
    if not job:
        return {"ok": False, "error": "训练任务不存在。"}
    return {"ok": True, "job": job}


def run_training_job(job_id: str, payload: TrainRequest):
    try:
        update_job(job_id, status="preparing", message="正在生成 YOLO 数据集。")
        job_dir = RUNS_DIR / job_id
        dataset_dir = job_dir / "dataset"
        train_count, val_count, seed_count = write_yolo_dataset(payload.records, dataset_dir, payload.trainRatio)
        update_job(
            job_id,
            status="ready" if payload.dryRun else "training",
            message="数据集已生成。" if payload.dryRun else "数据集已生成，正在训练模型。",
            trainImages=train_count,
            valImages=val_count,
            seedAnnotations=seed_count,
        )
        if payload.dryRun:
            update_job(job_id, status="completed", message="试运行完成，只生成数据集，未启动训练。")
            return
        command = [
            sys.executable,
            "-c",
            (
                "from ultralytics import YOLO; "
                "import sys; "
                "model=YOLO(sys.argv[1]); "
                "model.train(data=sys.argv[2], imgsz=int(sys.argv[3]), epochs=int(sys.argv[4]), "
                "batch=int(sys.argv[5]), project=sys.argv[6], name='seed_detector', exist_ok=True)"
            ),
            payload.model,
            str(dataset_dir / "data.yaml"),
            str(payload.imgsz),
            str(payload.epochs),
            str(payload.batch),
            str(job_dir / "train"),
        ]
        update_job(job_id, command=" ".join(command))
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=str(BASE_DIR))
        tail = []
        assert process.stdout is not None
        for line in process.stdout:
            tail.append(line.rstrip())
            tail = tail[-40:]
            update_job(job_id, logTail="\n".join(tail), message=line.rstrip()[:240] or "训练中。")
        code = process.wait()
        if code != 0:
            update_job(job_id, status="failed", message=f"训练失败，退出码 {code}。", logTail="\n".join(tail))
            return
        metrics = collect_training_metrics(job_dir / "train" / "seed_detector")
        update_job(job_id, status="completed", message="训练完成。", metrics=metrics, logTail="\n".join(tail))
    except Exception as exc:
        update_job(job_id, status="failed", message=str(exc))


def update_job(job_id: str, **updates):
    with TRAIN_LOCK:
        job = TRAIN_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updatedAt"] = datetime.now().isoformat(timespec="seconds")


def write_yolo_dataset(records: list[TrainingRecord], dataset_dir: Path, train_ratio: float) -> tuple[int, int, int]:
    if dataset_dir.exists():
        shutil.rmtree(dataset_dir)
    (dataset_dir / "images" / "train").mkdir(parents=True, exist_ok=True)
    (dataset_dir / "images" / "val").mkdir(parents=True, exist_ok=True)
    (dataset_dir / "labels" / "train").mkdir(parents=True, exist_ok=True)
    (dataset_dir / "labels" / "val").mkdir(parents=True, exist_ok=True)
    usable = [record for record in records if record.imageDataUrl and record.seedPoints and record.quality != "exclude"]
    usable.sort(key=lambda record: record.id)
    val_start = max(1, int(len(usable) * train_ratio))
    seed_count = 0
    for index, record in enumerate(usable):
        split = "val" if index >= val_start else "train"
        base_name = safe_name(f"{record.sampleId or 'sample'}_{record.siliqueId or record.id}")
        image_bytes, ext = decode_data_url_bytes(record.imageDataUrl)
        (dataset_dir / "images" / split / f"{base_name}.{ext}").write_bytes(image_bytes)
        label_text = yolo_label_text(record)
        (dataset_dir / "labels" / split / f"{base_name}.txt").write_text(label_text, encoding="utf-8")
        seed_count += len(record.seedPoints)
    data_yaml = "\n".join(["path: " + str(dataset_dir), "train: images/train", "val: images/val", "names:", "  0: rapeseed_seed", ""])
    (dataset_dir / "data.yaml").write_text(data_yaml, encoding="utf-8")
    metadata = {
        "schema": "rapeseed-seed-detection-yolo-v1",
        "createdAt": datetime.now().isoformat(timespec="seconds"),
        "trainImages": min(val_start, len(usable)),
        "valImages": max(0, len(usable) - val_start),
        "seedAnnotations": seed_count,
        "boxSource": "seed center points converted to fixed-size YOLO boxes",
    }
    (dataset_dir / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata["trainImages"], metadata["valImages"], seed_count


def yolo_label_text(record: TrainingRecord) -> str:
    box_px = estimate_box_size(record)
    lines = []
    for point in record.seedPoints:
        cx = clamp(point.x / record.imageWidth)
        cy = clamp(point.y / record.imageHeight)
        bw = clamp(box_px / record.imageWidth)
        bh = clamp(box_px / record.imageHeight)
        lines.append(f"0 {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")
    return "\n".join(lines)


def estimate_box_size(record: TrainingRecord) -> float:
    areas = [point.area for point in [*record.seedPoints, *record.autoSeedPoints] if point.area]
    if areas:
        median = float(np.median(areas))
        return max(10, min(80, np.sqrt(median) * 1.7))
    return max(12, min(40, min(record.imageWidth, record.imageHeight) * 0.025))


def collect_training_metrics(run_dir: Path) -> dict:
    weights = run_dir / "weights" / "best.pt"
    results_csv = run_dir / "results.csv"
    metrics = {"bestWeights": str(weights) if weights.exists() else None, "resultsCsv": str(results_csv) if results_csv.exists() else None}
    if results_csv.exists():
        lines = results_csv.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
        if len(lines) >= 2:
            headers = [item.strip() for item in lines[0].split(",")]
            values = [item.strip() for item in lines[-1].split(",")]
            metrics["lastEpoch"] = dict(zip(headers, values))
    return metrics


def load_stored_records() -> list[dict]:
    with RECORDS_LOCK:
        if not SILIQUE_RECORDS_PATH.exists():
            return []
        try:
            data = json.loads(SILIQUE_RECORDS_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            return data
        except Exception:
            return []


def save_stored_records(records: list[dict]):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with RECORDS_LOCK:
        SILIQUE_RECORDS_PATH.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")


def merge_records(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged = {str(record.get("id")): record for record in existing if record.get("id")}
    for record in incoming:
        record_id = str(record.get("id") or uuid.uuid4())
        merged[record_id] = {**record, "id": record_id, "syncedAt": datetime.now().isoformat(timespec="seconds")}
    return sorted(
        merged.values(),
        key=lambda record: str(record.get("createdAt") or record.get("measuredAt") or record.get("syncedAt") or ""),
        reverse=True,
    )


def fetch_supabase_training_records(payload: TrainCloudRequest) -> list[TrainingRecord]:
    url = normalize_supabase_url(payload.supabaseUrl)
    query = (
        f"{url}/rest/v1/silique_records"
        f"?select=*&user_id=eq.{urllib.parse.quote(payload.userId)}&order=created_at.desc"
    )
    request = urllib.request.Request(
        query,
        headers={
            "apikey": payload.anonKey,
            "Authorization": f"Bearer {payload.accessToken}",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            rows = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"云端数据读取失败：{message}") from exc

    records = []
    for row in rows:
        if row.get("quality") == "exclude":
            continue
        points = row.get("seed_points_json") or []
        image_url = row.get("seed_image_url") or row.get("cloud_url")
        if not points or not image_url:
            continue
        try:
            image_data_url, width, height = download_image_as_data_url(image_url, payload.accessToken)
            records.append(
                TrainingRecord(
                    id=str(row.get("id") or uuid.uuid4()),
                    imageDataUrl=image_data_url,
                    imageWidth=width,
                    imageHeight=height,
                    sampleId=row.get("sample_id") or "",
                    siliqueId=row.get("silique_id") or "",
                    quality=row.get("quality") or "good",
                    seedPoints=[SeedPoint(**point) for point in points if "x" in point and "y" in point],
                    autoSeedPoints=[SeedPoint(**point) for point in (row.get("auto_seed_points_json") or []) if "x" in point and "y" in point],
                )
            )
        except Exception:
            continue
    return records


def download_image_as_data_url(url: str, access_token: str) -> tuple[str, int, int]:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type") or "image/jpeg"
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Unable to decode cloud image")
    height, width = img.shape[:2]
    data_url = f"data:{content_type};base64,{base64.b64encode(raw).decode('ascii')}"
    return data_url, width, height


def normalize_supabase_url(url: str) -> str:
    return re.sub(r"/rest/v1/?$", "", str(url or "").rstrip("/"))


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return cleaned or "image"


def clamp(value: float) -> float:
    return max(0, min(1, value))
