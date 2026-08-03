// 后端 API 封装
import type { AnalysisResult, HistoryItem, LLMConfig } from './types'

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const data = await resp.json()
      if (data.detail) detail = data.detail
    } catch { /* ignore */ }
    throw new Error(detail)
  }
  return resp.json() as Promise<T>
}

export const api = {
  getConfig: () => request<LLMConfig>('/api/config'),

  saveConfig: (cfg: LLMConfig) =>
    request<LLMConfig>('/api/config', { method: 'PUT', body: JSON.stringify(cfg) }),

  getProviders: () => request<Record<string, { base_url: string; model: string }>>('/api/providers'),

  runAnalysis: (ticker: string, topic?: string) =>
    request<AnalysisResult>('/api/analysis', {
      method: 'POST',
      body: JSON.stringify({ ticker, topic: topic || null }),
    }),

  getAnalysis: (id: number) => request<{ result: AnalysisResult | null }>(`/api/analysis/${id}`),

  getHistory: () => request<HistoryItem[]>('/api/history'),

  health: () => request<{ status: string }>('/api/health'),
}
