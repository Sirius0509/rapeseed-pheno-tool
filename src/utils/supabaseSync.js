const SETTINGS_KEY = 'rapeseed-pheno-tool:supabase-settings'
const DEFAULT_BUCKET = 'rapeseed-images'

export function loadSupabaseSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || '{}')
    return {
      url: normalizeUrl(saved.url || ''),
      anonKey: saved.anonKey || '',
      bucket: saved.bucket || DEFAULT_BUCKET,
      enabled: Boolean(saved.enabled),
    }
  } catch {
    return { url: '', anonKey: '', bucket: DEFAULT_BUCKET, enabled: false }
  }
}

export function saveSupabaseSettings(settings) {
  localStorage.setItem(
    SETTINGS_KEY,
    JSON.stringify({
      url: normalizeUrl(settings.url || ''),
      anonKey: settings.anonKey || '',
      bucket: settings.bucket || DEFAULT_BUCKET,
      enabled: Boolean(settings.enabled),
    }),
  )
}

export async function testSupabaseConnection(settings) {
  const response = await supabaseFetch(settings, '/rest/v1/silique_records?select=id&limit=1')
  if (!response.ok) throw new Error(await response.text())
  return true
}

export async function fetchCloudSiliqueRecords(settings) {
  const response = await supabaseFetch(settings, '/rest/v1/silique_records?select=*&order=created_at.desc')
  if (!response.ok) throw new Error(await response.text())
  const rows = await response.json()
  return rows.map(rowToRecord)
}

export async function upsertCloudSiliqueRecord(settings, record) {
  const cloudRecord = await withCloudImages(settings, record)
  const response = await supabaseFetch(settings, '/rest/v1/silique_records?on_conflict=id', {
    method: 'POST',
    headers: {
      Prefer: 'resolution=merge-duplicates,return=representation',
    },
    body: JSON.stringify([recordToRow(cloudRecord)]),
  })
  if (!response.ok) throw new Error(await response.text())
  const rows = await response.json()
  const saved = rows[0] ? rowToRecord(rows[0]) : cloudRecord
  if (cloudRecord.cloudUploadError) saved.cloudUploadError = cloudRecord.cloudUploadError
  return saved
}

export async function upsertCloudSiliqueRecords(settings, records) {
  const synced = []
  for (const record of records) synced.push(await upsertCloudSiliqueRecord(settings, record))
  return synced
}

export async function deleteCloudSiliqueRecord(settings, recordId) {
  const response = await supabaseFetch(settings, `/rest/v1/silique_records?id=eq.${encodeURIComponent(recordId)}`, {
    method: 'DELETE',
  })
  if (!response.ok) throw new Error(await response.text())
}

async function withCloudImages(settings, record) {
  const result = { ...record }
  if (record.siliqueImageDataUrl && !record.siliqueImageUrl) {
    try {
      result.siliqueImageUrl = await uploadDataUrl(settings, record.siliqueImageDataUrl, imagePath(record, 'silique'))
    } catch (error) {
      result.cloudUploadError = error.message
    }
  }
  if ((record.seedImageDataUrl || record.imageDataUrl) && !record.seedImageUrl) {
    try {
      result.seedImageUrl = await uploadDataUrl(settings, record.seedImageDataUrl || record.imageDataUrl, imagePath(record, 'seed'))
    } catch (error) {
      result.cloudUploadError = error.message
    }
  }
  result.cloudUrl = result.seedImageUrl || result.siliqueImageUrl || result.cloudUrl || ''
  return result
}

async function uploadDataUrl(settings, dataUrl, path) {
  const blob = dataUrlToBlob(dataUrl)
  const response = await supabaseFetch(settings, `/storage/v1/object/${encodeURIComponent(settings.bucket || DEFAULT_BUCKET)}/${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': blob.type || 'image/jpeg',
      'x-upsert': 'true',
    },
    body: blob,
  })
  if (!response.ok) throw new Error(await response.text())
  return `${normalizeUrl(settings.url)}/storage/v1/object/public/${encodeURIComponent(settings.bucket || DEFAULT_BUCKET)}/${path}`
}

function recordToRow(record) {
  return {
    id: record.id,
    sample_id: record.sampleId || '',
    genotype: record.genotype || '',
    replicate: record.replicate || '',
    silique_id: record.siliqueId || '',
    silique_length_mm: numberOrNull(record.siliqueLengthMm),
    seed_count: numberOrNull(record.seedCount),
    seeds_per_cm: numberOrNull(record.seedsPerCm),
    quality: record.quality || 'good',
    notes: record.notes || '',
    seed_points_json: record.seedPoints || [],
    auto_seed_points_json: record.autoSeedPoints || [],
    deleted_seed_points_json: record.deletedSeedPoints || [],
    seed_roi_json: record.seedRoi || null,
    silique_scale_json: record.siliqueScale || null,
    silique_line_json: record.siliqueLine || null,
    silique_image_url: record.siliqueImageUrl || '',
    seed_image_url: record.seedImageUrl || record.cloudUrl || '',
    cloud_url: record.cloudUrl || record.seedImageUrl || record.siliqueImageUrl || '',
    method: record.method || '',
    measured_at: record.measuredAt || new Date().toISOString().slice(0, 10),
    updated_at: new Date().toISOString(),
  }
}

function rowToRecord(row) {
  return {
    id: row.id,
    genotype: row.genotype || '',
    sampleId: row.sample_id || '',
    replicate: row.replicate || '',
    siliqueId: row.silique_id || '',
    quality: row.quality || 'good',
    siliqueLengthMm: row.silique_length_mm ?? '',
    seedCount: row.seed_count ?? '',
    seedsPerCm: row.seeds_per_cm ?? '',
    method: row.method || '云端记录',
    cloudUrl: row.cloud_url || row.seed_image_url || row.silique_image_url || '',
    notes: row.notes || '',
    seedRoi: row.seed_roi_json || null,
    seedPoints: row.seed_points_json || [],
    autoSeedPoints: row.auto_seed_points_json || [],
    deletedSeedPoints: row.deleted_seed_points_json || [],
    siliqueScale: row.silique_scale_json || null,
    siliqueLine: row.silique_line_json || null,
    siliqueImageUrl: row.silique_image_url || '',
    seedImageUrl: row.seed_image_url || '',
    measuredAt: row.measured_at || '',
    createdAt: row.created_at || row.updated_at || '',
    editedAt: row.updated_at || '',
  }
}

function supabaseFetch(settings, path, options = {}) {
  const url = normalizeUrl(settings.url)
  if (!url || !settings.anonKey) throw new Error('请先填写 Supabase URL 和 anon key。')
  return fetch(`${url}${path}`, {
    ...options,
    headers: {
      apikey: settings.anonKey,
      Authorization: `Bearer ${settings.anonKey}`,
      ...(options.body && !(options.body instanceof Blob) ? { 'Content-Type': 'application/json' } : {}),
      ...(options.headers || {}),
    },
  })
}

function normalizeUrl(url) {
  return String(url || '').replace(/\/rest\/v1\/?$/, '').replace(/\/$/, '')
}

function imagePath(record, kind) {
  const ext = imageExtension(kind === 'silique' ? record.siliqueImageDataUrl : record.seedImageDataUrl || record.imageDataUrl)
  const sample = safeName(record.sampleId || 'sample')
  const silique = safeName(record.siliqueId || record.id)
  return `${sample}/${silique}/${kind}-${record.id}.${ext}`
}

function dataUrlToBlob(dataUrl) {
  const [header, body] = dataUrl.split(',')
  const mime = header.match(/data:([^;]+)/)?.[1] || 'image/jpeg'
  const binary = atob(body || '')
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i)
  return new Blob([bytes], { type: mime })
}

function imageExtension(dataUrl = '') {
  if (dataUrl.startsWith('data:image/png')) return 'png'
  if (dataUrl.startsWith('data:image/webp')) return 'webp'
  return 'jpg'
}

function safeName(value) {
  return String(value).replace(/[^a-zA-Z0-9_-]+/g, '_').replace(/^_+|_+$/g, '') || 'item'
}

function numberOrNull(value) {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}
