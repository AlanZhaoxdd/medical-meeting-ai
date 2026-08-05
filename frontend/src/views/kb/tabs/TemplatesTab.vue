<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { kbApi } from '@/api/kb'
import { useAuthStore } from '@/stores/auth'
import type { ExtractionTemplate, KnowledgeBase } from '@/types/kb'

const props = defineProps<{ kb: KnowledgeBase }>()
const emit = defineEmits<{ updated: [] }>()
const auth = useAuthStore()
const templates = ref<ExtractionTemplate[]>([])
const dialog = ref(false)
const saving = ref(false)
const canManage = computed(() => ['owner', 'admin'].includes(auth.user?.role || ''))
const fields = [
  ['participants', '参会人'],
  ['topics', '议题'],
  ['insights', '洞察'],
  ['consensus', '共识'],
  ['disagreements', '分歧'],
  ['evidence_claims', '证据主张'],
  ['evidence_gaps', '证据缺口'],
  ['action_items', '行动项'],
]
const form = reactive({ name: '', description: '', fields: fields.map(([value]) => value) })

async function load() {
  templates.value = await kbApi.templates(props.kb.id)
}

async function create() {
  saving.value = true
  try {
    await kbApi.createTemplate(props.kb.id, form)
    dialog.value = false
    ElMessage.success('模板 v1 已冻结保存')
    await load()
    emit('updated')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '模板创建失败')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="tab-stack">
    <div class="section-intro">
      <div><h2>字段模板</h2><p>分析字段来自服务端预定义目录；文档任务会冻结实际使用的模板版本。</p></div>
      <el-button v-if="canManage" type="primary" @click="dialog = true">新建模板</el-button>
    </div>
    <div class="template-grid">
      <el-card v-for="template in templates" :key="template.id" class="template-card" shadow="never">
        <div class="template-title"><strong>{{ template.name }}</strong><el-tag>v{{ template.version }}</el-tag></div>
        <p>{{ template.description }}</p>
        <div class="field-tags"><el-tag v-for="field in template.fields" :key="field" effect="plain">{{ field }}</el-tag></div>
        <small v-if="kb.default_template_id === template.id">知识库默认模板</small>
      </el-card>
    </div>
    <el-dialog v-model="dialog" title="创建字段模板" width="600px">
      <el-form label-position="top">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="form.description" type="textarea" /></el-form-item>
        <el-form-item label="启用字段">
          <el-checkbox-group v-model="form.fields" class="field-checks">
            <el-checkbox v-for="[value, label] in fields" :key="value" :value="value">{{ label }}</el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="!form.name || !form.fields.length" @click="create">创建并冻结 v1</el-button></template>
    </el-dialog>
  </div>
</template>
