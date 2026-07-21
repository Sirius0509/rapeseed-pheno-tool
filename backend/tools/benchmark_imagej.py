#!/usr/bin/env python3
import argparse
import csv
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import SeedCandidateRequest, imagej_particle_candidates  # noqa: E402


def detect_saved_roi(image: np.ndarray) -> tuple[int, int, int, int]:
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    purple = cv2.inRange(hsv, np.array([130, 90, 70]), np.array([170, 255, 255]))
    ys, xs = np.where(purple > 0)
    height, width = image.shape[:2]
    if len(xs) < 12:
        return int(width * .03), int(height * .18), int(width * .97), int(height * .78)
    return (
        max(0, int(np.percentile(xs, 2)) + 5),
        max(0, int(np.percentile(ys, 2)) + 5),
        min(width, int(np.percentile(xs, 98)) - 5),
        min(height, int(np.percentile(ys, 98)) - 5),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    params = SeedCandidateRequest(
        imageDataUrl="unused", foregroundMode="dark", threshold=73,
        minArea=8, maxArea=150, minCircularity=.1, minRoundness=.25,
        maxAspect=3.2, edgeMarginRatio=.03, touchingAreaMultiplier=4,
        useWatershed=True, useYolo=False, analysisEngine="imagej",
    )
    rows = []
    for split in ("train", "val"):
        for image_path in sorted((args.dataset / "images" / split).glob("*")):
            if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png"}:
                continue
            label_path = args.dataset / "labels" / split / f"{image_path.stem}.txt"
            truth = len([line for line in label_path.read_text().splitlines() if line.strip()])
            image = cv2.imread(str(image_path))
            x0, y0, x1, y1 = detect_saved_roi(image)
            points, warnings, _ = imagej_particle_candidates(image[y0:y1, x0:x1], params, (x0, y0))
            predicted = len(points)
            rows.append({
                "split": split, "image": image_path.name, "manual_count": truth,
                "imagej_count": predicted, "difference": predicted - truth,
                "absolute_error": abs(predicted - truth), "review_regions": len(warnings),
            })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)
    mae = sum(row["absolute_error"] for row in rows) / len(rows)
    exact = sum(row["absolute_error"] == 0 for row in rows)
    within_one = sum(row["absolute_error"] <= 1 for row in rows)
    print(f"images={len(rows)} truth={sum(r['manual_count'] for r in rows)} imagej={sum(r['imagej_count'] for r in rows)}")
    print(f"mae={mae:.2f} exact={exact}/{len(rows)} within_one={within_one}/{len(rows)}")


if __name__ == "__main__":
    main()
