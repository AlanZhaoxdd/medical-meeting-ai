import { http } from '@/api/client'
import type {
  BenchmarkCreatePayload,
  BenchmarkEnvironment,
  BenchmarkRun,
} from '@/types/benchmark'

export const benchmarksApi = {
  environment() {
    return http.get<BenchmarkEnvironment>('/api/v1/admin/benchmarks/environment').then((r) => r.data)
  },
  list() {
    return http.get<BenchmarkRun[]>('/api/v1/admin/benchmarks').then((r) => r.data)
  },
  get(id: string) {
    return http.get<BenchmarkRun>(`/api/v1/admin/benchmarks/${id}`).then((r) => r.data)
  },
  create(payload: BenchmarkCreatePayload) {
    return http.post<BenchmarkRun>('/api/v1/admin/benchmarks', payload).then((r) => r.data)
  },
}
