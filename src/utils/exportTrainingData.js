function downloadJson(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = filename
  link.click()
  URL.revokeObjectURL(link.href)
}

export function exportPlantTrainingData(samples) {
  const dataset = {
    schema: 'rapeseed-plant-phenotype-regression-v1',
    exportedAt: new Date().toISOString(),
    recommendedModels: [
      {
        task: 'plant_keypoints',
        model: 'YOLO-pose or HRNet',
        purpose: 'Detect scale endpoints, plant base, top, inflorescence start/top, and branch keypoints.',
      },
      {
        task: 'phenotype_regression',
        model: 'Geometry from detected keypoints',
        purpose: 'Compute phenotype values from predicted keypoints instead of directly regressing numbers.',
      },
    ],
    records: samples.map((sample) => ({
      id: sample.id,
      image: {
        fileName: sample.imageName || '',
        url: sample.cloudUrl || '',
      },
      metadata: {
        sampleId: sample.sampleId,
        genotype: sample.genotype,
        treatment: sample.treatment,
        replicate: sample.replicate,
        notes: sample.notes,
        measuredAt: sample.measuredAt,
      },
      labels: {
        plantHeightCm: sample.metrics.plantHeightCm,
        inflorescenceHeightCm: sample.metrics.inflorescenceHeightCm,
        inflorescenceLengthCm: sample.metrics.inflorescenceLengthCm,
        branchCount: sample.metrics.branchCount,
        branchAngleAvg: sample.metrics.branchAngleAvg,
        branchAngleMax: sample.metrics.branchAngleMax,
        branchAngleMin: sample.metrics.branchAngleMin,
      },
    })),
  }
  downloadJson(dataset, `rapeseed-plant-training-${new Date().toISOString().slice(0, 10)}.json`)
}

export function exportSiliqueTrainingData(records) {
  const dataset = {
    schema: 'rapeseed-silique-seed-counting-v1',
    exportedAt: new Date().toISOString(),
    recommendedModels: [
      {
        task: 'silique_length',
        model: 'OpenCV segmentation first, YOLO-seg or Mask R-CNN if backgrounds vary',
        purpose: 'Segment the silique and compute length using calibration.',
      },
      {
        task: 'seed_counting',
        model: 'YOLOv8/YOLO11 detection or Cellpose/StarDist-style instance segmentation',
        purpose: 'Detect or segment individual seeds. Use a separate model from whole-plant keypoints.',
      },
    ],
    records: records.map((record) => ({
      id: record.id,
      image: {
        fileName: record.imageName,
        url: record.cloudUrl,
      },
      metadata: {
        genotype: record.genotype,
        sampleId: record.sampleId,
        replicate: record.replicate,
        siliqueId: record.siliqueId,
        notes: record.notes,
        measuredAt: record.measuredAt,
        annotationMethod: record.method,
      },
      labels: {
        siliqueLengthMm: record.siliqueLengthMm,
        seedCount: record.seedCount,
        seedsPerCm: record.seedsPerCm,
      },
    })),
  }
  downloadJson(dataset, `rapeseed-silique-training-${new Date().toISOString().slice(0, 10)}.json`)
}
