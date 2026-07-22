<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { Camera, CheckCircle2, Download, ImagePlus, MinusCircle, Play, PlusCircle, Ruler, Save, Square, Trash2, Video } from '@lucide/vue'
import { distance, round } from '../utils/geometry'
import { createId } from '../utils/id'
import { exportViviparyRecords } from '../utils/exportExcel'
import { loadViviparyRecords, loadViviparyRecordsFull, saveViviparyRecords } from '../utils/viviparyStorage'

const canvas = ref(null)
const video = ref(null)
const image = ref(null)
const imageName = ref('')
const stream = ref(null)
const cameraOn = ref(false)
const mode = ref('scale')
const pending = ref([])
const detecting = ref(false)
const status = ref('拍照后先校准标尺，再框选种子区域。')
const records = ref(loadViviparyRecords())
const serviceUrl = ref(localStorage.getItem('rapeseed-pheno-tool:seed-service-url') || '')
const scaleLengthMm = ref(10)
const minProtrusionMm = ref(0.5)
const state = reactive({ scale: null, mmPerPixel: null, roi: null, seedPoints: [] })
const form = reactive({ materialType: '', sampleId: '', replicate: '1', notes: '' })

const metrics = computed(() => {
  const vivipary = state.seedPoints.filter((point) => point.vivipary)
  const lengths = vivipary.map((point) => Number(point.protrusionLengthMm)).filter(Number.isFinite)
  return {
    totalSeeds: state.seedPoints.length,
    viviparyCount: vivipary.length,
    viviparyRate: state.seedPoints.length ? round(vivipary.length / state.seedPoints.length * 100, 2) : 0,
    averageProtrusionMm: lengths.length ? round(lengths.reduce((sum, value) => sum + value, 0) / lengths.length, 3) : '',
    maxProtrusionMm: lengths.length ? round(Math.max(...lengths), 3) : '',
  }
})

const canSave = computed(() => form.sampleId.trim() && image.value && state.seedPoints.length)

onMounted(async () => {
  records.value = await loadViviparyRecordsFull()
})

onBeforeUnmount(stopCamera)

async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) {
    status.value = '当前浏览器不支持直接调用摄像头，请使用“拍照上传”。'
    return
  }
  stream.value = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
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
  imageName.value = `${form.sampleId || 'vivipary'}-${Date.now()}.jpg`
  await loadImage(snap.toDataURL('image/jpeg', 0.92))
}

function loadFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  imageName.value = file.name
  const url = URL.createObjectURL(file)
  loadImage(url, () => URL.revokeObjectURL(url))
  event.target.value = ''
}

function loadImage(url, cleanup) {
  return new Promise((resolve) => {
    const img = new Image()
    img.onload = async () => {
      image.value = img
      resetMeasurement()
      await nextTick()
      resizeCanvas()
      draw()
      cleanup?.()
      resolve()
    }
    img.src = url
  })
}

function resizeCanvas() {
  const holder = canvas.value?.parentElement
  if (!holder || !image.value) return
  canvas.value.width = Math.min(image.value.width, holder.clientWidth)
  canvas.value.height = canvas.value.width * image.value.height / image.value.width
}

function resetMeasurement() {
  state.scale = null
  state.mmPerPixel = null
  state.roi = null
  state.seedPoints = []
  pending.value = []
  status.value = '请先点击“标尺”，再点标尺两端。'
}

function pointFromEvent(event) {
  const rect = canvas.value.getBoundingClientRect()
  return { x: (event.clientX - rect.left) * canvas.value.width / rect.width, y: (event.clientY - rect.top) * canvas.value.height / rect.height }
}

function handleCanvas(event) {
  if (!image.value) return
  const point = pointFromEvent(event)
  if (mode.value === 'toggle') return toggleNearest(point)
  if (mode.value === 'delete') return deleteNearest(point)
  if (mode.value === 'addVivipary' || mode.value === 'addNormal') {
    state.seedPoints.push({ ...point, tipX: point.x, tipY: point.y, vivipary: mode.value === 'addVivipary', protrusionLengthMm: mode.value === 'addVivipary' ? minProtrusionMm.value : 0, source: 'manual_add' })
    draw()
    return
  }
  pending.value.push(point)
  if (pending.value.length < 2) return draw()
  if (mode.value === 'scale') {
    const pixels = distance(pending.value[0], pending.value[1])
    state.scale = { points: [...pending.value], pixels }
    state.mmPerPixel = Number(scaleLengthMm.value) / pixels
    status.value = `比例尺已校准：${round(state.mmPerPixel, 4)} mm/px。现在框选种子区域。`
  } else if (mode.value === 'roi') {
    const [a, b] = pending.value
    state.roi = { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(a.x - b.x), height: Math.abs(a.y - b.y) }
    status.value = '种子区域已确定，可以开始自动检测。'
  }
  pending.value = []
  draw()
}

function nearestIndex(point) {
  let best = -1
  let bestDistance = 28
  state.seedPoints.forEach((seed, index) => {
    const value = distance(seed, point)
    if (value < bestDistance) { best = index; bestDistance = value }
  })
  return best
}

function toggleNearest(point) {
  const index = nearestIndex(point)
  if (index < 0) return
  const seed = state.seedPoints[index]
  seed.vivipary = !seed.vivipary
  if (seed.vivipary && !Number(seed.protrusionLengthMm)) seed.protrusionLengthMm = minProtrusionMm.value
  seed.source = 'manual_corrected'
  draw()
}

function deleteNearest(point) {
  const index = nearestIndex(point)
  if (index >= 0) state.seedPoints.splice(index, 1)
  draw()
}

async function autoDetect() {
  if (!image.value || !state.mmPerPixel) {
    status.value = '自动检测前必须先完成标尺校准。'
    return
  }
  if (!serviceUrl.value.trim()) {
    status.value = '请在识别设置中填写后端地址，例如电脑端 http://127.0.0.1:8000。'
    return
  }
  detecting.value = true
  status.value = '正在分析种子轮廓和突出长度...'
  localStorage.setItem('rapeseed-pheno-tool:seed-service-url', serviceUrl.value.trim())
  try {
    const response = await fetch(`${serviceUrl.value.trim().replace(/\/$/, '')}/api/vivipary-candidates`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        imageDataUrl: cleanImageDataUrl(true),
        roi: fullResolutionRoi(),
        mmPerPixel: fullResolutionMmPerPixel(),
        minProtrusionMm: minProtrusionMm.value,
        foregroundMode: 'auto',
      }),
    })
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    const result = await response.json()
    state.seedPoints = (result.points || []).map(pointFromFullResolution)
    const warning = result.warnings?.length ? ` ${result.warnings.join(' ')}` : ''
    status.value = `识别到 ${result.count} 粒，其中疑似胎萌 ${result.viviparyCount} 粒。${warning} 点击标记可切换结果。`
    draw()
  } catch (error) {
    status.value = `胎萌检测失败：${error.message}`
  } finally {
    detecting.value = false
  }
}

function imageScale() {
  return {
    x: (image.value?.naturalWidth || canvas.value.width) / canvas.value.width,
    y: (image.value?.naturalHeight || canvas.value.height) / canvas.value.height,
  }
}

function fullResolutionRoi() {
  if (!state.roi) return null
  const scale = imageScale()
  return { x: state.roi.x * scale.x, y: state.roi.y * scale.y, width: state.roi.width * scale.x, height: state.roi.height * scale.y }
}

function fullResolutionMmPerPixel() {
  return state.mmPerPixel / imageScale().x
}

function pointFromFullResolution(point) {
  const scale = imageScale()
  return {
    ...point,
    x: point.x / scale.x,
    y: point.y / scale.y,
    tipX: Number.isFinite(point.tipX) ? point.tipX / scale.x : null,
    tipY: Number.isFinite(point.tipY) ? point.tipY / scale.y : null,
    bodyRadiusPx: Number.isFinite(point.bodyRadiusPx) ? point.bodyRadiusPx / scale.x : point.bodyRadiusPx,
    protrusionLengthPx: Number.isFinite(point.protrusionLengthPx) ? point.protrusionLengthPx / scale.x : point.protrusionLengthPx,
  }
}

function pointToFullResolution(point) {
  const scale = imageScale()
  return {
    ...point,
    x: point.x * scale.x,
    y: point.y * scale.y,
    tipX: Number.isFinite(point.tipX) ? point.tipX * scale.x : null,
    tipY: Number.isFinite(point.tipY) ? point.tipY * scale.y : null,
    bodyRadiusPx: Number.isFinite(point.bodyRadiusPx) ? point.bodyRadiusPx * scale.x : point.bodyRadiusPx,
    protrusionLengthPx: Number.isFinite(point.protrusionLengthPx) ? point.protrusionLengthPx * scale.x : point.protrusionLengthPx,
  }
}

function fullResolutionScale() {
  if (!state.scale) return null
  const scale = imageScale()
  return {
    ...state.scale,
    pixels: state.scale.pixels * scale.x,
    points: state.scale.points.map((point) => ({ x: point.x * scale.x, y: point.y * scale.y })),
  }
}

function cleanImageDataUrl(fullResolution = false) {
  const clean = document.createElement('canvas')
  clean.width = fullResolution ? image.value.naturalWidth : canvas.value.width
  clean.height = fullResolution ? image.value.naturalHeight : canvas.value.height
  clean.getContext('2d').drawImage(image.value, 0, 0, clean.width, clean.height)
  return clean.toDataURL('image/jpeg', 0.92)
}

async function saveRecord() {
  if (!canSave.value) return
  const record = {
    id: createId(), materialType: form.materialType.trim(), sampleId: form.sampleId.trim(), replicate: form.replicate,
    notes: form.notes.trim(), imageName: imageName.value, imageDataUrl: cleanImageDataUrl(true), mmPerPixel: fullResolutionMmPerPixel(),
    scale: fullResolutionScale(), roi: fullResolutionRoi(), minProtrusionMm: Number(minProtrusionMm.value), seedPoints: state.seedPoints.map(pointToFullResolution),
    ...metrics.value, measuredAt: new Date().toISOString().slice(0, 10), createdAt: new Date().toISOString(),
  }
  records.value = [record, ...records.value]
  await saveViviparyRecords(records.value)
  status.value = `已保存 ${form.sampleId}：胎萌 ${record.viviparyCount}/${record.totalSeeds} 粒。`
}

async function deleteRecord(id) {
  records.value = records.value.filter((record) => record.id !== id)
  await saveViviparyRecords(records.value)
}

function draw() {
  if (!canvas.value) return
  const ctx = canvas.value.getContext('2d')
  ctx.clearRect(0, 0, canvas.value.width, canvas.value.height)
  if (!image.value) return
  ctx.drawImage(image.value, 0, 0, canvas.value.width, canvas.value.height)
  drawLine(ctx, state.scale?.points, '#2563eb')
  if (state.roi) {
    ctx.strokeStyle = '#7c3aed'; ctx.lineWidth = 2; ctx.setLineDash([8, 6]); ctx.strokeRect(state.roi.x, state.roi.y, state.roi.width, state.roi.height); ctx.setLineDash([])
  }
  state.seedPoints.forEach((seed, index) => {
    const color = seed.vivipary ? '#ea580c' : seed.source === 'dense-seed-estimate' ? '#ca8a04' : '#15803d'
    ctx.strokeStyle = color; ctx.lineWidth = 2
    ctx.beginPath(); ctx.arc(seed.x, seed.y, 9, 0, Math.PI * 2); ctx.stroke()
    if (seed.vivipary && Number.isFinite(seed.tipX)) { ctx.beginPath(); ctx.moveTo(seed.x, seed.y); ctx.lineTo(seed.tipX, seed.tipY); ctx.stroke() }
    ctx.fillStyle = color; ctx.font = '12px sans-serif'; ctx.fillText(String(index + 1), seed.x + 11, seed.y - 8)
  })
  pending.value.forEach((point) => { ctx.fillStyle = '#111827'; ctx.beginPath(); ctx.arc(point.x, point.y, 4, 0, Math.PI * 2); ctx.fill() })
}

function drawLine(ctx, points, color) {
  if (!points?.length) return
  ctx.strokeStyle = color; ctx.lineWidth = 3; ctx.beginPath(); ctx.moveTo(points[0].x, points[0].y); ctx.lineTo(points[1].x, points[1].y); ctx.stroke()
}
</script>

<template>
  <section class="vivipary-layout">
    <div class="measure-shell">
      <div class="toolbar">
        <button class="primary" type="button" @click="startCamera"><Video :size="18" />打开相机</button>
        <button type="button" :disabled="!cameraOn" @click="capturePhoto"><Camera :size="18" />拍照</button>
        <label class="native-file"><span><ImagePlus :size="18" />拍照上传</span><input type="file" accept="image/*" capture="environment" @change="loadFile" /></label>
        <label class="native-file"><span><ImagePlus :size="18" />从相册选择</span><input type="file" accept="image/*" @change="loadFile" /></label>
      </div>
      <div v-if="cameraOn" class="camera-panel"><video ref="video" playsinline muted></video></div>
      <div class="tool-strip vivipary-tools">
        <button :class="{ active: mode === 'scale' }" type="button" @click="mode='scale'; pending=[]"><Ruler :size="17" />标尺</button>
        <button :class="{ active: mode === 'roi' }" type="button" @click="mode='roi'; pending=[]"><Square :size="17" />框选种子区</button>
        <button type="button" :disabled="!image || detecting" @click="autoDetect"><Play :size="17" />{{ detecting ? '分析中' : '自动检测' }}</button>
        <button :class="{ active: mode === 'toggle' }" type="button" @click="mode='toggle'"><CheckCircle2 :size="17" />切换胎萌</button>
        <button :class="{ active: mode === 'addVivipary' }" type="button" @click="mode='addVivipary'"><PlusCircle :size="17" />补胎萌粒</button>
        <button :class="{ active: mode === 'addNormal' }" type="button" @click="mode='addNormal'"><PlusCircle :size="17" />补正常粒</button>
        <button :class="{ active: mode === 'delete' }" type="button" @click="mode='delete'"><MinusCircle :size="17" />删除点</button>
      </div>
      <div class="canvas-panel">
        <canvas v-show="image" ref="canvas" @click="handleCanvas"></canvas>
        <div v-if="!image" class="empty-canvas"><ImagePlus :size="42" /><span>拍摄或上传平铺种子照片</span></div>
      </div>
      <div class="quick-confirm">
        <div class="metric-grid vivipary-metrics">
          <div><span>总种子数</span><strong>{{ metrics.totalSeeds }}</strong></div>
          <div><span>胎萌数</span><strong>{{ metrics.viviparyCount }}</strong></div>
          <div><span>胎萌率</span><strong>{{ metrics.viviparyRate }}%</strong></div>
          <div><span>平均突出</span><strong>{{ metrics.averageProtrusionMm || '-' }} mm</strong></div>
          <div><span>最大突出</span><strong>{{ metrics.maxProtrusionMm || '-' }} mm</strong></div>
        </div>
        <p class="hint status">{{ status }}</p>
      </div>
    </div>

    <aside class="side-area">
      <section class="panel">
        <div class="panel-title">胎萌记录</div>
        <div class="form-grid">
          <label>材料类型<input v-model="form.materialType" /></label>
          <label>样品编号<input v-model="form.sampleId" /></label>
          <label>重复<select v-model="form.replicate"><option v-for="n in 9" :key="n" :value="String(n)">{{ n }}</option></select></label>
          <label>标尺长度 mm<input v-model.number="scaleLengthMm" type="number" min="0.1" step="0.1" /></label>
          <label class="wide">胎萌阈值 mm<input v-model.number="minProtrusionMm" type="number" min="0.05" max="20" step="0.05" /></label>
          <label class="wide">备注<textarea v-model="form.notes" rows="2"></textarea></label>
        </div>
        <button class="primary full-action" type="button" :disabled="!canSave" @click="saveRecord"><Save :size="18" />保存胎萌记录</button>
      </section>
      <section class="panel">
        <div class="panel-title">识别设置</div>
        <label class="range-field">识别服务地址<input v-model="serviceUrl" placeholder="http://127.0.0.1:8000" /></label>
        <p class="hint">橙色表示疑似胎萌，绿色表示轮廓清楚的正常种子，黄色表示密集区估算点。点击“切换胎萌”后再点标记即可人工纠正。</p>
        <details v-if="metrics.viviparyCount" class="vivipary-details">
          <summary>胎萌粒突出长度（{{ metrics.viviparyCount }} 粒）</summary>
          <label v-for="(seed, index) in state.seedPoints" v-show="seed.vivipary" :key="`${index}-${seed.x}-${seed.y}`">
            <span>第 {{ index + 1 }} 粒</span>
            <input v-model.number="seed.protrusionLengthMm" type="number" min="0" step="0.05" />
            <span>mm</span>
          </label>
        </details>
      </section>
    </aside>
  </section>

  <section class="panel results-panel">
    <div class="panel-heading"><div><div class="panel-title">胎萌测定结果</div><p>{{ records.length }} 条记录</p></div><button type="button" :disabled="!records.length" @click="exportViviparyRecords(records)"><Download :size="18" />导出 Excel</button></div>
    <div class="table-wrap"><table class="vivipary-table"><thead><tr><th>材料类型</th><th>样品编号</th><th>重复</th><th>总数</th><th>胎萌数</th><th>胎萌率</th><th>平均突出</th><th>日期</th><th>操作</th></tr></thead><tbody>
      <tr v-if="!records.length"><td colspan="9">暂无胎萌记录</td></tr>
      <tr v-for="record in records" :key="record.id"><td>{{ record.materialType }}</td><td>{{ record.sampleId }}</td><td>{{ record.replicate }}</td><td>{{ record.totalSeeds }}</td><td>{{ record.viviparyCount }}</td><td>{{ record.viviparyRate }}%</td><td>{{ record.averageProtrusionMm || '-' }} mm</td><td>{{ record.measuredAt }}</td><td><button class="danger" type="button" @click="deleteRecord(record.id)"><Trash2 :size="16" />删除</button></td></tr>
    </tbody></table></div>
  </section>
</template>
