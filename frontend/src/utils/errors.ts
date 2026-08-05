import axios from 'axios'
import type { ApiErrorBody } from '@/types/meeting'

export class ApiRequestError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
    public readonly code?: string,
    public readonly details?: unknown,
  ) {
    super(message)
  }
}

export const toApiError = (error: unknown): ApiRequestError => {
  if (error instanceof ApiRequestError) return error
  if (axios.isAxiosError<ApiErrorBody>(error)) {
    const body = error.response?.data
    if (body?.message) return new ApiRequestError(body.message, error.response?.status, body.code, body.details)
    if (error.response) {
      const detail = typeof body === 'object' && body && 'detail' in body ? (body as { detail?: unknown }).detail : undefined
      const message = typeof detail === 'string' ? detail : '请求失败，请稍后重试。'
      const details = typeof body === 'object' && body && 'details' in body ? (body as { details?: unknown }).details : detail && typeof detail === 'object' ? detail : undefined
      return new ApiRequestError(message, error.response.status, undefined, details)
    }
    if (!error.response) return new ApiRequestError('无法连接后端服务，请确认服务已启动。')
  }
  return new ApiRequestError('请求失败，请稍后重试。')
}
