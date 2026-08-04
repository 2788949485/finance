import { useEffect, useState } from 'react'
import { api } from './api'
import type { LLMConfig } from './types'

/* ---------------- 模型配置 ---------------- */

function ConfigPane() {
  const [cfg, setCfg] = useState<LLMConfig | null>(null)
  const [providers, setProviders] = useState<Record<string, { base_url: string; model: string }>>({})
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')

  useEffect(() => {
    Promise.all([api.getConfig(), api.getProviders()])
      .then(([c, p]) => { setCfg(c); setProviders(p) })
      .catch((e) => setErr(e instanceof Error ? e.message : '加载配置失败'))
  }, [])

  const set = (patch: Partial<LLMConfig>) => setCfg((c) => (c ? { ...c, ...patch } : c))

  const pickProvider = (provider: string) => {
    if (!cfg) return
    const p = providers[provider]
    set({ provider, base_url: p?.base_url ?? '', model: p?.model ?? '' })
  }

  const save = async () => {
    if (!cfg) return
    setSaving(true)
    setMsg('')
    setErr('')
    try {
      const saved = await api.saveConfig(cfg)
      setCfg(saved)
      setMsg('配置已保存')
    } catch (e) {
      setErr(e instanceof Error ? e.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  if (!cfg) return <div className="pane">加载中...</div>

  return (
    <div className="pane config-pane">
      <h3>大模型配置</h3>
      <p className="hint">支持任意 OpenAI 兼容接口：DeepSeek、OpenAI、通义千问、Moonshot、本地 Ollama 等。API Key 仅保存在本机数据库，前端显示脱敏。</p>

      <label>服务商
        <select value={cfg.provider} onChange={(e) => pickProvider(e.target.value)}>
          {Object.keys(providers).map((p) => <option key={p} value={p}>{p}</option>)}
        </select>
      </label>

      <label>接口地址 (base_url)
        <input value={cfg.base_url} onChange={(e) => set({ base_url: e.target.value })} placeholder="https://api.deepseek.com/v1" />
      </label>

      <label>API Key
        <input value={cfg.api_key} onChange={(e) => set({ api_key: e.target.value })} placeholder="sk-...（留空则保留原值）" type="password" />
      </label>

      <label>模型名称
        <input value={cfg.model} onChange={(e) => set({ model: e.target.value })} placeholder="deepseek-chat" />
      </label>

      <label>温度 (temperature)
        <input type="number" step="0.1" min="0" max="2" value={Math.round(cfg.temperature * 100) / 100} onChange={(e) => set({ temperature: Number(e.target.value) })} />
      </label>

      <div className="config-actions">
        <button onClick={save} disabled={saving}>{saving ? '保存中...' : '保存配置'}</button>
        {msg && <span className="ok-msg">{msg}</span>}
        {err && <span className="err-msg">{err}</span>}
      </div>

      <div className="profile-section">
        <h3>我的画像</h3>
        <p className="hint">画像用于个性化投研建议（风险偏好影响仓位建议，自选股用于快速跟踪）。</p>
        <ProfileForm />
      </div>
    </div>
  )
}

/* ---------------- 用户画像 ---------------- */

function ProfileForm() {
  const [risk, setRisk] = useState('balanced')
  const [watchlistText, setWatchlistText] = useState('')
  const [msg, setMsg] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    api.getProfile()
      .then((p) => {
        setRisk(p.risk_preference)
        setWatchlistText((p.watchlist || []).join(', '))
      })
      .catch(() => { /* ignore */ })
  }, [])

  const save = async () => {
    setBusy(true)
    setMsg('')
    setErr('')
    try {
      const watchlist = watchlistText.split(/[,，\s]+/).filter(Boolean).map((s) => s.replace(/\D/g, '').slice(0, 6)).filter(Boolean)
      await api.saveProfile({ risk_preference: risk, watchlist })
      setMsg('画像已保存')
    } catch (e) {
      setErr(e instanceof Error ? e.message : '保存失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="profile-form">
      <label>风险偏好
        <select value={risk} onChange={(e) => setRisk(e.target.value)}>
          <option value="conservative">保守（低仓位，重止损）</option>
          <option value="balanced">平衡（默认）</option>
          <option value="aggressive">激进（可高仓位）</option>
        </select>
      </label>
      <label>自选股（逗号分隔的股票代码）
        <input value={watchlistText} onChange={(e) => setWatchlistText(e.target.value)} placeholder="600519, 000001, 300750" />
      </label>
      <div className="config-actions">
        <button onClick={save} disabled={busy}>{busy ? '保存中...' : '保存画像'}</button>
        {msg && <span className="ok-msg">{msg}</span>}
        {err && <span className="err-msg">{err}</span>}
      </div>
    </div>
  )
}

export default ConfigPane
