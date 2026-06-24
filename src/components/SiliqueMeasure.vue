<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import {
  Camera,
  Download,
  ImagePlus,
  MinusCircle,
  Play,
  PlusCircle,
  Ruler,
  Save,
  Square,
  Trash2,
  UploadCloud,
  Video,
} from '@lucide/vue'
import { distance, round } from '../utils/geometry'
import { createId } from '../utils/id'
import { exportSiliqueRecords } from '../utils/exportExcel'
import { exportSiliqueTrainingData, exportSiliqueYoloDataset } from '../utils/exportTrainingData'
import { deleteSiliqueRecordIndexed, loadSiliqueRecords, loadSiliqueRecordsFull, saveSiliqueRecords } from '../utils/storage'
import {
  deleteCloudSiliqueRecord,
  fetchCloudSiliqueRecords,
  loadSupabaseSession,
  loadSupabaseSettings,
  refreshSupabaseSession,
  saveSupabaseSettings,
  signInSupabase,
  signOutSupabase,
  signUpSupabase,
  testSupabaseConnection,
  upsertCloudSiliqueRecord,
  upsertCloudSiliqueRecords,
} from '../utils/supabaseSync'

const canvas = ref(null)
const video = ref(null)
const image = ref(null)
const imageName = ref('')
const cameraOn = ref(false)
const stream = ref(null)
const mode = ref('scale')
const pendingPoints = ref([])
const records = ref(loadSiliqueRecords())
const editingRecordId = ref(null)
const sampleTemplate = ref(localStorage.getItem('rapeseed-pheno-tool:silique-sample-template') || 'S001')
const showRecordPanel = ref(true)
const showDetectPanel = ref(false)
const showTrainingPanel = ref(false)
const showResultsPanel = ref(false)
const showCloudPanel = ref(false)
const supabaseSettings = reactive(loadSupabaseSettings())
const supabaseSession = ref(loadSupabaseSession())
const authForm = reactive({
  email: '',
  password: '',
})
const cloudStatus = ref('填写 Supabase URL 和 anon key 后，可把手机和电脑数据同步到云端。')
const cloudSyncing = ref(false)
const editDraft = reactive({
  genotype: '',
  sampleId: '',
  replicate: '',
  siliqueId: '',
  siliqueLengthMm: '',
  seedCount: '',
  quality: 'good',
  notes: '',
})
const form = reactive({
  genotype: '',
  sampleId: '',
  replicate: '',
  siliqueId: 'S001',
  quality: 'good',
  notes: '',
  cloudUrl: '',
})
const scaleLengthMm = ref(10)
const savedDetectParams = loadDetectParams()
const threshold = ref(savedDetectParams.threshold)
const minSeedArea = ref(savedDetectParams.minSeedArea)
const maxSeedArea = ref(savedDetectParams.maxSeedArea)
const minRoundness = ref(savedDetectParams.minRoundness)
const minCircularity = ref(savedDetectParams.minCircularity)
const maxAspect = ref(savedDetectParams.maxAspect)
const edgeMarginRatio = ref(savedDetectParams.edgeMarginRatio)
const touchingAreaMultiplier = ref(savedDetectParams.touchingAreaMultiplier)
const foregroundMode = ref(savedDetectParams.foregroundMode)
const serviceUrl = ref(localStorage.getItem('rapeseed-pheno-tool:seed-service-url') || '')
const detectStatus = ref('先框选籽粒区域，再生成候选点。')
const detecting = ref(false)
const training = ref(false)
const savingRecord = ref(false)
const saveStatus = ref('填写或确认长度和籽粒数后，点击“保存并上传”生成结果记录。')
const trainStatus = ref('本地训练需要先运行后端服务，并填写识别服务地址。')
const trainJob = ref(null)
const syncStatus = ref('本地保存已启用。填写电脑后端地址后，手机和电脑可同步记录。')
const syncing = ref(false)
let trainPollTimer = null
const state = reactive({
  mmPerPixel: null,
  scale: null,
  siliqueLine: null,
  seedRoi: null,
  seedPoints: [],
  detectedSeeds: [],
  deletedSeeds: [],
  confirmedSiliqueLengthMm: null,
  confirmedSeedCount: null,
  confirmedSeedsPerCm: null,
  confirmedSiliquePhoto: null,
  manualSiliqueLengthMm: '',
  manualSeedCount: '',
  previewMask: false,
})

watch([threshold, minSeedArea, maxSeedArea, minRoundness, minCircularity, maxAspect, edgeMarginRatio, touchingAreaMultiplier, foregroundMode], saveDetectParams)
watch(supabaseSettings, () => saveSupabaseSettings(supabaseSettings))

onMounted(async () => {
  const fullRecords = await loadSiliqueRecordsFull()
  if (fullRecords.length > records.value.length) records.value = fullRecords
})

const metrics = computed(() => {
  const lengthPx = state.siliqueLine?.pixels || 0
  const siliqueLengthMm = state.mmPerPixel && lengthPx ? round(lengthPx * state.mmPerPixel, 2) : ''
  const seedCount = state.seedPoints.length
  const seedsPerCm = siliqueLengthMm && seedCount ? round(seedCount / (siliqueLengthMm / 10), 2) : ''
  return {
    mmPerPixel: round(state.mmPerPixel, 5),
    siliqueLengthMm,
    seedCount,
    seedsPerCm,
  }
})

const confirmedMetrics = computed(() => {
  const siliqueLengthMm = numberOrEmpty(state.manualSiliqueLengthMm)
  const seedCount = numberOrEmpty(state.manualSeedCount)
  return {
    siliqueLengthMm,
    seedCount,
    seedsPerCm: siliqueLengthMm && seedCount !== '' ? round(seedCount / (siliqueLengthMm / 10), 2) : '',
  }
})

const sampleOptions = computed(() => buildSampleOptions(sampleTemplate.value, 120))
const replicateOptions = computed(() => Array.from({ length: 10 }, (_, index) => String(index)))

const trainingStats = computed(() => {
  const corrected = records.value.filter((record) => (record.seedPoints || []).length)
  const yoloReady = records.value.filter((record) => record.imageDataUrl && (record.seedPoints || []).length && record.quality !== 'exclude')
  const seedAnnotations = corrected.reduce((sum, record) => sum + (record.seedPoints?.length || 0), 0)
  const lowQuality = records.value.filter((record) => ['blurry', 'reflective', 'overlapping', 'edge_interference', 'exclude'].includes(record.quality)).length
  return {
    correctedImages: corrected.length,
    seedAnnotations,
    averageSeeds: corrected.length ? round(seedAnnotations / corrected.length, 1) : 0,
    lowQuality,
    yoloReady: yoloReady.length,
  }
})
const currentCloudUser = computed(() => supabaseSession.value?.user?.email || supabaseSession.value?.user?.id || '')

function applySampleTemplate() {
  localStorage.setItem('rapeseed-pheno-tool:silique-sample-template', sampleTemplate.value.trim())
  const options = sampleOptions.value
  if (options.length) form.sampleId = options[0]
}

function buildSampleOptions(template, count) {
  const value = String(template || '').trim()
  if (!value) return []
  const match = value.match(/^(.*?)(\d+)(\D*)$/)
  if (!match) return [value]
  const [, prefix, numberPart, suffix] = match
  const start = Number(numberPart)
  const width = numberPart.length
  return Array.from({ length: count }, (_, index) => `${prefix}${String(start + index).padStart(width, '0')}${suffix}`)
}

if (!form.sampleId && sampleOptions.value.length) form.sampleId = sampleOptions.value[0]
if (!form.replicate) form.replicate = '0'

function loadDetectParams() {
  try {
    const saved = JSON.parse(localStorage.getItem('rapeseed-pheno-tool:seed-detect-params') || '{}')
    return {
      threshold: Number(saved.threshold) || 73,
      minSeedArea: Number(saved.minSeedArea) || 18,
      maxSeedArea: Number(saved.maxSeedArea) || 1400,
      minRoundness: Number(saved.minRoundness) || 0.25,
      minCircularity: Number(saved.minCircularity) || 0.18,
      maxAspect: Number(saved.maxAspect) || 3.2,
      edgeMarginRatio: Number(saved.edgeMarginRatio) || 0.03,
      touchingAreaMultiplier: Number(saved.touchingAreaMultiplier) || 4,
      foregroundMode: ['auto', 'light', 'dark'].includes(saved.foregroundMode) ? saved.foregroundMode : 'dark',
    }
  } catch {
    return {
      threshold: 73,
      minSeedArea: 18,
      maxSeedArea: 1400,
      minRoundness: 0.25,
      minCircularity: 0.18,
      maxAspect: 3.2,
      edgeMarginRatio: 0.03,
      touchingAreaMultiplier: 4,
      foregroundMode: 'dark',
    }
  }
}

function saveDetectParams() {
  localStorage.setItem(
    'rapeseed-pheno-tool:seed-detect-params',
    JSON.stringify({
      threshold: threshold.value,
      minSeedArea: minSeedArea.value,
      maxSeedArea: maxSeedArea.value,
      minRoundness: minRoundness.value,
      minCircularity: minCircularity.value,
      maxAspect: maxAspect.value,
      edgeMarginRatio: edgeMarginRatio.value,
      touchingAreaMultiplier: touchingAreaMultiplier.value,
      foregroundMode: foregroundMode.value,
    }),
  )
}

async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) return
  stream.value = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } },
    audio: false,
  })
  cameraOn.value = true
  await nextTick()
  video.value.srcObject = stream.value
  await video.value.play()
}

function stopCamera() {
  stream.value?.getTracks().forEach((track) => track.stop())
  stream.value = null
  cameraOn.value = false
}

async function capturePhoto() {
  if (!video.value) return
  const snap = document.createElement('canvas')
  snap.width = video.value.videoWidth
  snap.height = video.value.videoHeight
  snap.getContext('2d').drawImage(video.value, 0, 0)
  imageName.value = `${form.sampleId || 'silique'}-${form.siliqueId || 'capture'}.jpg`
  await loadImageUrl(snap.toDataURL('image/jpeg', 0.92))
}

function loadFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  imageName.value = file.name
  const url = URL.createObjectURL(file)
  loadImageUrl(url, () => URL.revokeObjectURL(url))
  event.target.value = ''
}

function loadImageUrl(url, afterLoad) {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = async () => {
      image.value = img
      resetImageMeasurements()
      await nextTick()
      resizeCanvas()
      draw()
      afterLoad?.()
      resolve()
    }
    img.src = url
  })
}

function resizeCanvas() {
  if (!canvas.value || !image.value) return
  const holder = canvas.value.parentElement
  const ratio = image.value.height / image.value.width
  canvas.value.width = Math.min(image.value.width, holder.clientWidth)
  canvas.value.height = canvas.value.width * ratio
}

function canvasPoint(event) {
  const rect = canvas.value.getBoundingClientRect()
  return {
    x: ((event.clientX - rect.left) * canvas.value.width) / rect.width,
    y: ((event.clientY - rect.top) * canvas.value.height) / rect.height,
  }
}

function handleCanvasClick(event) {
  if (!image.value) return
  const point = canvasPoint(event)
  if (mode.value === 'seedAdd') {
    state.seedPoints.push({ ...point, source: 'manual_add' })
    state.confirmedSeedCount = null
    state.confirmedSeedsPerCm = null
    draw()
    return
  }
  if (mode.value === 'seedRemove') {
    removeNearestSeed(point)
    draw()
    return
  }

  pendingPoints.value.push(point)
  if (pendingPoints.value.length === 2) {
    if (mode.value === 'scale') {
      const pixels = distance(pendingPoints.value[0], pendingPoints.value[1])
      state.scale = { points: [...pendingPoints.value], pixels }
      state.mmPerPixel = Number(scaleLengthMm.value) / pixels
    }
    if (mode.value === 'silique') {
      state.siliqueLine = {
        points: [...pendingPoints.value],
        pixels: distance(pendingPoints.value[0], pendingPoints.value[1]),
      }
      state.confirmedSiliqueLengthMm = null
      state.confirmedSiliquePhoto = null
      state.confirmedSeedsPerCm = null
    }
    if (mode.value === 'seedRoi') {
      state.seedRoi = normalizeRect(pendingPoints.value[0], pendingPoints.value[1])
    }
    pendingPoints.value = []
  }
  draw()
}

function normalizeRect(a, b) {
  return {
    x: Math.min(a.x, b.x),
    y: Math.min(a.y, b.y),
    width: Math.abs(a.x - b.x),
    height: Math.abs(a.y - b.y),
  }
}

function removeNearestSeed(point) {
  if (!state.seedPoints.length) return
  let bestIndex = -1
  let bestDistance = Infinity
  state.seedPoints.forEach((seed, index) => {
    const d = distance(point, seed)
    if (d < bestDistance) {
      bestDistance = d
      bestIndex = index
    }
  })
  if (bestIndex >= 0 && bestDistance < 35) {
    const [removed] = state.seedPoints.splice(bestIndex, 1)
    state.deletedSeeds.push({ ...removed, deletedAt: new Date().toISOString() })
    state.confirmedSeedCount = null
    state.confirmedSeedsPerCm = null
  }
}

function recommendSeedParams() {
  if (!canvas.value || !image.value) return
  foregroundMode.value = 'dark'
  threshold.value = 73
  minSeedArea.value = Math.max(8, minSeedArea.value)
  maxSeedArea.value = Math.max(900, maxSeedArea.value)
  minRoundness.value = Math.min(0.3, Math.max(0.18, minRoundness.value))
  minCircularity.value = 0.18
  maxAspect.value = 3.2
  edgeMarginRatio.value = 0.03
  touchingAreaMultiplier.value = 4
  saveDetectParams()
  detectStatus.value = `已推荐参数：籽粒颜色=比背景暗，亮度阈值=${threshold.value}。如仍漏检粘连籽粒，请用“补籽粒”人工加点。`
}

async function autoDetectSeeds() {
  if (!canvas.value || !image.value) return
  detecting.value = true
  detectStatus.value = '正在生成候选点...'
  await nextTick()

  try {
    if (serviceUrl.value.trim()) {
      await detectSeedsWithService()
    } else {
      detectSeedsInBrowser()
    }
  } catch (error) {
    detectStatus.value = '候选点生成失败。请检查识别服务地址，或清空服务地址后用浏览器候选算法。'
  } finally {
    detecting.value = false
  }
}

async function detectSeedsWithService() {
  localStorage.setItem('rapeseed-pheno-tool:seed-service-url', serviceUrl.value.trim())
  const endpoint = `${serviceUrl.value.trim().replace(/\/$/, '')}/api/seed-candidates`
  const response = await fetch(endpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      imageDataUrl: canvas.value.toDataURL('image/jpeg', 0.9),
      roi: state.seedRoi,
      foregroundMode: foregroundMode.value,
      minArea: minSeedArea.value,
      maxArea: maxSeedArea.value,
      minRoundness: minRoundness.value,
      minCircularity: minCircularity.value,
      maxAspect: maxAspect.value,
      edgeMarginRatio: edgeMarginRatio.value,
      touchingAreaMultiplier: touchingAreaMultiplier.value,
      useWatershed: true,
      useYolo: true,
    }),
  })
  if (!response.ok) throw new Error(`Seed service failed: ${response.status}`)
  const result = await response.json()
  const points = Array.isArray(result.points) ? result.points : []
  const normalized = points.map((point) => ({ ...point, source: 'auto' }))
  state.detectedSeeds = normalized
  state.seedPoints = normalized
  state.confirmedSeedCount = null
  state.confirmedSeedsPerCm = null
  if (points.length) {
    const confidenceMap = { high: '高', medium: '中', low: '低' }
    const confidence = confidenceMap[result.confidence] || '未知'
    const reviewText = result.reviewCount ? `，疑似粘连/异常 ${result.reviewCount} 处` : ''
    const engineText = result.engine === 'trained-yolo-seed-detector' ? '训练模型' : '多算法融合'
    detectStatus.value = `后端${engineText}生成 ${points.length} 个候选点，置信度 ${confidence}${reviewText}。请人工增删后点击确认籽粒数。`
  } else {
    detectStatus.value = '后端识别没有生成候选点。请检查框选区域、拍照质量或参数。'
  }
  draw()
}

function confirmSiliqueLength() {
  if (!metrics.value.siliqueLengthMm || !canvas.value || !image.value) return
  state.confirmedSiliqueLengthMm = metrics.value.siliqueLengthMm
  state.manualSiliqueLengthMm = metrics.value.siliqueLengthMm
  state.confirmedSiliquePhoto = {
    imageName: imageName.value,
    imageDataUrl: canvas.value.toDataURL('image/jpeg', 0.9),
    imageWidth: canvas.value.width,
    imageHeight: canvas.value.height,
    mmPerPixel: state.mmPerPixel,
    scale: state.scale,
    siliqueLine: state.siliqueLine,
    siliqueLengthMm: metrics.value.siliqueLengthMm,
    confirmedAt: new Date().toISOString(),
  }
  if (state.confirmedSeedCount) {
    state.confirmedSeedsPerCm = round(state.confirmedSeedCount / (state.confirmedSiliqueLengthMm / 10), 2)
  }
  detectStatus.value = `已确认角果长度 ${state.confirmedSiliqueLengthMm} mm。现在可以重新拍摄该角果剥开的籽粒照片。`
}

function confirmSeedCount() {
  state.confirmedSeedCount = metrics.value.seedCount
  state.manualSeedCount = metrics.value.seedCount
  const length = state.confirmedSiliqueLengthMm || metrics.value.siliqueLengthMm
  state.confirmedSeedsPerCm = length && metrics.value.seedCount ? round(metrics.value.seedCount / (length / 10), 2) : ''
  detectStatus.value = `已确认籽粒数 ${state.confirmedSeedCount}。保存记录时会自动填入该数值和点位。`
}

async function startYoloTraining() {
  if (!serviceUrl.value.trim() || !trainingStats.value.yoloReady) return
  training.value = true
  trainStatus.value = '正在提交训练任务...'
  trainJob.value = null
  clearTrainingPoll()

  try {
    localStorage.setItem('rapeseed-pheno-tool:seed-service-url', serviceUrl.value.trim())
    const endpoint = `${serviceUrl.value.trim().replace(/\/$/, '')}/api/train-yolo`
    const payloadRecords = records.value.filter((record) => record.imageDataUrl && (record.seedPoints || []).length && record.quality !== 'exclude')
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        records: payloadRecords,
        model: 'yolo11n.pt',
        epochs: 50,
        imgsz: 1024,
        batch: 4,
      }),
    })
    const result = await response.json()
    if (!response.ok || !result.ok) throw new Error(result.error || `训练服务失败: ${response.status}`)
    trainJob.value = result.job
    updateTrainStatus(result.job)
    trainPollTimer = window.setInterval(() => pollYoloTraining(result.job.id), 3000)
  } catch (error) {
    training.value = false
    trainStatus.value = `训练任务提交失败：${readableError(error)}`
  }
}

async function startStoredYoloTraining() {
  if (!serviceUrl.value.trim()) return
  training.value = true
  trainStatus.value = '正在用后端共享记录提交训练任务...'
  trainJob.value = null
  clearTrainingPoll()

  try {
    localStorage.setItem('rapeseed-pheno-tool:seed-service-url', serviceUrl.value.trim())
    await pushRecordsToServer(false)
    const endpoint = `${serviceUrl.value.trim().replace(/\/$/, '')}/api/train-yolo-stored`
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: 'yolo11n.pt',
        epochs: 50,
        imgsz: 1024,
        batch: 4,
      }),
    })
    const result = await response.json()
    if (!response.ok || !result.ok) throw new Error(result.error || `训练服务失败: ${response.status}`)
    trainJob.value = result.job
    updateTrainStatus(result.job)
    trainPollTimer = window.setInterval(() => pollYoloTraining(result.job.id), 3000)
  } catch (error) {
    training.value = false
    trainStatus.value = `后端共享记录训练失败：${readableError(error)}`
  }
}

async function startCloudYoloTraining() {
  if (!serviceUrl.value.trim()) return
  if (!supabaseSession.value?.user?.id) {
    trainStatus.value = '请先登录 Supabase 账号，再用云端数据训练。'
    return
  }
  training.value = true
  trainStatus.value = '正在让电脑后端拉取 Supabase 云端数据...'
  trainJob.value = null
  clearTrainingPoll()

  try {
    localStorage.setItem('rapeseed-pheno-tool:seed-service-url', serviceUrl.value.trim())
    supabaseSession.value = await refreshSupabaseSession(supabaseSettings, supabaseSession.value)
    const endpoint = `${serviceUrl.value.trim().replace(/\/$/, '')}/api/train-yolo-cloud`
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        supabaseUrl: supabaseSettings.url,
        anonKey: supabaseSettings.anonKey,
        accessToken: supabaseSession.value.access_token,
        userId: supabaseSession.value.user.id,
        model: 'yolo11n.pt',
        epochs: 50,
        imgsz: 1024,
        batch: 4,
      }),
    })
    const result = await response.json()
    if (!response.ok || !result.ok) throw new Error(result.error || `云端训练服务失败: ${response.status}`)
    trainJob.value = result.job
    updateTrainStatus(result.job)
    trainPollTimer = window.setInterval(() => pollYoloTraining(result.job.id), 3000)
  } catch (error) {
    training.value = false
    trainStatus.value = `云端数据训练失败：${readableError(error)}`
  }
}

async function testCloudSync() {
  cloudSyncing.value = true
  cloudStatus.value = '正在测试 Supabase 连接...'
  try {
    await testSupabaseConnection(supabaseSettings)
    supabaseSession.value = loadSupabaseSession()
    supabaseSettings.enabled = true
    saveSupabaseSettings(supabaseSettings)
    cloudStatus.value = 'Supabase 连接成功，已开启自动云同步。'
  } catch (error) {
    cloudStatus.value = `Supabase 连接失败：${readableError(error)}`
  } finally {
    cloudSyncing.value = false
  }
}

async function registerCloudUser() {
  if (!authForm.email.trim() || !authForm.password) return
  cloudSyncing.value = true
  cloudStatus.value = '正在注册 Supabase 账号...'
  try {
    const session = await signUpSupabase(supabaseSettings, authForm.email.trim(), authForm.password)
    supabaseSession.value = session?.access_token ? session : null
    supabaseSettings.enabled = Boolean(supabaseSession.value)
    cloudStatus.value = supabaseSession.value ? '注册成功，已登录并开启云同步。' : '注册成功。若 Supabase 开启了邮箱验证，请先到邮箱确认后再登录。'
  } catch (error) {
    cloudStatus.value = `注册失败：${readableError(error)}`
  } finally {
    cloudSyncing.value = false
  }
}

async function loginCloudUser() {
  if (!authForm.email.trim() || !authForm.password) return
  cloudSyncing.value = true
  cloudStatus.value = '正在登录 Supabase...'
  try {
    supabaseSession.value = await signInSupabase(supabaseSettings, authForm.email.trim(), authForm.password)
    supabaseSettings.enabled = true
    saveSupabaseSettings(supabaseSettings)
    cloudStatus.value = `已登录：${currentCloudUser.value}`
    await pullRecordsFromCloud()
  } catch (error) {
    cloudStatus.value = `登录失败：${readableError(error)}`
  } finally {
    cloudSyncing.value = false
  }
}

async function logoutCloudUser() {
  cloudSyncing.value = true
  try {
    await signOutSupabase(supabaseSettings, supabaseSession.value)
    supabaseSession.value = null
    supabaseSettings.enabled = false
    saveSupabaseSettings(supabaseSettings)
    cloudStatus.value = '已退出云端账号。本机 IndexedDB 数据仍保留。'
  } catch (error) {
    cloudStatus.value = `退出失败：${readableError(error)}`
  } finally {
    cloudSyncing.value = false
  }
}

async function pushRecordsToCloud(showMessage = true) {
  if (!supabaseSettings.enabled) return
  if (!supabaseSession.value?.user?.id) {
    cloudStatus.value = '请先登录 Supabase 账号，再同步云端。'
    return
  }
  cloudSyncing.value = true
  try {
    supabaseSession.value = await refreshSupabaseSession(supabaseSettings, supabaseSession.value)
    const synced = await upsertCloudSiliqueRecords(supabaseSettings, records.value, supabaseSession.value)
    records.value = mergeRecordLists(records.value, synced)
    await saveSiliqueRecords(records.value)
    if (showMessage) cloudStatus.value = `已同步到 Supabase：${synced.length} 条记录。`
  } catch (error) {
    cloudStatus.value = `同步到 Supabase 失败：${readableError(error)}`
  } finally {
    cloudSyncing.value = false
  }
}

async function pullRecordsFromCloud() {
  if (!supabaseSession.value?.user?.id) {
    cloudStatus.value = '请先登录 Supabase 账号，再从云端拉取。'
    return
  }
  cloudSyncing.value = true
  try {
    supabaseSession.value = await refreshSupabaseSession(supabaseSettings, supabaseSession.value)
    const cloudRecords = await fetchCloudSiliqueRecords(supabaseSettings, supabaseSession.value)
    records.value = mergeRecordLists(records.value, cloudRecords)
    await saveSiliqueRecords(records.value)
    cloudStatus.value = `已从 Supabase 拉取 ${cloudRecords.length} 条记录，本机当前 ${records.value.length} 条。`
  } catch (error) {
    cloudStatus.value = `从 Supabase 拉取失败：${readableError(error)}`
  } finally {
    cloudSyncing.value = false
  }
}

async function syncRecordToCloud(record) {
  if (!supabaseSettings.enabled) return record
  if (!supabaseSession.value?.user?.id) {
    cloudStatus.value = '记录已本地保存。登录 Supabase 后可同步到个人云端。'
    return record
  }
  try {
    supabaseSession.value = await refreshSupabaseSession(supabaseSettings, supabaseSession.value)
    const cloudRecord = await upsertCloudSiliqueRecord(supabaseSettings, record, supabaseSession.value)
    cloudStatus.value = cloudRecord.cloudUploadError
      ? `记录数据已同步到 Supabase，但照片上传失败：${cloudRecord.cloudUploadError}`
      : '记录已同步到 Supabase。'
    return { ...record, ...cloudRecord }
  } catch (error) {
    cloudStatus.value = `记录已本地保存，但云同步失败：${readableError(error)}`
    return record
  }
}

async function deleteRecordFromCloud(recordId) {
  if (!supabaseSettings.enabled || !supabaseSession.value?.user?.id) return
  try {
    supabaseSession.value = await refreshSupabaseSession(supabaseSettings, supabaseSession.value)
    await deleteCloudSiliqueRecord(supabaseSettings, recordId, supabaseSession.value)
    cloudStatus.value = '云端记录已删除。'
  } catch (error) {
    cloudStatus.value = `云端删除失败：${readableError(error)}`
  }
}

async function pollYoloTraining(jobId) {
  try {
    const endpoint = `${serviceUrl.value.trim().replace(/\/$/, '')}/api/train-yolo/${jobId}`
    const response = await fetch(endpoint)
    const result = await response.json()
    if (!response.ok || !result.ok) throw new Error(result.error || `状态查询失败: ${response.status}`)
    trainJob.value = result.job
    updateTrainStatus(result.job)
    if (['completed', 'failed'].includes(result.job.status)) {
      training.value = false
      clearTrainingPoll()
    }
  } catch (error) {
    training.value = false
    trainStatus.value = `训练状态查询失败：${readableError(error)}`
    clearTrainingPoll()
  }
}

async function pullRecordsFromServer() {
  if (!serviceUrl.value.trim()) return
  syncing.value = true
  try {
    localStorage.setItem('rapeseed-pheno-tool:seed-service-url', serviceUrl.value.trim())
    const endpoint = `${serviceUrl.value.trim().replace(/\/$/, '')}/api/silique-records`
    const response = await fetch(endpoint)
    const result = await response.json()
    if (!response.ok || !result.ok) throw new Error(result.error || `同步失败: ${response.status}`)
    records.value = mergeRecordLists(records.value, result.records || [])
    await saveSiliqueRecords(records.value)
    syncStatus.value = `已从电脑后端同步 ${result.records?.length || 0} 条记录，本机当前 ${records.value.length} 条。`
  } catch (error) {
    syncStatus.value = `从后端同步失败：${readableError(error)}`
  } finally {
    syncing.value = false
  }
}

async function pushRecordsToServer(showMessage = true) {
  if (!serviceUrl.value.trim()) return
  syncing.value = true
  try {
    localStorage.setItem('rapeseed-pheno-tool:seed-service-url', serviceUrl.value.trim())
    const endpoint = `${serviceUrl.value.trim().replace(/\/$/, '')}/api/silique-records`
    const response = await fetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ records: records.value }),
    })
    const result = await response.json()
    if (!response.ok || !result.ok) throw new Error(result.error || `同步失败: ${response.status}`)
    records.value = mergeRecordLists(records.value, result.records || [])
    await saveSiliqueRecords(records.value)
    if (showMessage) syncStatus.value = `已同步到电脑后端：${result.count || records.value.length} 条记录。`
  } catch (error) {
    syncStatus.value = `同步到后端失败：${readableError(error)}`
  } finally {
    syncing.value = false
  }
}

async function syncRecordDeleteToServer(recordId) {
  if (!serviceUrl.value.trim()) return
  try {
    const endpoint = `${serviceUrl.value.trim().replace(/\/$/, '')}/api/silique-records/${encodeURIComponent(recordId)}`
    await fetch(endpoint, { method: 'DELETE' })
  } catch {
    syncStatus.value = '本机已删除，但后端删除失败。请稍后点“同步到电脑”。'
  }
}

async function clearServerRecords() {
  if (!serviceUrl.value.trim()) return
  syncing.value = true
  try {
    const endpoint = `${serviceUrl.value.trim().replace(/\/$/, '')}/api/silique-records`
    const response = await fetch(endpoint, { method: 'DELETE' })
    const result = await response.json()
    if (!response.ok || !result.ok) throw new Error(result.error || `清空失败: ${response.status}`)
    syncStatus.value = '电脑后端共享记录已清空。'
  } catch (error) {
    syncStatus.value = `清空电脑后端失败：${readableError(error)}`
  } finally {
    syncing.value = false
  }
}

function mergeRecordLists(localRecords, remoteRecords) {
  const merged = new Map()
  ;[...remoteRecords, ...localRecords].forEach((record) => {
    if (!record?.id) return
    const existing = merged.get(record.id)
    if (!existing) {
      merged.set(record.id, record)
      return
    }
    const currentTime = new Date(record.editedAt || record.createdAt || record.measuredAt || 0).getTime()
    const existingTime = new Date(existing.editedAt || existing.createdAt || existing.measuredAt || 0).getTime()
    merged.set(record.id, currentTime >= existingTime ? record : existing)
  })
  return [...merged.values()].sort((a, b) => String(b.createdAt || b.measuredAt || '').localeCompare(String(a.createdAt || a.measuredAt || '')))
}

function updateTrainStatus(job) {
  const statusMap = {
    queued: '排队中',
    preparing: '生成数据集',
    ready: '数据集已生成',
    training: '训练中',
    completed: '训练完成',
    failed: '训练失败',
  }
  const prefix = statusMap[job.status] || job.status
  const counts = job.trainImages ? `训练 ${job.trainImages} 张，验证 ${job.valImages} 张，标注 ${job.seedAnnotations} 粒。` : ''
  trainStatus.value = `${prefix}：${job.message || ''} ${counts}`.trim()
}

function readableError(error) {
  const message = error?.message || String(error)
  if (message.includes('JWT expired') || message.includes('Invalid Refresh Token') || message.includes('refresh_token')) {
    return '登录已过期，请退出后重新登录 Supabase。'
  }
  return message
}

function clearTrainingPoll() {
  if (trainPollTimer) window.clearInterval(trainPollTimer)
  trainPollTimer = null
}

function detectSeedsInBrowser() {
    const maxAnalyzeWidth = 900
    const scale = Math.min(1, maxAnalyzeWidth / canvas.value.width)
    const work = document.createElement('canvas')
    work.width = Math.max(1, Math.round(canvas.value.width * scale))
    work.height = Math.max(1, Math.round(canvas.value.height * scale))
    const ctx = work.getContext('2d', { willReadFrequently: true })
    ctx.drawImage(image.value, 0, 0, work.width, work.height)
    const img = ctx.getImageData(0, 0, work.width, work.height)
    const scaledRoi = state.seedRoi
      ? {
          x: state.seedRoi.x * scale,
          y: state.seedRoi.y * scale,
          width: state.seedRoi.width * scale,
          height: state.seedRoi.height * scale,
        }
      : null
    const areaScale = scale * scale
    const points = connectedComponents({
      imageData: img,
      cutoff: threshold.value,
      minArea: Math.max(1, minSeedArea.value * areaScale),
      maxArea: Math.max(2, maxSeedArea.value * areaScale),
      minRoundness: minRoundness.value,
      minCircularity: minCircularity.value,
      maxAspect: maxAspect.value,
      edgeMarginRatio: edgeMarginRatio.value,
      touchingAreaMultiplier: touchingAreaMultiplier.value,
      roi: scaledRoi,
      mode: foregroundMode.value,
    }).map((point) => ({
      ...point,
      x: point.x / scale,
      y: point.y / scale,
      area: point.area / areaScale,
    }))
    const normalized = points.map((point) => ({ ...point, source: 'browser_auto' }))
    state.detectedSeeds = normalized
    state.seedPoints = normalized
    state.confirmedSeedCount = null
    state.confirmedSeedsPerCm = null
    const reviewCount = normalized.filter((point) => point.review).length
    detectStatus.value = points.length
      ? `已生成 ${points.length} 个候选点${reviewCount ? `，其中 ${reviewCount} 个疑似粘连/异常` : ''}，请人工增删确认。`
      : '没有生成候选点。请调整阈值/面积，或先框选更准确的籽粒区域。'
    draw()
}

function connectedComponents({ imageData, cutoff, minArea, maxArea, minRoundness, minCircularity, maxAspect, edgeMarginRatio, touchingAreaMultiplier, roi, mode }) {
  const { data, width, height } = imageData
  const seen = new Uint8Array(width * height)
  const components = []
  const stack = []
  const rawBounds = roi
    ? {
        minX: Math.max(0, Math.floor(roi.x)),
        minY: Math.max(0, Math.floor(roi.y)),
        maxX: Math.min(width - 1, Math.ceil(roi.x + roi.width)),
        maxY: Math.min(height - 1, Math.ceil(roi.y + roi.height)),
      }
    : { minX: 0, minY: 0, maxX: width - 1, maxY: height - 1 }
  const marginX = Math.round((rawBounds.maxX - rawBounds.minX + 1) * edgeMarginRatio)
  const marginY = Math.round((rawBounds.maxY - rawBounds.minY + 1) * edgeMarginRatio)
  const bounds = {
    minX: Math.min(rawBounds.maxX, rawBounds.minX + marginX),
    minY: Math.min(rawBounds.maxY, rawBounds.minY + marginY),
    maxX: Math.max(rawBounds.minX, rawBounds.maxX - marginX),
    maxY: Math.max(rawBounds.minY, rawBounds.maxY - marginY),
  }

  for (let y = bounds.minY; y <= bounds.maxY; y += 1) {
    for (let x = bounds.minX; x <= bounds.maxX; x += 1) {
      const idx = y * width + x
      if (seen[idx]) continue
      if (!isForeground(data, idx, cutoff, mode)) {
        seen[idx] = 1
        continue
      }
      let area = 0
      let sumX = 0
      let sumY = 0
      let minX = x
      let maxX = x
      let minY = y
      let maxY = y
      let perimeter = 0
      stack.push(idx)
      seen[idx] = 1
      while (stack.length) {
        const current = stack.pop()
        const cx = current % width
        const cy = Math.floor(current / width)
        area += 1
        sumX += cx
        sumY += cy
        minX = Math.min(minX, cx)
        maxX = Math.max(maxX, cx)
        minY = Math.min(minY, cy)
        maxY = Math.max(maxY, cy)
        const cardinalNeighbors = [
          [cx - 1, cy],
          [cx + 1, cy],
          [cx, cy - 1],
          [cx, cy + 1],
        ]
        for (const [nx, ny] of cardinalNeighbors) {
          if (nx < bounds.minX || nx > bounds.maxX || ny < bounds.minY || ny > bounds.maxY) {
            perimeter += 1
            continue
          }
          const next = ny * width + nx
          if (!isForeground(data, next, cutoff, mode)) perimeter += 1
        }
        const neighbors = [
          [cx - 1, cy],
          [cx + 1, cy],
          [cx, cy - 1],
          [cx, cy + 1],
          [cx - 1, cy - 1],
          [cx + 1, cy - 1],
          [cx - 1, cy + 1],
          [cx + 1, cy + 1],
        ]
        for (const [nx, ny] of neighbors) {
          if (nx < bounds.minX || nx > bounds.maxX || ny < bounds.minY || ny > bounds.maxY) continue
          const next = ny * width + nx
          if (next < 0 || next >= seen.length || seen[next]) continue
          if (!isForeground(data, next, cutoff, mode)) continue
          seen[next] = 1
          stack.push(next)
        }
      }
      const boxWidth = maxX - minX + 1
      const boxHeight = maxY - minY + 1
      const boxArea = boxWidth * boxHeight
      const roundness = boxArea ? area / boxArea : 0
      const aspect = Math.max(boxWidth, boxHeight) / Math.max(1, Math.min(boxWidth, boxHeight))
      const circularity = perimeter ? (4 * Math.PI * area) / (perimeter * perimeter) : 0
      if (
        area >= minArea &&
        area <= maxArea * touchingAreaMultiplier &&
        roundness >= minRoundness &&
        circularity >= minCircularity &&
        aspect <= maxAspect
      ) {
        components.push({ x: sumX / area, y: sumY / area, area, roundness, circularity, aspect })
      }
    }
  }
  const normalAreas = components.filter((item) => item.area <= maxArea).map((item) => item.area).sort((a, b) => a - b)
  const medianArea = normalAreas.length ? normalAreas[Math.floor(normalAreas.length / 2)] : 0
  const result = []
  for (const item of components) {
    if (!medianArea || item.area <= maxArea) {
      result.push(item)
      continue
    }
    const estimated = Math.max(1, Math.min(6, Math.round(item.area / medianArea)))
    if (estimated <= 1) {
      result.push(item)
      continue
    }
    const radius = Math.max(8, Math.sqrt(item.area / Math.PI) / 3)
    for (let index = 0; index < estimated; index += 1) {
      const angle = (2 * Math.PI * index) / estimated
      result.push({
        ...item,
        x: item.x + radius * Math.cos(angle),
        y: item.y + radius * Math.sin(angle),
        estimatedSeeds: estimated,
        review: true,
      })
    }
  }
  return result
}

function isForeground(data, index, cutoff, mode) {
  const i = index * 4
  const r = data[i]
  const g = data[i + 1]
  const b = data[i + 2]
  const brightness = (r + g + b) / 3
  return mode === 'dark' ? brightness < cutoff : brightness > cutoff
}

function resetImageMeasurements() {
  state.mmPerPixel = null
  state.scale = null
  state.siliqueLine = null
  state.seedRoi = null
  state.seedPoints = []
  state.detectedSeeds = []
  state.deletedSeeds = []
  state.confirmedSeedCount = null
  state.confirmedSeedsPerCm = null
  state.manualSeedCount = ''
  pendingPoints.value = []
}

function resetMeasurements() {
  resetImageMeasurements()
  state.confirmedSiliqueLengthMm = null
  state.confirmedSiliquePhoto = null
  state.manualSiliqueLengthMm = ''
  image.value = null
  imageName.value = ''
}

function clearSeeds() {
  state.seedPoints = []
  state.detectedSeeds = []
  state.deletedSeeds = []
  state.confirmedSeedCount = null
  state.confirmedSeedsPerCm = null
  state.manualSeedCount = ''
  detectStatus.value = '已清空籽粒点。'
  draw()
}

async function saveRecord() {
  const missing = []
  if (!form.sampleId.trim()) missing.push('样品编号')
  if (!form.siliqueId.trim()) missing.push('角果编号')
  const siliqueLengthMm = numberOrEmpty(state.manualSiliqueLengthMm)
  const seedCount = numberOrEmpty(state.manualSeedCount)
  if (siliqueLengthMm === '') missing.push('保存长度 mm')
  if (seedCount === '') missing.push('保存籽粒数')
  if (missing.length) {
    saveStatus.value = `还不能保存：请补全 ${missing.join('、')}。`
    return
  }
  savingRecord.value = true
  saveStatus.value = '正在保存记录...'
  const seedImageDataUrl = canvas.value && image.value ? canvas.value.toDataURL('image/jpeg', 0.9) : ''
  const siliquePhoto = state.confirmedSiliquePhoto
  try {
    const record = {
      id: createId(),
      genotype: form.genotype.trim(),
      sampleId: form.sampleId.trim(),
      replicate: form.replicate.trim(),
      siliqueId: form.siliqueId.trim(),
      quality: form.quality,
      siliqueLengthMm,
      seedCount,
      seedsPerCm: siliqueLengthMm ? round(seedCount / (siliqueLengthMm / 10), 2) : '',
      method: state.detectedSeeds.length ? '候选点+人工确认' : '人工标注',
      imageName: imageName.value,
      imageDataUrl: seedImageDataUrl,
      imageWidth: canvas.value?.width || null,
      imageHeight: canvas.value?.height || null,
      seedImageName: imageName.value,
      seedImageDataUrl,
      seedImageWidth: canvas.value?.width || null,
      seedImageHeight: canvas.value?.height || null,
      siliqueImageName: siliquePhoto?.imageName || '',
      siliqueImageDataUrl: siliquePhoto?.imageDataUrl || '',
      siliqueImageWidth: siliquePhoto?.imageWidth || null,
      siliqueImageHeight: siliquePhoto?.imageHeight || null,
      siliqueScale: siliquePhoto?.scale || null,
      siliqueLine: siliquePhoto?.siliqueLine || null,
      cloudUrl: form.cloudUrl.trim(),
      notes: form.notes.trim(),
      seedRoi: state.seedRoi,
      autoSeedPoints: state.detectedSeeds.map(cleanPoint),
      seedPoints: state.seedPoints.map(cleanPoint),
      deletedSeedPoints: state.deletedSeeds.map(cleanPoint),
      rawAutoCount: state.detectedSeeds.length,
      correctedCount: confirmedMetrics.value.seedCount,
      confirmedSiliqueLengthMm: state.confirmedSiliqueLengthMm,
      confirmedSeedCount: state.confirmedSeedCount,
      manualSiliqueLengthMm: siliqueLengthMm,
      manualSeedCount: seedCount,
      measuredAt: new Date().toISOString().slice(0, 10),
      createdAt: new Date().toISOString(),
    }
    records.value = [record, ...records.value]
    await saveSiliqueRecords(records.value)
    const syncedRecord = await syncRecordToCloud(record)
    if (syncedRecord !== record) {
      records.value = records.value.map((item) => (item.id === record.id ? syncedRecord : item))
      await saveSiliqueRecords(records.value)
    }
    await pushRecordsToServer(false)
    syncStatus.value = serviceUrl.value.trim() ? '记录已保存到本机，并已尝试同步到电脑后端。' : '记录已保存到本机。填写电脑后端地址后可同步。'
    saveStatus.value = supabaseSettings.enabled ? '记录已生成，并已尝试上传云端。' : '记录已生成并保存到本机。'
    incrementSiliqueId()
    resetMeasurements()
    draw()
  } catch (error) {
    saveStatus.value = `保存失败：${readableError(error)}`
  } finally {
    savingRecord.value = false
  }
}

function numberOrEmpty(value) {
  if (value === null || value === undefined || value === '') return ''
  const number = Number(value)
  return Number.isFinite(number) ? number : ''
}

function cleanPoint(point) {
  return {
    x: round(point.x, 2),
    y: round(point.y, 2),
    area: point.area ? round(point.area, 2) : undefined,
    source: point.source || 'manual',
    review: Boolean(point.review),
    estimatedSeeds: point.estimatedSeeds || undefined,
    circularity: point.circularity ? round(point.circularity, 3) : undefined,
    roundness: point.roundness ? round(point.roundness, 3) : undefined,
    aspect: point.aspect ? round(point.aspect, 3) : undefined,
  }
}

function incrementSiliqueId() {
  const match = form.siliqueId.match(/^(.*?)(\d+)$/)
  if (!match) return
  const next = String(Number(match[2]) + 1).padStart(match[2].length, '0')
  form.siliqueId = `${match[1]}${next}`
}

async function clearRecords() {
  records.value = []
  await saveSiliqueRecords(records.value)
  await clearServerRecords()
  cancelEditRecord()
}

async function deleteRecord(recordId) {
  records.value = records.value.filter((record) => record.id !== recordId)
  await saveSiliqueRecords(records.value)
  await deleteSiliqueRecordIndexed(recordId)
  await deleteRecordFromCloud(recordId)
  await syncRecordDeleteToServer(recordId)
  if (editingRecordId.value === recordId) cancelEditRecord()
}

function startEditRecord(record) {
  editingRecordId.value = record.id
  editDraft.genotype = record.genotype || ''
  editDraft.sampleId = record.sampleId || ''
  editDraft.replicate = record.replicate || ''
  editDraft.siliqueId = record.siliqueId || ''
  editDraft.siliqueLengthMm = record.siliqueLengthMm ?? ''
  editDraft.seedCount = record.seedCount ?? ''
  editDraft.quality = record.quality || 'good'
  editDraft.notes = record.notes || ''
}

function cancelEditRecord() {
  editingRecordId.value = null
  editDraft.genotype = ''
  editDraft.sampleId = ''
  editDraft.replicate = ''
  editDraft.siliqueId = ''
  editDraft.siliqueLengthMm = ''
  editDraft.seedCount = ''
  editDraft.quality = 'good'
  editDraft.notes = ''
}

async function saveEditRecord(recordId) {
  const siliqueLengthMm = numberOrEmpty(editDraft.siliqueLengthMm)
  const seedCount = numberOrEmpty(editDraft.seedCount)
  if (siliqueLengthMm === '' || seedCount === '') return
  records.value = records.value.map((record) => {
    if (record.id !== recordId) return record
    return {
      ...record,
      genotype: editDraft.genotype.trim(),
      sampleId: editDraft.sampleId.trim(),
      replicate: editDraft.replicate,
      siliqueId: editDraft.siliqueId.trim(),
      quality: editDraft.quality,
      notes: editDraft.notes.trim(),
      siliqueLengthMm,
      seedCount,
      seedsPerCm: siliqueLengthMm ? round(seedCount / (siliqueLengthMm / 10), 2) : '',
      manualSiliqueLengthMm: siliqueLengthMm,
      manualSeedCount: seedCount,
      editedAt: new Date().toISOString(),
    }
  })
  await saveSiliqueRecords(records.value)
  const edited = records.value.find((record) => record.id === recordId)
  if (edited) {
    const syncedRecord = await syncRecordToCloud(edited)
    records.value = records.value.map((record) => (record.id === recordId ? syncedRecord : record))
    await saveSiliqueRecords(records.value)
  }
  await pushRecordsToServer(false)
  syncStatus.value = serviceUrl.value.trim() ? '修改已保存并已尝试同步到电脑后端。' : '修改已保存到本机。'
  cancelEditRecord()
}

function exportAnnotatedImage() {
  if (!canvas.value || !image.value) return
  const link = document.createElement('a')
  link.download = `${imageName.value || 'silique-annotation'}.png`
  link.href = canvas.value.toDataURL('image/png')
  link.click()
}

function draw() {
  if (!canvas.value) return
  const ctx = canvas.value.getContext('2d')
  ctx.clearRect(0, 0, canvas.value.width, canvas.value.height)
  if (!image.value) return
  ctx.drawImage(image.value, 0, 0, canvas.value.width, canvas.value.height)
  drawLine(ctx, state.scale, '#2563eb', '标尺')
  drawLine(ctx, state.siliqueLine, '#16a34a', metrics.value.siliqueLengthMm ? `角果 ${metrics.value.siliqueLengthMm} mm` : '角果')
  drawRect(ctx, state.seedRoi, '#9333ea', '籽粒区域')
  state.seedPoints.forEach((point, index) => drawSeed(ctx, point, index + 1))
  pendingPoints.value.forEach((point) => drawDot(ctx, point, '#111827'))
}

function drawLine(ctx, item, color, label) {
  if (!item?.points?.length) return
  const [a, b] = item.points
  ctx.strokeStyle = color
  ctx.lineWidth = 3
  ctx.beginPath()
  ctx.moveTo(a.x, a.y)
  ctx.lineTo(b.x, b.y)
  ctx.stroke()
  drawDot(ctx, a, color)
  drawDot(ctx, b, color)
  drawLabel(ctx, label, (a.x + b.x) / 2, (a.y + b.y) / 2, color)
}

function drawSeed(ctx, point) {
  const color = point.review ? '234, 88, 12' : '220, 38, 38'
  ctx.strokeStyle = `rgba(${color}, 0.9)`
  ctx.fillStyle = `rgba(${color}, 0.18)`
  ctx.lineWidth = 1.5
  ctx.beginPath()
  ctx.arc(point.x, point.y, point.review ? 7 : 5, 0, Math.PI * 2)
  ctx.fill()
  ctx.stroke()
  ctx.fillStyle = `rgba(${color}, 0.95)`
  ctx.beginPath()
  ctx.arc(point.x, point.y, 1.8, 0, Math.PI * 2)
  ctx.fill()
}

function drawRect(ctx, rect, color, label) {
  if (!rect) return
  ctx.strokeStyle = color
  ctx.lineWidth = 3
  ctx.setLineDash([8, 5])
  ctx.strokeRect(rect.x, rect.y, rect.width, rect.height)
  ctx.setLineDash([])
  drawLabel(ctx, label, rect.x + 6, rect.y + 20, color)
}

function drawDot(ctx, point, color) {
  ctx.fillStyle = color
  ctx.beginPath()
  ctx.arc(point.x, point.y, 5, 0, Math.PI * 2)
  ctx.fill()
}

function drawLabel(ctx, text, x, y, color) {
  ctx.font = '13px system-ui'
  const width = ctx.measureText(text).width + 10
  ctx.fillStyle = 'rgba(255,255,255,0.9)'
  ctx.fillRect(x, y - 17, width, 21)
  ctx.fillStyle = color
  ctx.fillText(text, x + 5, y - 3)
}

window.addEventListener('resize', () => {
  resizeCanvas()
  draw()
})

onBeforeUnmount(() => {
  clearTrainingPoll()
  stopCamera()
})
</script>

<template>
  <section class="silique-layout">
    <div class="measure-shell">
      <div class="toolbar">
        <button class="primary" type="button" @click="startCamera">
          <Video :size="18" />
          打开相机
        </button>
        <button type="button" :disabled="!cameraOn" @click="capturePhoto">
          <Camera :size="18" />
          拍照
        </button>
        <button type="button" :disabled="!cameraOn" @click="stopCamera">
          <Square :size="18" />
          关闭相机
        </button>
        <label class="native-file">
          <span><ImagePlus :size="18" /> 拍照上传</span>
          <input type="file" accept="image/*" capture="environment" @change="loadFile" />
        </label>
        <label class="native-file">
          <span><ImagePlus :size="18" /> 从相册选择</span>
          <input type="file" accept="image/*" @change="loadFile" />
        </label>
        <label class="scale-field">
          标尺长度 mm
          <input v-model.number="scaleLengthMm" type="number" min="1" />
        </label>
      </div>

      <div v-if="cameraOn" class="camera-panel">
        <video ref="video" playsinline muted></video>
      </div>

      <div class="tool-strip">
        <button type="button" :class="{ active: mode === 'scale' }" @click="mode = 'scale'; pendingPoints = []">
          <Ruler :size="17" />
          标尺
        </button>
        <button type="button" :class="{ active: mode === 'silique' }" @click="mode = 'silique'; pendingPoints = []">
          <Ruler :size="17" />
          角果长度
        </button>
        <button type="button" :class="{ active: mode === 'seedRoi' }" @click="mode = 'seedRoi'; pendingPoints = []">
          <Square :size="17" />
          框选籽粒区
        </button>
        <button type="button" :class="{ active: mode === 'seedAdd' }" @click="mode = 'seedAdd'; pendingPoints = []">
          <PlusCircle :size="17" />
          补籽粒
        </button>
        <button type="button" :class="{ active: mode === 'seedRemove' }" @click="mode = 'seedRemove'; pendingPoints = []">
          <MinusCircle :size="17" />
          删籽粒
        </button>
        <button type="button" :disabled="!image || detecting" @click="autoDetectSeeds">
          <Play :size="17" />
          {{ detecting ? '分析中' : '生成候选点' }}
        </button>
        <button type="button" :disabled="!image" @click="recommendSeedParams">
          <Ruler :size="17" />
          推荐参数
        </button>
        <button type="button" @click="clearSeeds">
          <Trash2 :size="17" />
          清空籽粒
        </button>
      </div>

      <div class="canvas-panel">
        <canvas v-show="image" ref="canvas" @click="handleCanvasClick"></canvas>
        <div v-if="!image" class="empty-canvas">
          <UploadCloud :size="42" />
          <span>拍摄或上传角果/籽粒照片开始测量</span>
        </div>
      </div>

      <div class="quick-confirm">
        <div class="quick-values">
          <label>保存长度 mm<input v-model.number="state.manualSiliqueLengthMm" type="number" min="0" step="0.01" placeholder="手动输入" /></label>
          <label>保存籽粒数<input v-model.number="state.manualSeedCount" type="number" min="0" step="1" placeholder="手动输入" /></label>
          <label>粒数/cm<input :value="confirmedMetrics.seedsPerCm || ''" readonly placeholder="自动计算" /></label>
        </div>
        <div class="button-row compact-row">
          <button type="button" :disabled="!metrics.siliqueLengthMm" @click="confirmSiliqueLength">
            <Save :size="18" />
            确认长度
          </button>
          <button type="button" :disabled="!image" @click="confirmSeedCount">
            <Save :size="18" />
            确认籽粒
          </button>
          <button class="primary" type="button" :disabled="savingRecord" @click="saveRecord">
            <Save :size="18" />
            {{ savingRecord ? '保存中' : '保存并上传' }}
          </button>
        </div>
        <p class="hint status">{{ saveStatus }}</p>
      </div>

      <div class="metric-grid">
        <div><span>比例尺</span><strong>{{ metrics.mmPerPixel || '-' }} mm/px</strong></div>
        <div><span>角果长度</span><strong>{{ metrics.siliqueLengthMm || '-' }} mm</strong></div>
        <div><span>籽粒数</span><strong>{{ metrics.seedCount }}</strong></div>
        <div><span>粒数每 cm</span><strong>{{ metrics.seedsPerCm || '-' }}</strong></div>
        <div><span>保存长度</span><strong>{{ confirmedMetrics.siliqueLengthMm || '-' }} mm</strong></div>
        <div><span>保存籽粒</span><strong>{{ confirmedMetrics.seedCount !== '' ? confirmedMetrics.seedCount : '-' }}</strong></div>
      </div>
    </div>

    <aside class="side-area">
      <section class="panel">
        <button class="panel-toggle" type="button" @click="showRecordPanel = !showRecordPanel">
          <span>角果记录</span>
          <span>{{ showRecordPanel ? '收起' : '展开' }}</span>
        </button>
        <div v-show="showRecordPanel">
        <div class="form-grid">
          <label>材料类型<input v-model="form.genotype" placeholder="例如 WT / mutant" /></label>
          <label class="wide">编号示范<input v-model="sampleTemplate" placeholder="例如 G0001" @change="applySampleTemplate" /></label>
          <label class="wide">
            样品编号
            <select v-model="form.sampleId">
              <option v-for="sample in sampleOptions" :key="sample" :value="sample">{{ sample }}</option>
            </select>
          </label>
          <label>
            重复
            <select v-model="form.replicate">
              <option v-for="replicate in replicateOptions" :key="replicate" :value="replicate">{{ replicate }}</option>
            </select>
          </label>
          <label>角果编号<input v-model="form.siliqueId" placeholder="S001" /></label>
          <label>
            图片质量
            <select v-model="form.quality">
              <option value="good">可训练</option>
              <option value="blurry">模糊</option>
              <option value="reflective">反光</option>
              <option value="overlapping">籽粒粘连</option>
              <option value="edge_interference">边缘干扰</option>
              <option value="exclude">不进训练集</option>
            </select>
          </label>
          <label class="wide">云端链接<input v-model="form.cloudUrl" placeholder="云端上传接入后自动填写" /></label>
          <label class="wide">备注<textarea v-model="form.notes" rows="3"></textarea></label>
        </div>
        <div class="button-row">
          <button type="button" :disabled="!image" @click="exportAnnotatedImage">
            <Download :size="18" />
            标注图
          </button>
        </div>
        </div>
      </section>

      <section class="panel">
        <button class="panel-toggle" type="button" @click="showCloudPanel = !showCloudPanel">
          <span>Supabase 云同步</span>
          <span>{{ showCloudPanel ? '收起' : '展开' }}</span>
        </button>
        <div v-show="showCloudPanel">
          <label class="range-field">
            Supabase URL
            <input v-model="supabaseSettings.url" placeholder="https://xxxx.supabase.co" />
          </label>
          <label class="range-field">
            Supabase anon key
            <input v-model="supabaseSettings.anonKey" placeholder="只填写 anon public key，不要填 service_role key" />
          </label>
          <label class="range-field">
            Storage bucket
            <input v-model="supabaseSettings.bucket" placeholder="rapeseed-images" />
          </label>
          <div v-if="currentCloudUser" class="cloud-user-row">
            <span>当前账号：{{ currentCloudUser }}</span>
            <button type="button" :disabled="cloudSyncing" @click="logoutCloudUser">退出</button>
          </div>
          <template v-else>
            <label class="range-field">
              邮箱
              <input v-model="authForm.email" type="email" placeholder="name@example.com" />
            </label>
            <label class="range-field">
              密码
              <input v-model="authForm.password" type="password" placeholder="至少 6 位" />
            </label>
            <div class="button-row compact-row">
              <button type="button" :disabled="cloudSyncing" @click="loginCloudUser">登录</button>
              <button type="button" :disabled="cloudSyncing" @click="registerCloudUser">注册</button>
            </div>
          </template>
          <label class="checkbox-row">
            <input v-model="supabaseSettings.enabled" type="checkbox" />
            <span>启用自动云同步</span>
          </label>
          <div class="button-row compact-row">
            <button type="button" :disabled="cloudSyncing" @click="testCloudSync">
              {{ cloudSyncing ? '连接中' : '测试连接' }}
            </button>
            <button type="button" :disabled="cloudSyncing || !records.length" @click="pushRecordsToCloud">
              同步到云端
            </button>
            <button type="button" :disabled="cloudSyncing" @click="pullRecordsFromCloud">
              从云端拉取
            </button>
          </div>
          <p class="hint status">{{ cloudStatus }}</p>
          <p class="hint">云同步会上传角果照片、籽粒照片和校正点位。手机和电脑填写同一个 Supabase 项目后，可以看到同一批数据。</p>
        </div>
      </section>

      <section class="panel">
        <button class="panel-toggle" type="button" @click="showDetectPanel = !showDetectPanel">
          <span>自动识别参数</span>
          <span>{{ showDetectPanel ? '收起' : '展开' }}</span>
        </button>
        <div v-show="showDetectPanel">
        <label class="range-field">
          识别服务地址
          <input v-model="serviceUrl" placeholder="例如 https://your-seed-api.example.com；留空用浏览器算法" />
        </label>
        <div class="button-row compact-row">
          <button type="button" :disabled="!serviceUrl.trim() || syncing" @click="pullRecordsFromServer">
            {{ syncing ? '同步中' : '从电脑同步' }}
          </button>
          <button type="button" :disabled="!serviceUrl.trim() || syncing || !records.length" @click="pushRecordsToServer">
            同步到电脑
          </button>
        </div>
        <p class="hint status">{{ syncStatus }}</p>
        <label class="range-field">
          籽粒颜色
          <select v-model="foregroundMode">
            <option value="auto">自动判断</option>
            <option value="light">比背景亮</option>
            <option value="dark">比背景暗</option>
          </select>
        </label>
        <label class="range-field">亮度阈值 {{ threshold }}<input v-model.number="threshold" type="range" min="10" max="245" /></label>
        <label class="range-field">最小面积 {{ minSeedArea }}<input v-model.number="minSeedArea" type="range" min="2" max="300" /></label>
        <label class="range-field">最大面积 {{ maxSeedArea }}<input v-model.number="maxSeedArea" type="range" min="100" max="5000" /></label>
        <label class="range-field">圆整度 {{ minRoundness }}<input v-model.number="minRoundness" type="range" min="0.05" max="0.9" step="0.05" /></label>
        <label class="range-field">圆度 {{ minCircularity }}<input v-model.number="minCircularity" type="range" min="0.05" max="0.9" step="0.05" /></label>
        <label class="range-field">最大长宽比 {{ maxAspect }}<input v-model.number="maxAspect" type="range" min="1.2" max="6" step="0.1" /></label>
        <label class="range-field">边缘忽略 {{ Math.round(edgeMarginRatio * 100) }}%<input v-model.number="edgeMarginRatio" type="range" min="0" max="0.15" step="0.01" /></label>
        <label class="range-field">粘连倍数 {{ touchingAreaMultiplier }}<input v-model.number="touchingAreaMultiplier" type="range" min="1.5" max="6" step="0.5" /></label>
        <div class="button-row compact-row">
          <button type="button" :disabled="!image" @click="recommendSeedParams">
            <Ruler :size="18" />
            根据当前照片推荐参数
          </button>
        </div>
        <p class="hint status">{{ detectStatus }}</p>
        <p class="hint">先点“框选籽粒区”框住籽粒，再生成候选点。参数会自动保存到本机；候选点不是最终结果，需要用“补籽粒/删籽粒”人工确认。</p>
        </div>
      </section>

      <section class="panel">
        <button class="panel-toggle" type="button" @click="showTrainingPanel = !showTrainingPanel">
          <span>训练数据</span>
          <span>{{ showTrainingPanel ? '收起' : '展开' }}</span>
        </button>
        <div v-show="showTrainingPanel">
        <div class="mini-stats">
          <div><span>已校正图片</span><strong>{{ trainingStats.correctedImages }}</strong></div>
          <div><span>籽粒标注</span><strong>{{ trainingStats.seedAnnotations }}</strong></div>
          <div><span>平均粒数</span><strong>{{ trainingStats.averageSeeds }}</strong></div>
          <div><span>可导出 YOLO</span><strong>{{ trainingStats.yoloReady }}</strong></div>
          <div><span>低质图片</span><strong>{{ trainingStats.lowQuality }}</strong></div>
        </div>
        <p class="hint">保存记录时会同时保存原图、自动点位、删除点位和最终校正点位。训练检测模型时以最终校正点位为准。</p>
        <div class="button-row">
          <button class="primary" type="button" :disabled="!serviceUrl.trim() || !trainingStats.yoloReady || training" @click="startYoloTraining">
            <Play :size="18" />
            {{ training ? '训练中' : '用本机记录训练' }}
          </button>
          <button type="button" :disabled="!serviceUrl.trim() || training" @click="startStoredYoloTraining">
            <Play :size="18" />
            用电脑同步数据训练
          </button>
          <button type="button" :disabled="!serviceUrl.trim() || !currentCloudUser || training" @click="startCloudYoloTraining">
            <Play :size="18" />
            用 Supabase 云端数据训练
          </button>
        </div>
        <p class="hint status">{{ trainStatus }}</p>
        <pre v-if="trainJob?.logTail" class="log-box">{{ trainJob.logTail }}</pre>
        <p v-if="trainJob?.metrics?.bestWeights" class="hint">最佳模型：{{ trainJob.metrics.bestWeights }}</p>
        </div>
      </section>
    </aside>
  </section>

  <section class="panel results-panel">
    <div class="panel-heading">
      <div>
        <div class="panel-title">角果/籽粒结果</div>
        <p>{{ records.length }} 条记录</p>
      </div>
      <div class="button-row">
        <button type="button" @click="showResultsPanel = !showResultsPanel">{{ showResultsPanel ? '收起结果' : '查看结果' }}</button>
        <button type="button" :disabled="!records.length" @click="exportSiliqueRecords(records)">导出 Excel</button>
        <button type="button" :disabled="!records.length" @click="exportSiliqueTrainingData(records)">导出训练数据</button>
        <button type="button" :disabled="!trainingStats.yoloReady" @click="exportSiliqueYoloDataset(records)">导出 YOLO 数据集</button>
        <button class="ghost" type="button" :disabled="!records.length" @click="clearRecords">清空</button>
      </div>
    </div>
    <div v-show="showResultsPanel" class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>材料类型</th>
            <th>样品编号</th>
            <th>重复</th>
            <th>角果编号</th>
            <th>长度 mm</th>
            <th>籽粒数</th>
            <th>粒数/cm</th>
            <th>质量</th>
            <th>方式</th>
            <th>日期</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!records.length"><td colspan="11">暂无记录</td></tr>
          <tr v-for="record in records" :key="record.id">
            <td v-if="editingRecordId === record.id"><input v-model="editDraft.genotype" class="table-input" /></td>
            <td v-else>{{ record.genotype }}</td>
            <td v-if="editingRecordId === record.id"><input v-model="editDraft.sampleId" class="table-input" /></td>
            <td v-else>{{ record.sampleId }}</td>
            <td v-if="editingRecordId === record.id">
              <select v-model="editDraft.replicate" class="table-input">
                <option v-for="replicate in replicateOptions" :key="replicate" :value="replicate">{{ replicate }}</option>
              </select>
            </td>
            <td v-else>{{ record.replicate }}</td>
            <td v-if="editingRecordId === record.id"><input v-model="editDraft.siliqueId" class="table-input" /></td>
            <td v-else>{{ record.siliqueId }}</td>
            <td v-if="editingRecordId === record.id"><input v-model.number="editDraft.siliqueLengthMm" class="table-input" type="number" min="0" step="0.01" /></td>
            <td v-else>{{ record.siliqueLengthMm }}</td>
            <td v-if="editingRecordId === record.id"><input v-model.number="editDraft.seedCount" class="table-input" type="number" min="0" step="1" /></td>
            <td v-else>{{ record.seedCount }}</td>
            <td>{{ editingRecordId === record.id && numberOrEmpty(editDraft.siliqueLengthMm) !== '' && numberOrEmpty(editDraft.seedCount) !== '' ? round(Number(editDraft.seedCount) / (Number(editDraft.siliqueLengthMm) / 10), 2) : record.seedsPerCm }}</td>
            <td v-if="editingRecordId === record.id">
              <select v-model="editDraft.quality" class="table-input">
                <option value="good">可训练</option>
                <option value="blurry">模糊</option>
                <option value="reflective">反光</option>
                <option value="overlapping">籽粒粘连</option>
                <option value="edge_interference">边缘干扰</option>
                <option value="exclude">不进训练集</option>
              </select>
            </td>
            <td v-else>{{ record.quality || '-' }}</td>
            <td>{{ record.method }}</td>
            <td>{{ record.measuredAt }}</td>
            <td>
              <div class="table-actions">
                <template v-if="editingRecordId === record.id">
                  <button type="button" @click="saveEditRecord(record.id)">保存</button>
                  <button type="button" class="ghost" @click="cancelEditRecord">取消</button>
                </template>
                <template v-else>
                  <button type="button" @click="startEditRecord(record)">编辑</button>
                  <button type="button" class="danger" @click="deleteRecord(record.id)">删除</button>
                </template>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
