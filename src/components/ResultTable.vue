<script setup>
defineProps({
  samples: {
    type: Array,
    required: true,
  },
})

const emit = defineEmits(['clear'])
</script>

<template>
  <section class="panel results-panel">
    <div class="panel-heading">
      <div>
        <div class="panel-title">已保存结果</div>
        <p>{{ samples.length }} 个样品</p>
      </div>
      <button class="ghost" type="button" :disabled="!samples.length" @click="emit('clear')">清空</button>
    </div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>样品编号</th>
            <th>基因型</th>
            <th>处理</th>
            <th>重复</th>
            <th>株高</th>
            <th>主花序高</th>
            <th>主花序长度</th>
            <th>分枝数</th>
            <th>平均角</th>
            <th>日期</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!samples.length">
            <td colspan="10">暂无保存数据</td>
          </tr>
          <tr v-for="sample in samples" :key="sample.id">
            <td>{{ sample.sampleId }}</td>
            <td>{{ sample.genotype }}</td>
            <td>{{ sample.treatment }}</td>
            <td>{{ sample.replicate }}</td>
            <td>{{ sample.metrics.plantHeightCm }}</td>
            <td>{{ sample.metrics.inflorescenceHeightCm }}</td>
            <td>{{ sample.metrics.inflorescenceLengthCm }}</td>
            <td>{{ sample.metrics.branchCount }}</td>
            <td>{{ sample.metrics.branchAngleAvg }}</td>
            <td>{{ sample.measuredAt }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
