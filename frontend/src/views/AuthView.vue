<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const mode = ref<'login' | 'register'>('login')
const loading = ref(false)
const form = reactive({
  email: '',
  password: '',
  display_name: '',
  organization_name: '',
})

async function submit() {
  loading.value = true
  try {
    if (mode.value === 'login') {
      await auth.login(form.email, form.password)
      ElMessage.success('欢迎回来')
    } else {
      await auth.register({
        email: form.email,
        password: form.password,
        display_name: form.display_name,
        organization_name: form.organization_name || undefined,
      })
      ElMessage.success('组织与管理员账号已初始化')
    }
    await router.replace(String(route.query.redirect || '/knowledge-bases'))
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '认证失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-story">
      <div class="auth-brand">M</div>
      <p class="eyebrow">MEDICAL KNOWLEDGE OPERATIONS</p>
      <h1>让每一条医学洞察<br />都有可核验的证据。</h1>
      <p>从会议原件到知识审核与混合检索，在同一个受控工作台完成。</p>
      <div class="workflow-strip">
        <span>解析</span><i /> <span>证据</span><i /> <span>审核</span><i /> <span>发布</span>
      </div>
    </section>
    <section class="auth-panel">
      <div class="auth-card">
        <p class="eyebrow">{{ mode === 'login' ? 'SIGN IN' : 'GET STARTED' }}</p>
        <h2>{{ mode === 'login' ? '登录知识工作台' : '初始化您的组织' }}</h2>
        <p class="muted">{{ mode === 'login' ? '继续管理会议知识资产' : '首个账号将成为组织 Owner' }}</p>
        <el-form label-position="top" @submit.prevent="submit">
          <el-form-item v-if="mode === 'register'" label="姓名">
            <el-input v-model="form.display_name" size="large" autocomplete="name" />
          </el-form-item>
          <el-form-item v-if="mode === 'register'" label="组织名称">
            <el-input v-model="form.organization_name" size="large" placeholder="例：星海医学事务部" />
          </el-form-item>
          <el-form-item label="工作邮箱">
            <el-input v-model="form.email" size="large" type="email" autocomplete="email" />
          </el-form-item>
          <el-form-item label="密码">
            <el-input
              v-model="form.password"
              size="large"
              type="password"
              show-password
              autocomplete="current-password"
              placeholder="至少 10 位"
              @keyup.enter="submit"
            />
          </el-form-item>
          <el-button class="auth-submit" type="primary" size="large" :loading="loading" @click="submit">
            {{ mode === 'login' ? '登录' : '创建组织并进入' }}
          </el-button>
        </el-form>
        <el-divider />
        <button class="mode-switch" type="button" @click="mode = mode === 'login' ? 'register' : 'login'">
          {{ mode === 'login' ? '没有账号？初始化组织' : '已有账号？返回登录' }}
        </button>
      </div>
    </section>
  </main>
</template>
