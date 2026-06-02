import { strToU8, zipSync } from 'fflate'

function downloadJson(data, filename) {
  const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
  const link = document.createElement('a')
  link.href = URL.createObjectURL(blob)
  link.download = filename
  link.click()
  URL.revokeObjectURL(link.href)
}

function downloadBlob(blob, filename) {
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
        seedPoints: record.seedPoints || [],
        autoSeedPoints: record.autoSeedPoints || [],
        deletedSeedPoints: record.deletedSeedPoints || [],
      },
      training: {
        quality: record.quality || 'unlabeled',
        imageWidth: record.imageWidth || null,
        imageHeight: record.imageHeight || null,
        hasImageData: Boolean(record.imageDataUrl),
      },
    })),
  }
  downloadJson(dataset, `rapeseed-silique-training-${new Date().toISOString().slice(0, 10)}.json`)
}

export function exportSiliqueYoloDataset(records) {
  const usable = records.filter((record) => record.imageDataUrl && (record.seedPoints || []).length && record.quality !== 'exclude')
  const datasetVersion = `dataset_${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}`
  const files = {}
  const shuffled = [...usable].sort((a, b) => String(a.id).localeCompare(String(b.id)))
  const valStart = Math.max(1, Math.floor(shuffled.length * 0.8))

  shuffled.forEach((record, index) => {
    const split = index >= valStart ? 'val' : 'train'
    const baseName = safeName(`${record.sampleId || 'sample'}_${record.siliqueId || record.id}`)
    const imageExt = imageExtension(record.imageDataUrl)
    const imagePath = `${datasetVersion}/images/${split}/${baseName}.${imageExt}`
    const labelPath = `${datasetVersion}/labels/${split}/${baseName}.txt`
    files[imagePath] = base64ToU8(record.imageDataUrl)
    files[labelPath] = strToU8(yoloLabels(record))
  })

  files[`${datasetVersion}/data.yaml`] = strToU8(
    [
      `path: ./${datasetVersion}`,
      'train: images/train',
      'val: images/val',
      'names:',
      '  0: rapeseed_seed',
      '',
    ].join('\n'),
  )
  files[`${datasetVersion}/metadata.json`] = strToU8(
    JSON.stringify(
      {
        schema: 'rapeseed-seed-detection-yolo-v1',
        datasetVersion,
        exportedAt: new Date().toISOString(),
        trainImages: Math.min(valStart, shuffled.length),
        valImages: Math.max(0, shuffled.length - valStart),
        seedAnnotations: usable.reduce((sum, record) => sum + (record.seedPoints?.length || 0), 0),
        boxSource: 'seed center points converted to fixed-size YOLO boxes',
        recommendedModels: ['YOLOv8n', 'YOLOv8s', 'YOLO11n', 'YOLO11s'],
      },
      null,
      2,
    ),
  )

  const zipped = zipSync(files)
  downloadBlob(new Blob([zipped], { type: 'application/zip' }), `${datasetVersion}.zip`)
}

function yoloLabels(record) {
  const width = record.imageWidth || 1
  const height = record.imageHeight || 1
  const boxPx = estimateBoxSize(record)
  return (record.seedPoints || [])
    .map((point) => {
      const cx = clamp(point.x / width)
      const cy = clamp(point.y / height)
      const bw = clamp(boxPx / width)
      const bh = clamp(boxPx / height)
      return `0 ${cx.toFixed(6)} ${cy.toFixed(6)} ${bw.toFixed(6)} ${bh.toFixed(6)}`
    })
    .join('\n')
}

function estimateBoxSize(record) {
  const areas = [...(record.seedPoints || []), ...(record.autoSeedPoints || [])].map((point) => point.area).filter(Boolean)
  if (areas.length) {
    const sorted = areas.sort((a, b) => a - b)
    const median = sorted[Math.floor(sorted.length / 2)]
    return Math.max(10, Math.min(80, Math.sqrt(median) * 1.7))
  }
  const shortSide = Math.min(record.imageWidth || 1200, record.imageHeight || 1200)
  return Math.max(12, Math.min(40, shortSide * 0.025))
}

function base64ToU8(dataUrl) {
  const base64 = dataUrl.split(',')[1] || ''
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return bytes
}

function imageExtension(dataUrl) {
  if (dataUrl.startsWith('data:image/png')) return 'png'
  if (dataUrl.startsWith('data:image/webp')) return 'webp'
  return 'jpg'
}

function safeName(value) {
  return String(value).replace(/[^a-zA-Z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'image'
}

function clamp(value) {
  return Math.max(0, Math.min(1, value))
}
