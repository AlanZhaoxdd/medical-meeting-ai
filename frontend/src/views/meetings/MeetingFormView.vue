<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import MeetingForm from '@/components/MeetingForm.vue'
import { meetingsApi } from '@/api/meetings'
import type { Meeting, MeetingPayload } from '@/types/meeting'
import { toApiError } from '@/utils/errors'
import { isTerminalStatus } from '@/utils/meeting'

const route = useRoute()
const router = useRouter()
const id = computed(() => route.params.id as string | undefined)
const meeting = ref<Meeting>()
const loading = ref(Boolean(id.value))
const submitting = ref(false)

const load = async () => {
  if (!id.value) return
  loading.value = true
  try {
    meeting.value = await meetingsApi.get(id.value)
    if (isTerminalStatus(meeting.value.meeting_status)) {
      ElMessage.warning('已取消或归档的会议不可编辑')
      await router.replace({ name: 'meeting-detail', params: { id: id.value } })
    }
  } catch (error) {
    ElMessage.error(toApiError(error).message)
    await router.replace({ name: 'meetings' })
  } finally { loading.value = false }
}
const save = async (payload: MeetingPayload) => {
  submitting.value = true
  try {
    const saved = id.value ? await meetingsApi.update(id.value, payload) : await meetingsApi.create(payload)
    ElMessage.success(id.value ? '会议资料已更新' : '会议已创建')
    await router.replace({ name: 'meeting-detail', params: { id: saved.id } })
  } catch (error) { ElMessage.error(toApiError(error).message) }
  finally { submitting.value = false }
}
const cancel = () => router.back()
onMounted(load)
</script>

<template>
  <section>
    <div class="page-header"><div><h1 class="page-title">{{ id ? '编辑会议' : '新建会议' }}</h1><p class="page-subtitle">请填写会议的基础资料，时间将按当前浏览器时区提交。</p></div></div>
    <el-skeleton v-if="loading" :rows="8" animated />
    <MeetingForm v-else :meeting="meeting" :submitting="submitting" @submit="save" @cancel="cancel" />
  </section>
</template>
