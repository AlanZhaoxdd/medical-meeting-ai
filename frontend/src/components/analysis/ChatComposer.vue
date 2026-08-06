<script setup lang="ts">
import { Position, VideoPause } from '@element-plus/icons-vue'
import { nextTick, ref, watch } from 'vue'

const props = defineProps<{
  modelValue: string
  disabled?: boolean
  generating?: boolean
  placeholder?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  send: [value: string]
  stop: []
}>()

const textarea = ref<HTMLTextAreaElement>()

function resize() {
  const element = textarea.value
  if (!element) return
  element.style.height = 'auto'
  element.style.height = `${Math.min(element.scrollHeight, 160)}px`
}

watch(
  () => props.modelValue,
  () => nextTick(resize),
)

function onInput(value: string) {
  emit('update:modelValue', value)
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault()
    send()
  }
}

function send() {
  const value = props.modelValue.trim()
  if (!value || props.disabled || props.generating) return
  emit('send', value)
}
</script>

<template>
  <div class="chat-composer">
    <el-input
      ref="textarea"
      :model-value="modelValue"
      type="textarea"
      :rows="2"
      resize="none"
      :autosize="false"
      :placeholder="placeholder ?? '请输入问题，Enter 发送，Shift + Enter 换行'"
      class="composer-input"
      @update:model-value="onInput"
      @keydown="onKeydown"
    />
    <div class="composer-footer">
      <span class="composer-hint">回答基于会议记录与知识库，来源可逐条核验</span>
      <div class="composer-actions">
        <el-button v-if="generating" type="warning" plain :icon="VideoPause" @click="emit('stop')">停止生成</el-button>
        <el-button type="primary" :icon="Position" :disabled="disabled || generating || !modelValue.trim()" @click="send">发送</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat-composer { display: grid; gap: 10px; padding: 12px 14px; border-top: 1px solid #e6e9f2; background: #fbfbfe; }
.composer-input :deep(.el-textarea__inner) { min-height: 56px; max-height: 160px; padding: 10px 12px; border-radius: 10px; color: #2b4360; line-height: 1.6; background: white; }
.composer-footer { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.composer-hint { color: #9aa5b0; font-size: 11px; }
.composer-actions { display: flex; gap: 8px; }
@media (max-width: 640px) {
  .composer-footer { align-items: stretch; flex-direction: column; }
  .composer-actions { justify-content: flex-end; }
}
</style>
