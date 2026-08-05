<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { kbApi } from '@/api/kb'
import { useAuthStore } from '@/stores/auth'
import type { KnowledgeBase } from '@/types/kb'

const props = defineProps<{ kb: KnowledgeBase }>()
const emit = defineEmits<{ updated: [] }>()
const router = useRouter()
const auth = useAuthStore()
const saving = ref(false)
const form = reactive({ name: props.kb.name, description: props.kb.description, status: props.kb.status })
const canManage = computed(() => ['owner', 'admin'].includes(auth.user?.role || ''))

async function save() {
  saving.value = true
  try {
    await kbApi.update(props.kb.id, form)
    ElMessage.success('设置已保存')
    emit('updated')
  } finally {
    saving.value = false
  }
}

async function remove() {
  await ElMessageBox.confirm('知识库将被软删除，普通列表不再显示。', '删除知识库', { type: 'warning', confirmButtonText: '确认删除' })
  await kbApi.remove(props.kb.id)
  ElMessage.success('知识库已删除')
  await router.push('/knowledge-bases')
}
</script>

<template>
  <el-card class="settings-card" shadow="never">
    <h2>知识库设置</h2>
    <p>组织边界和知识库范围由后端强制校验。</p>
    <el-form label-position="top" :disabled="!canManage">
      <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
      <el-form-item label="说明"><el-input v-model="form.description" type="textarea" :rows="4" /></el-form-item>
      <el-form-item label="状态"><el-radio-group v-model="form.status"><el-radio-button value="active">启用</el-radio-button><el-radio-button value="archived">归档</el-radio-button></el-radio-group></el-form-item>
      <el-button type="primary" :loading="saving" @click="save">保存设置</el-button>
    </el-form>
    <template v-if="canManage">
      <el-divider />
      <div class="danger-zone"><div><strong>删除知识库</strong><p>保留底层数据的软删除，可由管理员执行后续清理。</p></div><el-button type="danger" plain @click="remove">删除</el-button></div>
    </template>
  </el-card>
</template>
