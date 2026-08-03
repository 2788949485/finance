// 后端 API 封装
import type {
  AnalysisResult, AuthResponse, ChatMessage, ChatReply, ChatSession,
  HistoryItem, LLMConfig, QuoteResponse, UserProfile,
} from './types'

const TOKEN_KEY = 'financecrew_token'

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}
export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  const resp = await fetch(url, { headers, ...options })
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`
    try {
      const data = await resp.json()
      if (data.detail) detail = data.detail
    } catch { /* ignore */ }
    if (resp.status === 401 && !url.includes('/auth/')) {
      setToken(null)
    }
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

  getQuote: (symbol: string, days = 120) =>
    request<QuoteResponse>(`/api/quote/${symbol}?days=${days}`),

  health: () => request<{ status: string }>('/api/health'),

  // 认证
  register: (username: string, password: string) =>
    request<AuthResponse>('/api/auth/register', { method: 'POST', body: JSON.stringify({ username, password }) }),

  login: (username: string, password: string) =>
    request<AuthResponse>('/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),

  me: () => request<{ user: { id: number; username: string }; profile: UserProfile }>('/api/auth/me'),

  getProfile: () => request<UserProfile>('/api/auth/profile'),

  saveProfile: (patch: Partial<UserProfile>) =>
    request<UserProfile>('/api/auth/profile', { method: 'PUT', body: JSON.stringify(patch) }),

  // 对话
  newChat: () => request<{ session_id: number }>('/api/chat/session', { method: 'POST' }),

  listChats: () => request<ChatSession[]>('/api/chat/sessions'),

  deleteChat: (sessionId: number) =>
    request<{ deleted: number }>(`/api/chat/${sessionId}`, { method: 'DELETE' }),

  chatMessages: (sessionId: number) => request<ChatMessage[]>(`/api/chat/${sessionId}/messages`),

  sendChat: (message: string, sessionId?: number) =>
    request<ChatReply>('/api/chat', { method: 'POST', body: JSON.stringify({ message, session_id: sessionId }) }),
}
