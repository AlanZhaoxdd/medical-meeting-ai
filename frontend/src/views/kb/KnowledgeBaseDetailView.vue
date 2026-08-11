<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft } from '@element-plus/icons-vue'
import { kbApi } from '@/api/kb'
import type { KnowledgeBase } from '@/types/kb'
import DocumentsTab from '@/views/kb/tabs/DocumentsTab.vue'
import SearchWorkbenchTab from '@/views/kb/tabs/SearchWorkbenchTab.vue'
import TemplatesTab from '@/views/kb/tabs/TemplatesTab.vue'
import KbSettingsTab from '@/views/kb/tabs/KbSettingsTab.vue'
import { useAuthStore } from '@/stores/auth'
import { canAccessSettings } from '@/utils/kb'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const kbId = String(route.params.id)
const kb = ref<KnowledgeBase>()
const loading = ref(true)
const requestedTab = String(route.query.tab || 'documents')
const activeTab = ref(requestedTab === 'review' || (requestedTab === 'settings' && !canAccessSettings(auth.user?.role)) ? 'documents' : requestedTab)

async function load() {
  loading.value = true
  try {
    kb.value = await kbApi.get(kbId)
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '知识库加载失败')
  } finally {
    loading.value = false
  }
}

function changeTab(name: string | number) {
  router.replace({ query: { ...route.query, tab: String(name) } })
}

onMounted(load)
</script>

<template>
  <section v-loading="loading">
    <header class="detail-heading">
      <el-button text :icon="ArrowLeft" @click="router.push('/knowledge-bases')">全部知识库</el-button>
      <div v-if="kb" class="detail-title-row">
        <div class="kb-monogram large">{{ kb.name.slice(0, 1) }}</div>
        <div>
          <h1>{{ kb.name }}</h1>
          <p>{{ kb.description || '未填写项目说明' }}</p>
        </div>
      </div>
    </header>
    <el-tabs v-if="kb" v-model="activeTab" class="kb-tabs" @tab-change="changeTab">
      <el-tab-pane label="文档" name="documents"><DocumentsTab :kb="kb" /></el-tab-pane>
      <el-tab-pane label="检索测试" name="search"><SearchWorkbenchTab :kb="kb" /></el-tab-pane>
      <el-tab-pane label="字段模板" name="templates"><TemplatesTab :kb="kb" @updated="load" /></el-tab-pane>
      <el-tab-pane v-if="canAccessSettings(auth.user?.role)" label="设置" name="settings"><KbSettingsTab :kb="kb" @updated="load" /></el-tab-pane>
    </el-tabs>
  </section>
</template>
