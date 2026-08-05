<script setup lang="ts">
import { computed } from 'vue'
import type { VerificationEligibility } from '@/types/meetingVerification'
import { missingConditionLabels } from '@/utils/meetingVerification'

const props = defineProps<{ eligibility: VerificationEligibility; confirmed?: boolean; readonly?: boolean; dirty?: boolean; loading?: boolean }>()
const emit = defineEmits<{ confirm: []; submitAnalysis: []; save: [] }>()
const missing = computed(() => missingConditionLabels(props.eligibility.missing_conditions))
</script>

<template>
  <div class="action-bar">
    <div><strong>核验操作</strong><p v-if="missing.length" class="missing">{{ missing.join('；') }}</p><p v-else-if="confirmed" class="ready">核验已确认，可以提交 AI 分析。</p><p v-else>请完成两类问题后确认核验结果。</p></div>
    <div class="action-buttons"><el-button type="primary" plain :disabled="readonly || !dirty || loading" :loading="loading" @click="emit('save')">保存修改</el-button><el-button v-if="!confirmed" type="primary" :disabled="readonly || dirty || loading || !eligibility.can_confirm" :loading="loading" @click="emit('confirm')">确认核验</el-button><el-tooltip :disabled="!missing.length" :content="missing.join('；')" placement="top"><span><el-button type="success" :disabled="readonly || dirty || loading || !eligibility.can_submit_analysis || !confirmed" :loading="loading" @click="emit('submitAnalysis')">提交 AI 分析</el-button></span></el-tooltip></div>
  </div>
</template>

<style scoped>
.action-bar { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 16px 20px; border: 1px solid #cfe7df; border-radius: 12px; background: #f2faf7; }
.action-bar strong { color: #164d5f; }
.action-bar p { margin: 5px 0 0; color: #71888d; font-size: 12px; }
.action-bar .missing { color: #a66e30; }
.action-bar .ready { color: #14806f; }
.action-buttons { display: flex; gap: 8px; white-space: nowrap; }
@media (max-width: 650px) { .action-bar { align-items: stretch; flex-direction: column; } .action-buttons { flex-wrap: wrap; } }
</style>
