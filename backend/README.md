# Seed Candidate Backend

This optional backend provides stronger seed candidate detection than the in-browser threshold tool and can launch local YOLO training jobs.
It first tries the latest trained YOLO weights under `runs/*/train/seed_detector/weights/best.pt`. If no trained model is available, it falls back to OpenCV multi-method detection: illumination correction, auto dark/light thresholding, morphology, watershed splitting, SimpleBlobDetector, Hough circles, and point merging.

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

API:

```text
POST /api/seed-candidates
POST /api/train-yolo
GET /api/train-yolo/{job_id}
```

The frontend sends a canvas snapshot and optional seed ROI. The service returns candidate points in the same coordinate space as the submitted image, plus confidence, median seed area, review count, and touching-seed warnings.

For training, the frontend sends saved records with original image data and corrected seed points. The backend writes a YOLO dataset under `backend/runs/{job_id}/dataset`, starts ultralytics training, and stores outputs under `backend/runs/{job_id}/train`.

For GitHub Pages production use, deploy this backend behind HTTPS and set the frontend "识别服务地址" to that URL.
