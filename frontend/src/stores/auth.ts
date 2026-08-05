import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { authApi } from '@/api/auth'
import type { CurrentUser, TokenResponse } from '@/types/kb'

const ACCESS_KEY = 'medical_kb_access'
const REFRESH_KEY = 'medical_kb_refresh'

export function getAccessToken() {
  return localStorage.getItem(ACCESS_KEY)
}

export function getRefreshToken() {
  return localStorage.getItem(REFRESH_KEY)
}

export function saveTokens(tokens: TokenResponse) {
  localStorage.setItem(ACCESS_KEY, tokens.access_token)
  localStorage.setItem(REFRESH_KEY, tokens.refresh_token)
}

export function clearTokens() {
  localStorage.removeItem(ACCESS_KEY)
  localStorage.removeItem(REFRESH_KEY)
}

export const useAuthStore = defineStore('auth', () => {
  const user = ref<CurrentUser | null>(null)
  const initialized = ref(false)
  const authenticated = computed(() => Boolean(user.value && getAccessToken()))

  async function initialize() {
    if (initialized.value) return
    try {
      if (getAccessToken()) user.value = await authApi.me()
    } catch {
      clearTokens()
    } finally {
      initialized.value = true
    }
  }

  async function login(email: string, password: string) {
    saveTokens(await authApi.login({ email, password }))
    user.value = await authApi.me()
  }

  async function register(payload: { email: string; password: string; display_name: string; organization_name?: string }) {
    saveTokens(await authApi.register(payload))
    user.value = await authApi.me()
  }

  async function logout() {
    const refresh = getRefreshToken()
    try {
      if (refresh) await authApi.logout(refresh)
    } finally {
      clearTokens()
      user.value = null
    }
  }

  return { user, initialized, authenticated, initialize, login, register, logout }
})
