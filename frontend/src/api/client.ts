import axios from 'axios'
import { clearTokens, getAccessToken, getRefreshToken, saveTokens } from '@/stores/auth'
import type { TokenResponse } from '@/types/kb'
import { toApiError } from '@/utils/errors'

const baseURL = import.meta.env.VITE_API_BASE_URL || ''

export const http = axios.create({ baseURL, timeout: 15_000 })
const refreshHttp = axios.create({ baseURL, timeout: 15_000 })

http.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

http.interceptors.response.use(
  (response) => response,
  async (error: unknown) => {
    if (axios.isAxiosError(error) && error.response?.status === 401 && !error.config?.url?.includes('/auth/')) {
      const refresh = getRefreshToken()
      const config = error.config as typeof error.config & { _retried?: boolean }
      if (refresh && config && !config._retried) {
        config._retried = true
        try {
          const response = await refreshHttp.post<TokenResponse>('/api/v1/auth/refresh', { refresh_token: refresh })
          saveTokens(response.data)
          config.headers.Authorization = `Bearer ${getAccessToken()}`
          return http.request(config)
        } catch {
          clearTokens()
        }
      }
    }
    return Promise.reject(toApiError(error))
  },
)
