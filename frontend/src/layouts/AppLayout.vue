<script setup lang="ts">
import { CircleCheck, Collection, DataAnalysis, Document, Expand, Fold, SwitchButton, Upload } from '@element-plus/icons-vue'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const narrowScreen = ref(false)
let mediaQuery: MediaQueryList | undefined
const navigationCollapsed = computed(() => narrowScreen.value || appStore.sidebarCollapsed)
const activeNavigation = computed(() => route.path.startsWith('/meetings/import/') ? '/meetings/minutes/edit' : route.path.startsWith('/meeting-review') ? '/meeting-review' : route.path)

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
        <span v-show="!navigationCollapsed">医药会议智能分析平台</span>
      </div>
      <el-menu router :collapse="navigationCollapsed" :default-active="activeNavigation" class="nav-menu">
        <el-menu-item index="/meetings/import" aria-label="导入会议" title="导入会议">
          <el-icon><Upload /></el-icon><span>导入会议</span>
        </el-menu-item>
        <el-menu-item index="/meetings/minutes/edit" aria-label="会议纪要编辑" title="会议纪要编辑">
          <el-icon><Document /></el-icon><span>会议纪要编辑</span>
        </el-menu-item>
        <el-menu-item index="/meeting-review" aria-label="基本信息概览" title="基本信息概览">
          <el-icon><CircleCheck /></el-icon><span>基本信息概览</span>
        </el-menu-item>
        <el-sub-menu index="settings" class="nav-settings" popper-class="nav-popup" aria-label="设置" title="设置">
          <template #title><el-icon><Collection /></el-icon><span>设置</span></template>
          <el-menu-item index="/knowledge-bases" class="nav-kb" aria-label="知识库管理"><el-icon><Collection /></el-icon><span>知识库管理</span></el-menu-item>
          <el-menu-item index="/benchmarks" class="nav-benchmarks" aria-label="性能评测" title="性能评测"><el-icon><DataAnalysis /></el-icon><span>性能评测</span></el-menu-item>
        </el-sub-menu>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="topbar">
        <el-button v-if="!narrowScreen" text circle :icon="appStore.sidebarCollapsed ? Expand : Fold" :aria-label="appStore.sidebarCollapsed ? '展开导航' : '收起导航'" @click="appStore.toggleSidebar" />
        <div class="topbar-title">医学知识运营中心</div>
        <div class="user-chip">
          <span>{{ auth.user?.display_name }}</span>
          <el-tag size="small" effect="plain">{{ auth.user?.role }}</el-tag>
        </div>
        <el-button text circle :icon="SwitchButton" aria-label="退出登录" @click="logout" />
      </el-header>
      <el-main class="main-content"><RouterView /></el-main>
    </el-container>
  </el-container>
</template>
