<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import type { VerificationQuestion, VerificationQuestionType } from '@/types/meetingVerification'

const props = defineProps<{ modelValue: boolean; question?: VerificationQuestion | null; type?: VerificationQuestionType; loading?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean]; save: [payload: { content: string; question?: VerificationQuestion; question_type: VerificationQuestionType }]; 'draft-change': [dirty: boolean] }>()
const form = reactive({ content: '' })
const isEdit = computed(() => Boolean(props.question))
const originalContent = ref('')
const isDraftDirty = computed(() => form.content.trim() !== originalContent.value.trim())
watch(() => props.modelValue, (value) => { if (value) { originalContent.value = props.question?.content ?? ''; form.content = originalContent.value; emit('draft-change', false) } else { originalContent.value = ''; form.content = ''; emit('draft-change', false) } })
watch(() => props.question, (value) => { if (props.modelValue) { originalContent.value = value?.content ?? ''; form.content = originalContent.value; emit('draft-change', false) } })
const submit = () => { const content = form.content.trim(); if (content && isDraftDirty.value) emit('save', { content, question: props.question ?? undefined, question_type: props.question?.question_type ?? props.type ?? 'cut_point' }) }
const emitDraft = () => emit('draft-change', form.content.trim() !== originalContent.value.trim())
defineExpose({ submit })
</script>

<template>
  <el-dialog :model-value="modelValue" :title="isEdit ? '编辑问题' : '新增问题'" width="520px" destroy-on-close @update:model-value="emit('update:modelValue', $event)">
    <el-form @submit.prevent="submit"><el-form-item label="问题内容" required><el-input v-model="form.content" type="textarea" :rows="5" maxlength="4000" show-word-limit autofocus placeholder="请输入需要核验的问题" @input="emitDraft" @keyup.ctrl.enter="submit" /></el-form-item></el-form>
    <template #footer><el-button @click="emit('update:modelValue', false)">取消</el-button><el-button type="primary" :loading="loading" :disabled="!form.content.trim() || !isDraftDirty" @click="submit">即时保存</el-button></template>
  </el-dialog>
</template>
