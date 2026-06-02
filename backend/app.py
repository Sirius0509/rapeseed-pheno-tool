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
    foregroundMode: Literal["light", "dark"] = "light"
    minArea: float = Field(default=12, ge=1)
    maxArea: float = Field(default=1800, ge=2)
    minCircularity: float = Field(default=0.18, ge=0, le=1)
    minRoundness: float = Field(default=0.12, ge=0, le=1)
    useWatershed: bool = True
    edgeMarginRatio: float = Field(default=0.03, ge=0, le=0.3)


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
    return {"ok": True, "engine": "opencv-watershed-seed-candidates", "training": "local-yolo"}


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
