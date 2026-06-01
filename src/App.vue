<script setup>
import { computed, reactive, ref } from 'vue'
import { Download, Save } from '@lucide/vue'
import ImageMeasure from './components/ImageMeasure.vue'
import ResultTable from './components/ResultTable.vue'
import SampleForm from './components/SampleForm.vue'
import SiliqueMeasure from './components/SiliqueMeasure.vue'
import { exportSamples } from './utils/exportExcel'
import { exportPlantTrainingData } from './utils/exportTrainingData'
import { loadSamples, saveSamples } from './utils/storage'

const form = reactive({
  sampleId: '',
  genotype: '',
  treatment: '',
  replicate: '',
  notes: '',
})

const metrics = ref({})
const samples = ref(loadSamples())
const activeModule = ref('plant')

const canSave = computed(() => form.sampleId.trim() && metrics.value.cmPerPixel)

function updateMetrics(value) {
  metrics.value = value
}

function saveCurrentSample() {
  if (!canSave.value) return
  const sample = {
    id: crypto.randomUUID(),
    sampleId: form.sampleId.trim(),
    genotype: form.genotype.trim(),
    treatment: form.treatment.trim(),
    replicate: form.replicate.trim(),
    notes: form.notes.trim(),
    metrics: { ...metrics.value },
    measuredAt: new Date().toISOString().slice(0, 10),
  }
  samples.value = [sample, ...samples.value]
  saveSamples(samples.value)
}

function clearSamples() {
  samples.value = []
  saveSamples(samples.value)
}
</script>

<template>
  <main class="app">
    <header class="app-header">
      <div>
        <h1>油菜表型拍照测量工具</h1>
        <p>整株表型、角果长度、籽粒计数、拍照记录和 Excel 导出。</p>
      </div>
      <div class="header-actions">
        <button
          type="button"
          :class="{ primary: activeModule === 'plant' }"
          @click="activeModule = 'plant'"
        >
          整株测量
        </button>
        <button
          type="button"
          :class="{ primary: activeModule === 'silique' }"
          @click="activeModule = 'silique'"
        >
          角果/籽粒
        </button>
        <button v-if="activeModule === 'plant'" class="primary" type="button" :disabled="!canSave" @click="saveCurrentSample">
          <Save :size="18" />
          保存样品
        </button>
        <button v-if="activeModule === 'plant'" type="button" :disabled="!samples.length" @click="exportSamples(samples)">
          <Download :size="18" />
          导出 Excel
        </button>
        <button v-if="activeModule === 'plant'" type="button" :disabled="!samples.length" @click="exportPlantTrainingData(samples)">
          <Download :size="18" />
          导出训练数据
        </button>
      </div>
    </header>

    <template v-if="activeModule === 'plant'">
      <div class="workspace">
        <section class="measure-area">
          <ImageMeasure @metrics-change="updateMetrics" />
        </section>
        <aside class="side-area">
          <SampleForm :form="form" />
          <section class="panel current-panel">
            <div class="panel-title">当前测量</div>
            <dl>
              <div><dt>比例尺</dt><dd>{{ metrics.cmPerPixel || '-' }} cm/px</dd></div>
              <div><dt>株高</dt><dd>{{ metrics.plantHeightCm || '-' }} cm</dd></div>
              <div><dt>主花序高</dt><dd>{{ metrics.inflorescenceHeightCm || '-' }} cm</dd></div>
              <div><dt>主花序长度</dt><dd>{{ metrics.inflorescenceLengthCm || '-' }} cm</dd></div>
              <div><dt>一级分枝数</dt><dd>{{ metrics.branchCount || 0 }}</dd></div>
              <div><dt>平均分支角</dt><dd>{{ metrics.branchAngleAvg || '-' }} deg</dd></div>
              <div><dt>最大分支角</dt><dd>{{ metrics.branchAngleMax || '-' }} deg</dd></div>
              <div><dt>最小分支角</dt><dd>{{ metrics.branchAngleMin || '-' }} deg</dd></div>
            </dl>
          </section>
        </aside>
      </div>

      <ResultTable :samples="samples" @clear="clearSamples" />
    </template>

    <SiliqueMeasure v-else />
  </main>
</template>
