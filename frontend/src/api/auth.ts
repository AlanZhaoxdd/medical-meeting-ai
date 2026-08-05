import { http } from '@/api/client'
import type { CurrentUser, TokenResponse } from '@/types/kb'

export const authApi = {
  register(payload: {
    email: string
    password: string
    display_name: string
    organization_name?: string
  }) {
    return http.post<TokenResponse>('/api/v1/auth/register', payload).then((response) => response.data)
  },
  login(payload: { email: string; password: string }) {
    return http.post<TokenResponse>('/api/v1/auth/login', payload).then((response) => response.data)
  },
  refresh(refresh_token: string) {
    return http.post<TokenResponse>('/api/v1/auth/refresh', { refresh_token }).then((response) => response.data)
  },
  me() {
    return http.get<CurrentUser>('/api/v1/auth/me').then((response) => response.data)
  },
  logout(refresh_token: string) {
    return http.post('/api/v1/auth/logout', { refresh_token })
  },
}
