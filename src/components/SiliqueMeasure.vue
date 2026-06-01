<script setup>
import { computed, nextTick, reactive, ref } from 'vue'
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
import { exportSiliqueRecords } from '../utils/exportExcel'
import { loadSiliqueRecords, saveSiliqueRecords } from '../utils/storage'

const canvas = ref(null)
const fileInput = ref(null)
const video = ref(null)
const image = ref(null)
const imageName = ref('')
const cameraOn = ref(false)
const stream = ref(null)
const mode = ref('scale')
const pendingPoints = ref([])
const records = ref(loadSiliqueRecords())
const form = reactive({
  genotype: '',
  sampleId: '',
  replicate: '',
  siliqueId: 'S001',
  notes: '',
  cloudUrl: '',
})
const scaleLengthMm = ref(10)
const threshold = ref(65)
const minSeedArea = ref(18)
const maxSeedArea = ref(1400)
const state = reactive({
  mmPerPixel: null,
  scale: null,
  siliqueLine: null,
  seedPoints: [],
  detectedSeeds: [],
  previewMask: false,
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
      resetMeasurements()
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
    state.seedPoints.push(point)
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
    }
    pendingPoints.value = []
  }
  draw()
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
  if (bestIndex >= 0 && bestDistance < 35) state.seedPoints.splice(bestIndex, 1)
}

function autoDetectSeeds() {
  if (!canvas.value || !image.value) return
  const work = document.createElement('canvas')
  work.width = canvas.value.width
  work.height = canvas.value.height
  const ctx = work.getContext('2d')
  ctx.drawImage(image.value, 0, 0, work.width, work.height)
  const img = ctx.getImageData(0, 0, work.width, work.height)
  const points = connectedComponents(img, threshold.value, minSeedArea.value, maxSeedArea.value)
  state.detectedSeeds = points
  state.seedPoints = points
  draw()
}

function connectedComponents(imageData, cutoff, minArea, maxArea) {
  const { data, width, height } = imageData
  const seen = new Uint8Array(width * height)
  const result = []
  const stack = []
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const idx = y * width + x
      if (seen[idx] || !isForeground(data, idx, cutoff)) continue
      let area = 0
      let sumX = 0
      let sumY = 0
      stack.push(idx)
      seen[idx] = 1
      while (stack.length) {
        const current = stack.pop()
        const cx = current % width
        const cy = Math.floor(current / width)
        area += 1
        sumX += cx
        sumY += cy
        const neighbors = [current - 1, current + 1, current - width, current + width]
        for (const next of neighbors) {
          if (next < 0 || next >= seen.length || seen[next]) continue
          const nx = next % width
          if (Math.abs(nx - cx) > 1) continue
          if (!isForeground(data, next, cutoff)) continue
          seen[next] = 1
          stack.push(next)
        }
      }
      if (area >= minArea && area <= maxArea) {
        result.push({ x: sumX / area, y: sumY / area, area })
      }
    }
  }
  return result
}

function isForeground(data, index, cutoff) {
  const i = index * 4
  const r = data[i]
  const g = data[i + 1]
  const b = data[i + 2]
  const brightness = (r + g + b) / 3
  return brightness > cutoff
}

function resetMeasurements() {
  state.mmPerPixel = null
  state.scale = null
  state.siliqueLine = null
  state.seedPoints = []
  state.detectedSeeds = []
  pendingPoints.value = []
}

function clearSeeds() {
  state.seedPoints = []
  state.detectedSeeds = []
  draw()
}

function saveRecord() {
  if (!form.sampleId.trim() || !form.siliqueId.trim()) return
  const record = {
    id: crypto.randomUUID(),
    genotype: form.genotype.trim(),
    sampleId: form.sampleId.trim(),
    replicate: form.replicate.trim(),
    siliqueId: form.siliqueId.trim(),
    siliqueLengthMm: metrics.value.siliqueLengthMm,
    seedCount: metrics.value.seedCount,
    seedsPerCm: metrics.value.seedsPerCm,
    method: state.detectedSeeds.length ? '自动候选+人工确认' : '人工标注',
    imageName: imageName.value,
    cloudUrl: form.cloudUrl.trim(),
    notes: form.notes.trim(),
    measuredAt: new Date().toISOString().slice(0, 10),
  }
  records.value = [record, ...records.value]
  saveSiliqueRecords(records.value)
  incrementSiliqueId()
}

function incrementSiliqueId() {
  const match = form.siliqueId.match(/^(.*?)(\d+)$/)
  if (!match) return
  const next = String(Number(match[2]) + 1).padStart(match[2].length, '0')
  form.siliqueId = `${match[1]}${next}`
}

function clearRecords() {
  records.value = []
  saveSiliqueRecords(records.value)
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

function drawSeed(ctx, point, index) {
  ctx.strokeStyle = '#dc2626'
  ctx.lineWidth = 2
  ctx.beginPath()
  ctx.arc(point.x, point.y, 7, 0, Math.PI * 2)
  ctx.stroke()
  if (index <= 99) drawLabel(ctx, String(index), point.x + 7, point.y - 7, '#dc2626')
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
        <label class="upload-button">
          <ImagePlus :size="18" />
          上传照片
          <input ref="fileInput" type="file" accept="image/*" capture="environment" @change="loadFile" />
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
        <button type="button" :class="{ active: mode === 'seedAdd' }" @click="mode = 'seedAdd'; pendingPoints = []">
          <PlusCircle :size="17" />
          补籽粒
        </button>
        <button type="button" :class="{ active: mode === 'seedRemove' }" @click="mode = 'seedRemove'; pendingPoints = []">
          <MinusCircle :size="17" />
          删籽粒
        </button>
        <button type="button" :disabled="!image" @click="autoDetectSeeds">
          <Play :size="17" />
          自动数籽粒
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

      <div class="metric-grid">
        <div><span>比例尺</span><strong>{{ metrics.mmPerPixel || '-' }} mm/px</strong></div>
        <div><span>角果长度</span><strong>{{ metrics.siliqueLengthMm || '-' }} mm</strong></div>
        <div><span>籽粒数</span><strong>{{ metrics.seedCount }}</strong></div>
        <div><span>粒数每 cm</span><strong>{{ metrics.seedsPerCm || '-' }}</strong></div>
      </div>
    </div>

    <aside class="side-area">
      <section class="panel">
        <div class="panel-title">角果记录</div>
        <div class="form-grid">
          <label>材料编号<input v-model="form.genotype" placeholder="例如 G0001" /></label>
          <label>样品编号<input v-model="form.sampleId" placeholder="例如 P01" /></label>
          <label>重复<input v-model="form.replicate" placeholder="重复编号" /></label>
          <label>角果编号<input v-model="form.siliqueId" placeholder="S001" /></label>
          <label class="wide">云端链接<input v-model="form.cloudUrl" placeholder="云端上传接入后自动填写" /></label>
          <label class="wide">备注<textarea v-model="form.notes" rows="3"></textarea></label>
        </div>
        <div class="button-row">
          <button class="primary" type="button" :disabled="!form.sampleId || !form.siliqueId" @click="saveRecord">
            <Save :size="18" />
            保存记录
          </button>
          <button type="button" :disabled="!image" @click="exportAnnotatedImage">
            <Download :size="18" />
            标注图
          </button>
        </div>
      </section>

      <section class="panel">
        <div class="panel-title">自动识别参数</div>
        <label class="range-field">亮度阈值 {{ threshold }}<input v-model.number="threshold" type="range" min="10" max="245" /></label>
        <label class="range-field">最小面积 {{ minSeedArea }}<input v-model.number="minSeedArea" type="range" min="2" max="300" /></label>
        <label class="range-field">最大面积 {{ maxSeedArea }}<input v-model.number="maxSeedArea" type="range" min="100" max="5000" /></label>
        <p class="hint">适合深色背景、籽粒分散的照片。自动结果需要人工确认。</p>
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
        <button type="button" :disabled="!records.length" @click="exportSiliqueRecords(records)">导出 Excel</button>
        <button class="ghost" type="button" :disabled="!records.length" @click="clearRecords">清空</button>
      </div>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>材料编号</th>
            <th>样品编号</th>
            <th>角果编号</th>
            <th>长度 mm</th>
            <th>籽粒数</th>
            <th>粒数/cm</th>
            <th>方式</th>
            <th>日期</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!records.length"><td colspan="8">暂无记录</td></tr>
          <tr v-for="record in records" :key="record.id">
            <td>{{ record.genotype }}</td>
            <td>{{ record.sampleId }}</td>
            <td>{{ record.siliqueId }}</td>
            <td>{{ record.siliqueLengthMm }}</td>
            <td>{{ record.seedCount }}</td>
            <td>{{ record.seedsPerCm }}</td>
            <td>{{ record.method }}</td>
            <td>{{ record.measuredAt }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
