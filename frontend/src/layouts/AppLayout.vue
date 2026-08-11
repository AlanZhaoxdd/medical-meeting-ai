<script setup lang="ts">
import { ChatLineRound, CircleCheck, Collection, DataAnalysis, Document, Expand, Files, Fold, Histogram, QuestionFilled, SwitchButton, Upload } from '@element-plus/icons-vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import { canAccessMeetingWorkspace, canAccessSettings, roleLabel } from '@/utils/kb'

const appStore = useAppStore()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const narrowScreen = ref(false)
let mediaQuery: MediaQueryList | undefined
const navigationCollapsed = computed(() => narrowScreen.value || appStore.sidebarCollapsed)
const canViewSettings = computed(() => canAccessSettings(auth.user?.role))
const canViewMeetingWorkspace = computed(() => canAccessMeetingWorkspace(auth.user?.role))
const minutesTabs = computed(() => {
  const meetingId = String(route.params.meetingId || '')
  const base = meetingId ? `/meeting-review/${meetingId}` : '/meeting-review'
  return {
    info: base,
    cutPoint: meetingId ? `${base}/questions/cut-point` : '/meeting-review',
    openEnded: meetingId ? `${base}/questions/open-ended` : '/meeting-review',
  }
})
const activeNavigation = computed(() => {
  const path = String(route.path)
  if (path.startsWith('/meetings/import')) return '/meetings/import'
  if (route.path.startsWith('/meeting-review')) {
    if (path.endsWith('/questions/cut-point')) return minutesTabs.value.cutPoint
    if (path.endsWith('/questions/open-ended')) return minutesTabs.value.openEnded
    return path === '/meeting-review' ? '/meeting-review' : minutesTabs.value.info
  }
  if (path.startsWith('/meeting-analysis')) return '/meeting-analysis'
  if (path.startsWith('/meeting-export')) return '/meeting-export'
  return path
})

function updateScreen(event: MediaQueryListEvent | MediaQueryList) {
  narrowScreen.value = event.matches
}

onMounted(() => {
  mediaQuery = window.matchMedia('(max-width: 768px)')
  updateScreen(mediaQuery)
  mediaQuery.addEventListener('change', updateScreen)
})

onBeforeUnmount(() => mediaQuery?.removeEventListener('change', updateScreen))

async function logout() {
  await auth.logout()
  await router.push('/auth')
}
</script>

<template>
  <el-container class="app-shell">
    <el-aside class="sidebar" :width="navigationCollapsed ? '64px' : '232px'">
      <div class="brand">
        <div class="brand-mark">M</div>
        <span v-show="!navigationCollapsed">ConferenceAI 2.0</span>
      </div>
      <el-menu router :collapse="navigationCollapsed" :default-active="activeNavigation" class="nav-menu">
        <el-menu-item v-if="canViewMeetingWorkspace" index="/meetings/import" aria-label="导入会议" title="导入会议">
          <el-icon><Upload /></el-icon><span>导入会议</span>
        </el-menu-item>
        <el-sub-menu v-if="canViewMeetingWorkspace" index="minutes" class="nav-minutes" popper-class="nav-popup" aria-label="会议纪要编辑" title="会议纪要编辑">
          <template #title><el-icon><Document /></el-icon><span>会议纪要编辑</span></template>
          <el-menu-item :index="minutesTabs.info" class="nav-minutes-info" aria-label="核验基本信息" title="核验基本信息">
            <el-icon><CircleCheck /></el-icon><span>核验基本信息</span>
          </el-menu-item>
          <el-menu-item :index="minutesTabs.cutPoint" class="nav-minutes-cut-point" aria-label="切点问题" title="切点问题">
            <el-icon><QuestionFilled /></el-icon><span>切点问题</span>
          </el-menu-item>
          <el-menu-item :index="minutesTabs.openEnded" class="nav-minutes-open-ended" aria-label="开放性问题" title="开放性问题">
            <el-icon><ChatLineRound /></el-icon><span>开放性问题</span>
          </el-menu-item>
        </el-sub-menu>
        <el-menu-item v-if="canViewMeetingWorkspace" index="/meeting-analysis" aria-label="AI 纪要分析" title="AI 纪要分析">
          <el-icon><DataAnalysis /></el-icon><span>AI 纪要分析</span>
        </el-menu-item>
        <el-menu-item v-if="canViewMeetingWorkspace" index="/meeting-export" aria-label="会议成果导出" title="会议成果导出">
          <el-icon><Files /></el-icon><span>会议成果导出</span>
        </el-menu-item>
        <el-sub-menu v-if="canViewSettings" index="settings" class="nav-settings" popper-class="nav-popup" aria-label="设置" title="设置">
          <template #title><el-icon><Collection /></el-icon><span>设置</span></template>
          <el-menu-item index="/knowledge-bases" class="nav-kb" aria-label="知识库管理"><el-icon><Collection /></el-icon><span>知识库管理</span></el-menu-item>
          <el-menu-item index="/benchmarks" class="nav-benchmarks" aria-label="性能评测" title="性能评测"><el-icon><Histogram /></el-icon><span>性能评测</span></el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <el-button v-if="!narrowScreen" text circle :icon="appStore.sidebarCollapsed ? Expand : Fold" :aria-label="appStore.sidebarCollapsed ? '展开导航' : '收起导航'" @click="appStore.toggleSidebar" />
        <div class="topbar-title">医学知识运营中心</div>
        <div class="user-chip">
          <span>{{ roleLabel(auth.user?.role) }}</span>
        </div>
        <el-button text circle :icon="SwitchButton" aria-label="退出登录" @click="logout" />
      </el-header>
      <el-main class="main-content"><RouterView /></el-main>
    </el-container>
  </el-container>
</template>
