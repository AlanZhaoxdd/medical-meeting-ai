<script setup lang="ts">
import type { FormInstance, FormRules } from 'element-plus'
import { computed, reactive, ref, watch } from 'vue'
import type { Meeting, MeetingPayload } from '@/types/meeting'
import { isValidTimeRange, toDate, toIsoString } from '@/utils/meeting'

interface FormModel {
  title: string
  startsAt?: Date
  endsAt?: Date
  location: string | null
  onlineUrl: string | null
  organizer: string | null
  topic: string | null
  description: string | null
  coverUrl: string | null
}

const props = withDefaults(defineProps<{ meeting?: Meeting; submitting?: boolean }>(), { meeting: undefined, submitting: false })
const emit = defineEmits<{ submit: [payload: MeetingPayload]; cancel: [] }>()
const formRef = ref<FormInstance>()

const emptyForm = (): FormModel => ({
  title: '', startsAt: undefined, endsAt: undefined, location: null, onlineUrl: null,
  organizer: null, topic: null, description: null, coverUrl: null,
})
const form = reactive<FormModel>(emptyForm())

const fillForm = (meeting?: Meeting) => Object.assign(form, meeting ? {
  title: meeting.title, startsAt: toDate(meeting.starts_at), endsAt: toDate(meeting.ends_at),
  location: meeting.location, onlineUrl: meeting.online_url, organizer: meeting.organizer,
  topic: meeting.topic, description: meeting.description, coverUrl: meeting.cover_url,
} : emptyForm())
watch(() => props.meeting, fillForm, { immediate: true })

const rules: FormRules<FormModel> = {
  title: [{ required: true, message: '请输入会议标题', trigger: 'blur' }],
  startsAt: [{ required: true, message: '请选择开始时间', trigger: 'change' }],
  endsAt: [
    { required: true, message: '请选择结束时间', trigger: 'change' },
    { validator: (_rule, value, callback) => (!form.startsAt || !value || isValidTimeRange(form.startsAt, value) ? callback() : callback(new Error('结束时间必须晚于开始时间'))), trigger: 'change' },
  ],
}

const normalize = (value: string | null) => value?.trim() || null
const submit = async () => {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid || !form.startsAt || !form.endsAt) return
  emit('submit', {
    title: form.title.trim(), starts_at: toIsoString(form.startsAt), ends_at: toIsoString(form.endsAt),
    location: normalize(form.location), online_url: normalize(form.onlineUrl), organizer: normalize(form.organizer),
    topic: normalize(form.topic), description: normalize(form.description), cover_url: normalize(form.coverUrl),
  })
}

const title = computed(() => (props.meeting ? '编辑会议' : '新建会议'))
</script>

<template>
  <el-card class="content-card form-card">
    <template #header><strong>{{ title }}</strong></template>
    <el-form ref="formRef" :model="form" :rules="rules" label-position="top" @submit.prevent="submit">
      <el-row :gutter="20">
        <el-col :xs="24" :md="12"><el-form-item label="会议标题" prop="title"><el-input v-model="form.title" maxlength="255" show-word-limit placeholder="例如：2026 肿瘤精准诊疗峰会" /></el-form-item></el-col>
        <el-col :xs="24" :md="12"><el-form-item label="主办方"><el-input v-model="form.organizer" maxlength="255" placeholder="请输入主办方" /></el-form-item></el-col>
        <el-col :xs="24" :md="12"><el-form-item label="开始时间" prop="startsAt"><el-date-picker v-model="form.startsAt" type="datetime" value-format="" style="width: 100%" /></el-form-item></el-col>
        <el-col :xs="24" :md="12"><el-form-item label="结束时间" prop="endsAt"><el-date-picker v-model="form.endsAt" type="datetime" value-format="" style="width: 100%" /></el-form-item></el-col>
        <el-col :xs="24" :md="12"><el-form-item label="举办地点"><el-input v-model="form.location" maxlength="500" placeholder="线下地址（可选）" /></el-form-item></el-col>
        <el-col :xs="24" :md="12"><el-form-item label="线上地址"><el-input v-model="form.onlineUrl" placeholder="https://example.com/live" /></el-form-item></el-col>
        <el-col :xs="24" :md="12"><el-form-item label="会议主题"><el-input v-model="form.topic" maxlength="255" placeholder="例如：肿瘤精准诊疗" /></el-form-item></el-col>
        <el-col :xs="24" :md="12"><el-form-item label="封面地址"><el-input v-model="form.coverUrl" placeholder="https://example.com/cover.png" /></el-form-item></el-col>
        <el-col :span="24"><el-form-item label="会议简介"><el-input v-model="form.description" type="textarea" :rows="5" placeholder="请输入会议简介（可选）" /></el-form-item></el-col>
      </el-row>
      <div class="form-actions"><el-button @click="emit('cancel')">取消</el-button><el-button native-type="submit" type="primary" :loading="submitting">保存会议</el-button></div>
    </el-form>
  </el-card>
</template>

<style scoped>
.form-card { max-width: 980px; }
.form-actions { display: flex; justify-content: flex-end; gap: 12px; padding-top: 8px; }
</style>
