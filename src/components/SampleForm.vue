<script setup>
defineProps({
  form: {
    type: Object,
    required: true,
  },
})
</script>

<template>
  <section class="panel">
    <div class="panel-title">样品信息</div>
    <div class="form-grid">
      <label>
        样品编号
        <input v-model="form.sampleId" placeholder="例如 R001" />
      </label>
      <label>
        基因型
        <input v-model="form.genotype" placeholder="材料/品种" />
      </label>
      <label>
        处理
        <input v-model="form.treatment" placeholder="处理方式" />
      </label>
      <label>
        重复
        <input v-model="form.replicate" placeholder="重复编号" />
      </label>
      <label class="wide">
        备注
        <textarea v-model="form.notes" rows="3" placeholder="可记录拍摄条件或异常情况"></textarea>
      </label>
    </div>
  </section>
</template>
