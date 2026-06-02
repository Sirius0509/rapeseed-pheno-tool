import base64
import json
import os
import shutil
import subprocess
import sys
import threading
import uuid
import re
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
TRAIN_JOBS = {}
TRAIN_LOCK = threading.Lock()

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
    useWatershed: bool = True
    edgeMarginRatio: float = Field(default=0.03, ge=0, le=0.3)
    useYolo: bool = True


class SeedPoint(BaseModel):
    x: float
    y: float
    area: Optional[float] = None
    source: Optional[str] = None
    review: Optional[bool] = None


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


@app.post("/api/seed-candidates")
def seed_candidates(payload: SeedCandidateRequest):
    img = decode_data_url(payload.imageDataUrl)
    height, width = img.shape[:2]
    x0, y0, x1, y1 = clamp_roi(payload.roi, width, height)
    crop = img[y0:y1, x0:x1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    yolo_points = yolo_seed_candidates(crop, payload, (x0, y0))
    if yolo_points is not None and yolo_points:
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


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value).strip("_")
    return cleaned or "image"


def clamp(value: float) -> float:
    return max(0, min(1, value))
