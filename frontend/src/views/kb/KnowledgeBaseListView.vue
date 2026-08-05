<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import { kbApi } from '@/api/kb'
import { useAuthStore } from '@/stores/auth'
import type { KnowledgeBase } from '@/types/kb'

const router = useRouter()
const auth = useAuthStore()
const items = ref<KnowledgeBase[]>([])
const loading = ref(true)
const creating = ref(false)
const dialog = ref(false)
const keyword = ref('')
const form = reactive({ name: '', description: '' })
const canManage = computed(() => ['owner', 'admin'].includes(auth.user?.role || ''))
const filtered = computed(() => {
  const query = keyword.value.trim().toLowerCase()
  return query ? items.value.filter((item) => `${item.name} ${item.description}`.toLowerCase().includes(query)) : items.value
})

async function load() {
  loading.value = true
  try {
    items.value = await kbApi.list()
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '知识库加载失败')
  } finally {
    loading.value = false
  }
}

async function create() {
  creating.value = true
  try {
    const kb = await kbApi.create(form)
    dialog.value = false
    ElMessage.success('知识库已创建')
    await router.push(`/knowledge-bases/${kb.id}`)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '创建失败')
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<template>
  <section>
    <header class="kb-hero">
      <div>
        <p class="eyebrow">KNOWLEDGE BASES</p>
        <h1>可信医学知识，从证据开始</h1>
        <p>管理会议文档、人工审核与可追溯检索。</p>
      </div>
      <el-button v-if="canManage" type="primary" size="large" :icon="Plus" @click="dialog = true">新建知识库</el-button>
    </header>

    <div class="list-toolbar">
      <el-input v-model="keyword" :prefix-icon="Search" placeholder="搜索知识库" clearable />
      <span>{{ filtered.length }} 个项目</span>
    </div>

    <div v-loading="loading" class="kb-grid">
      <button v-for="kb in filtered" :key="kb.id" class="kb-card" type="button" @click="router.push(`/knowledge-bases/${kb.id}`)">
        <div class="kb-card-top">
          <span class="kb-monogram">{{ kb.name.slice(0, 1) }}</span>
          <el-tag size="small" effect="plain" type="success">{{ kb.status }}</el-tag>
        </div>
        <h2>{{ kb.name }}</h2>
        <p>{{ kb.description || '尚未填写项目说明' }}</p>
        <div class="kb-metrics">
          <span><strong>{{ kb.document_count }}</strong> 文档</span>
          <span><strong>{{ kb.published_knowledge_count }}</strong> 已发布知识</span>
        </div>
        <time>更新于 {{ new Date(kb.updated_at).toLocaleDateString('zh-CN') }}</time>
      </button>
      <el-empty v-if="!loading && !filtered.length" class="wide-empty" description="还没有知识库">
        <el-button v-if="canManage" type="primary" @click="dialog = true">创建第一个知识库</el-button>
      </el-empty>
    </div>

    <el-dialog v-model="dialog" title="新建知识库" width="520px">
      <el-form label-position="top">
        <el-form-item label="知识库名称"><el-input v-model="form.name" maxlength="120" /></el-form-item>
        <el-form-item label="项目说明"><el-input v-model="form.description" type="textarea" :rows="4" maxlength="2000" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="creating" :disabled="!form.name.trim()" @click="create">创建并进入</el-button>
      </template>
    </el-dialog>
  </section>
</template>
