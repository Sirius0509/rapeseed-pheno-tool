<script setup>
import { computed, nextTick, reactive, ref, watch } from 'vue'
import {
  Activity,
  Binary,
  Download,
  ImagePlus,
  Leaf,
  Ruler,
  RotateCcw,
  Sprout,
  Trash2,
} from '@lucide/vue'
import { angleFromThreePoints, distance, round, verticalDistance } from '../utils/geometry'

const emit = defineEmits(['metrics-change'])

const canvas = ref(null)
const image = ref(null)
const imageName = ref('')
const mode = ref('scale')
const pendingPoints = ref([])
const scaleLength = ref(100)
const state = reactive({
  cmPerPixel: null,
  scale: null,
  plantHeight: null,
  inflorescenceHeight: null,
  inflorescenceLength: null,
  branchAngles: [],
  branchPoints: [],
})

const tools = [
  { id: 'scale', label: '标尺', icon: Ruler, points: 2 },
  { id: 'plantHeight', label: '株高', icon: Sprout, points: 2 },
  { id: 'inflorescenceHeight', label: '主花序高', icon: Leaf, points: 2 },
  { id: 'inflorescenceLength', label: '主花序长度', icon: Activity, points: 2 },
  { id: 'branchAngle', label: '分支角', icon: Binary, points: 3 },
  { id: 'branchCount', label: '分支计数', icon: null, points: 1 },
]

const metrics = computed(() => {
  const angles = state.branchAngles.map((item) => item.angle)
  return {
    cmPerPixel: round(state.cmPerPixel, 4),
    plantHeightCm: cmValue(state.plantHeight?.pixels),
    inflorescenceHeightCm: cmValue(state.inflorescenceHeight?.pixels),
    inflorescenceLengthCm: cmValue(state.inflorescenceLength?.pixels),
    branchCount: state.branchPoints.length,
    branchAngleAvg: angles.length ? round(angles.reduce((sum, value) => sum + value, 0) / angles.length) : '',
    branchAngleMax: angles.length ? round(Math.max(...angles)) : '',
    branchAngleMin: angles.length ? round(Math.min(...angles)) : '',
  }
})

watch(metrics, (value) => emit('metrics-change', value), { immediate: true })

function cmValue(pixels) {
  if (!state.cmPerPixel || !pixels) return ''
  return round(pixels * state.cmPerPixel)
}

function loadFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  imageName.value = file.name
  const url = URL.createObjectURL(file)
  const img = new Image()
  img.onload = async () => {
    image.value = img
    resetMeasurements()
    await nextTick()
    resizeCanvas()
    draw()
    URL.revokeObjectURL(url)
    event.target.value = ''
  }
  img.src = url
}

function resizeCanvas() {
  if (!canvas.value || !image.value) return
  const holder = canvas.value.parentElement
  const maxWidth = holder.clientWidth
  const ratio = image.value.height / image.value.width
  canvas.value.width = Math.min(image.value.width, maxWidth)
  canvas.value.height = canvas.value.width * ratio
}

function canvasPoint(event) {
  const rect = canvas.value.getBoundingClientRect()
  const scaleX = canvas.value.width / rect.width
  const scaleY = canvas.value.height / rect.height
  return {
    x: (event.clientX - rect.left) * scaleX,
    y: (event.clientY - rect.top) * scaleY,
  }
}

function handleCanvasClick(event) {
  if (!image.value) return
  const point = canvasPoint(event)
  if (mode.value === 'branchCount') {
    state.branchPoints.push(point)
    draw()
    return
  }

  pendingPoints.value.push(point)
  const tool = tools.find((item) => item.id === mode.value)
  if (pendingPoints.value.length === tool.points) {
    commitPoints(mode.value, [...pendingPoints.value])
    pendingPoints.value = []
  }
  draw()
}

function commitPoints(tool, points) {
  if (tool === 'scale') {
    const pixels = distance(points[0], points[1])
    state.scale = { points, pixels }
    state.cmPerPixel = Number(scaleLength.value) / pixels
  }
  if (tool === 'plantHeight') {
    state.plantHeight = { points, pixels: verticalDistance(points[0], points[1]) }
  }
  if (tool === 'inflorescenceHeight') {
    state.inflorescenceHeight = { points, pixels: verticalDistance(points[0], points[1]) }
  }
  if (tool === 'inflorescenceLength') {
    state.inflorescenceLength = { points, pixels: distance(points[0], points[1]) }
  }
  if (tool === 'branchAngle') {
    state.branchAngles.push({
      points,
      angle: round(angleFromThreePoints(points[0], points[1], points[2])),
    })
  }
}

function resetMeasurements() {
  state.cmPerPixel = null
  state.scale = null
  state.plantHeight = null
  state.inflorescenceHeight = null
  state.inflorescenceLength = null
  state.branchAngles = []
  state.branchPoints = []
  pendingPoints.value = []
  draw()
}

function undo() {
  if (pendingPoints.value.length) {
    pendingPoints.value.pop()
  } else if (mode.value === 'branchCount' && state.branchPoints.length) {
    state.branchPoints.pop()
  } else if (mode.value === 'branchAngle' && state.branchAngles.length) {
    state.branchAngles.pop()
  } else if (mode.value === 'plantHeight') {
    state.plantHeight = null
  } else if (mode.value === 'inflorescenceHeight') {
    state.inflorescenceHeight = null
  } else if (mode.value === 'inflorescenceLength') {
    state.inflorescenceLength = null
  } else if (mode.value === 'scale') {
    state.scale = null
    state.cmPerPixel = null
  }
  draw()
}

function draw() {
  if (!canvas.value) return
  const ctx = canvas.value.getContext('2d')
  ctx.clearRect(0, 0, canvas.value.width, canvas.value.height)
  if (!image.value) return

  ctx.drawImage(image.value, 0, 0, canvas.value.width, canvas.value.height)
  drawLine(ctx, state.scale, '#2563eb', '标尺')
  drawLine(ctx, state.plantHeight, '#16a34a', metrics.value.plantHeightCm ? `株高 ${metrics.value.plantHeightCm} cm` : '株高')
  drawLine(
    ctx,
    state.inflorescenceHeight,
    '#db2777',
    metrics.value.inflorescenceHeightCm ? `主花序高 ${metrics.value.inflorescenceHeightCm} cm` : '主花序高',
  )
  drawLine(
    ctx,
    state.inflorescenceLength,
    '#ea580c',
    metrics.value.inflorescenceLengthCm ? `主花序长 ${metrics.value.inflorescenceLengthCm} cm` : '主花序长',
  )
  state.branchAngles.forEach((item, index) => drawAngle(ctx, item, index + 1))
  state.branchPoints.forEach((point, index) => drawPoint(ctx, point, '#7c3aed', String(index + 1)))
  pendingPoints.value.forEach((point) => drawPoint(ctx, point, '#111827', ''))
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
  drawPoint(ctx, a, color, '')
  drawPoint(ctx, b, color, '')
  drawLabel(ctx, label, (a.x + b.x) / 2, (a.y + b.y) / 2, color)
}

function drawAngle(ctx, item, index) {
  const [a, b, c] = item.points
  ctx.strokeStyle = '#0891b2'
  ctx.lineWidth = 3
  ctx.beginPath()
  ctx.moveTo(a.x, a.y)
  ctx.lineTo(b.x, b.y)
  ctx.lineTo(c.x, c.y)
  ctx.stroke()
  ;[a, b, c].forEach((point) => drawPoint(ctx, point, '#0891b2', ''))
  drawLabel(ctx, `角${index}: ${item.angle} deg`, b.x + 8, b.y - 8, '#0891b2')
}

function drawPoint(ctx, point, color, text) {
  ctx.fillStyle = color
  ctx.beginPath()
  ctx.arc(point.x, point.y, 5, 0, Math.PI * 2)
  ctx.fill()
  if (text) drawLabel(ctx, text, point.x + 8, point.y - 8, color)
}

function drawLabel(ctx, text, x, y, color) {
  ctx.font = '14px system-ui'
  const width = ctx.measureText(text).width + 12
  ctx.fillStyle = 'rgba(255,255,255,0.88)'
  ctx.fillRect(x, y - 18, width, 22)
  ctx.fillStyle = color
  ctx.fillText(text, x + 6, y - 3)
}

function exportAnnotatedImage() {
  if (!canvas.value || !image.value) return
  const link = document.createElement('a')
  link.download = `${imageName.value || 'rapeseed-annotation'}.png`
  link.href = canvas.value.toDataURL('image/png')
  link.click()
}

window.addEventListener('resize', () => {
  resizeCanvas()
  draw()
})
</script>

<template>
  <section class="measure-shell">
    <div class="toolbar">
      <label class="native-file">
        <span><ImagePlus :size="18" /> 拍照上传</span>
        <input type="file" accept="image/*" capture="environment" @change="loadFile" />
      </label>
      <label class="native-file">
        <span><ImagePlus :size="18" /> 从相册选择</span>
        <input type="file" accept="image/*" @change="loadFile" />
      </label>
      <label class="scale-field">
        标尺长度 cm
        <input v-model.number="scaleLength" type="number" min="1" />
      </label>
      <button type="button" @click="undo">
        <RotateCcw :size="18" />
        撤销
      </button>
      <button type="button" @click="resetMeasurements">
        <Trash2 :size="18" />
        清空标注
      </button>
      <button type="button" :disabled="!image" @click="exportAnnotatedImage">
        <Download :size="18" />
        标注图
      </button>
    </div>

    <div class="tool-strip">
      <button
        v-for="tool in tools"
        :key="tool.id"
        type="button"
        :class="{ active: mode === tool.id }"
        @click="mode = tool.id; pendingPoints = []"
      >
        <component :is="tool.icon" v-if="tool.icon" :size="17" />
        <span v-else class="count-icon">#</span>
        {{ tool.label }}
      </button>
    </div>

    <div class="canvas-panel">
      <canvas v-show="image" ref="canvas" @click="handleCanvasClick"></canvas>
      <div v-if="!image" class="empty-canvas">
        <ImagePlus :size="42" />
        <span>上传一张包含标尺的油菜照片开始测量</span>
      </div>
    </div>

    <div class="metric-grid">
      <div><span>比例尺</span><strong>{{ metrics.cmPerPixel || '-' }} cm/px</strong></div>
      <div><span>株高</span><strong>{{ metrics.plantHeightCm || '-' }} cm</strong></div>
      <div><span>主花序高</span><strong>{{ metrics.inflorescenceHeightCm || '-' }} cm</strong></div>
      <div><span>主花序长度</span><strong>{{ metrics.inflorescenceLengthCm || '-' }} cm</strong></div>
      <div><span>一级分枝数</span><strong>{{ metrics.branchCount }}</strong></div>
      <div><span>平均分支角</span><strong>{{ metrics.branchAngleAvg || '-' }} deg</strong></div>
    </div>
  </section>
</template>
