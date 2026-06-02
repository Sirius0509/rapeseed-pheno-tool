# Seed Candidate Backend

This optional backend provides stronger seed candidate detection than the in-browser threshold tool.
It uses OpenCV illumination correction, Otsu/adaptive thresholding, morphology, distance transform, and watershed splitting for touching seeds.

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
```

The frontend sends a canvas snapshot and optional seed ROI. The service returns candidate points in the same coordinate space as the submitted image, plus confidence, median seed area, review count, and touching-seed warnings.

For GitHub Pages production use, deploy this backend behind HTTPS and set the frontend "识别服务地址" to that URL.
