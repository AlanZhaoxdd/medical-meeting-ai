export type BenchmarkKind =
  | 'retrieval_quality'
  | 'search_latency'
  | 'embedding_throughput'
  | 'ragas_quality'
export type BenchmarkStatus = 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'DISPATCH_FAILED'

export interface BenchmarkEnvironment {
  device: string
  embedding_model: string
  embedding_strategy: string
  reranker_model: string
  bge_batch_size: number
}

export interface BenchmarkRun {
  id: string
  kind: BenchmarkKind
  name: string
  status: BenchmarkStatus
  progress: number
  message: string
  environment: Partial<BenchmarkEnvironment>
  params: Record<string, unknown>
  metrics: Record<string, unknown> | null
  error_message: string | null
  created_by: string | null
  created_at: string
  updated_at: string
  completed_at: string | null
}

export interface BenchmarkCreatePayload {
  kind: BenchmarkKind
  name: string
  params: Record<string, unknown>
}
