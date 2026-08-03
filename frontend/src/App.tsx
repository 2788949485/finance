import { useCallback, useEffect, useState } from 'react'
import { api, getToken, setToken } from './api'
import type { AnalysisResult, AuthResponse, HistoryItem, LLMConfig } from './types'
import LoginPage from './LoginPage'
import ChatPage from './ChatPage'
import './App.css'

type Tab = 'chat' | 'analyze' | 'history' | 'config'

function App() {
  const [tab, setTab] = useState<Tab>('chat')
  const [auth, setAuth] = useState<AuthResponse | null>(null)
  const [booted, setBooted] = useState(false)

  // 启动时校验 token
  useEffect(() => {
    if (!getToken()) { setBooted(true); return }
    api.me()
      .then((r) => {
        setAuth({ token: getToken()!, user: r.user, profile: r.profile })
        setTab('chat')
      })
      .catch(() => setToken(null))
      .finally(() => setBooted(true))
  }, [])

  if (!booted) return <div className="boot-screen">加载中...</div>

  if (!auth) {
    return <LoginPage onLogin={(r) => { setAuth(r); setTab('chat') }} />
  }

  const logout = () => {
    setToken(null)
    setAuth(null)
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">FC</div>
          <h1>FinanceCrew<small>金融智能体投研团队</small></h1>
        </div>
        <nav>
          <button className={tab === 'chat' ? 'active' : ''} onClick={() => setTab('chat')}>智能对话</button>
          <button className={tab === 'analyze' ? 'active' : ''} onClick={() => setTab('analyze')}>投研分析</button>
          <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>历史记录</button>
          <button className={tab === 'config' ? 'active' : ''} onClick={() => setTab('config')}>模型配置</button>
        </nav>
        <div className="user-menu">
          <span className="user-name">{auth.user.username}</span>
          <button className="ghost" onClick={logout}>退出</button>
        </div>
      </header>
      <main>
        {tab === 'chat' && <ChatPage />}
        {tab === 'analyze' && <AnalyzePane />}
        {tab === 'history' && <HistoryPane onPick={() => setTab('analyze')} />}
        {tab === 'config' && <ConfigPane />}
      </main>
    </div>
  )
}

/* ---------------- 投研分析 ---------------- */

function AnalyzePane() {
  const [ticker, setTicker] = useState('600519')
  const [topic, setTopic] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<AnalysisResult | null>(null)

  const run = async () => {
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const r = await api.runAnalysis(ticker, topic)
      setResult(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : '分析失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="pane">
      <div className="input-row">
        <input
          className="ticker-input"
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          placeholder="A股代码，如 600519"
          maxLength={6}
        />
        <input
          className="topic-input"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="可选：分析主题（如：AI算力涨价对公司影响）"
        />
        <button onClick={run} disabled={loading || !ticker.trim()}>
          {loading ? '分析中...' : '开始分析'}
        </button>
      </div>
      {error && <div className="error-box">{error}</div>}
      {loading && <div className="loading">智能体团队正在工作：数据收集 → 五位分析师独立分析 → 多空辩论 → 共识 → 风控审查 → 交易计划...</div>}
      {result && <ReportView result={result} />}
    </div>
  )
}

function ReportView({ result }: { result: AnalysisResult }) {
  const score = result.consensus_score
  const trend = score >= 3 ? '偏多' : score <= -3 ? '偏空' : '中性'
  const plan = result.trade_plan
  const risk = result.risk_review
  // gauge 标记位置：-10 ~ +10 映射到 0% ~ 100%
  const gaugeLeft = `${((score + 10) / 20) * 100}%`

  const price = result.price
  const changePct = result.change_pct

  return (
    <div className="report">
      <div className="report-head">
        <div>
          <h2>{result.name || result.ticker} <span className="ticker-code">{result.ticker}</span></h2>
          <div className="meta">{result.created_at}</div>
        </div>
        <div className={`score-display ${trend === '偏多' ? 'up' : trend === '偏空' ? 'down' : 'neutral'}`}>
          {score > 0 ? '+' : ''}{score}
        </div>
      </div>

      <div className="kpi-grid">
        <div className="kpi-card">
          <div className="kpi-label">现价</div>
          <div className="kpi-value">{price ?? '--'}</div>
          <div className="kpi-sub">人民币</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">涨跌幅</div>
          <div className={`kpi-value ${(changePct ?? 0) >= 0 ? 'up' : 'down'}`}>{changePct != null ? `${changePct > 0 ? '+' : ''}${changePct}%` : '--'}</div>
          <div className="kpi-sub">当日</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">共识评分</div>
          <div className={`kpi-value ${trend === '偏多' ? 'up' : trend === '偏空' ? 'down' : 'neutral'}`}>{score}</div>
          <div className="kpi-sub">{trend}</div>
        </div>
        <div className="kpi-card">
          <div className="kpi-label">分析师</div>
          <div className="kpi-value">{result.analyst_views.length}</div>
          <div className="kpi-sub">五位角色独立研判</div>
        </div>
      </div>

      <div className="consensus">
        <div className="consensus-head">
          <h3>共识结论 / CONSENSUS</h3>
          <span className={`score-display ${trend === '偏多' ? 'up' : trend === '偏空' ? 'down' : 'neutral'}`} style={{ fontSize: 22 }}>
            {score > 0 ? '+' : ''}{score}
          </span>
        </div>
        <div className="gauge-track">
          <div className="gauge-marker" style={{ left: gaugeLeft }} />
        </div>
        <div className="gauge-labels"><span>-10 看空</span><span>0 中性</span><span>+10 看多</span></div>
        <p className="consensus-text">{result.consensus_verdict || '（无）'}</p>
      </div>

      <h3 className="section-title">分析师观点</h3>
      <div className="views-grid">
        {result.analyst_views.map((v) => (
          <div className="view-card" key={v.role}>
            <div className="view-head">
              <span className="view-title">{v.title}</span>
              <span className={`view-score ${v.score >= 3 ? 'up' : v.score <= -3 ? 'down' : 'neutral'}`}>
                {v.score > 0 ? '+' : ''}{v.score}
              </span>
            </div>
            <p className="view-summary">{v.summary}</p>
            {v.evidence.length > 0 && (
              <ul className="evidence">{v.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
            )}
            {v.risk_points.length > 0 && (
              <div className="risk-points">{v.risk_points.map((r, i) => <span key={i}>{r}</span>)}</div>
            )}
          </div>
        ))}
      </div>

      {result.debate.length > 0 && (
        <>
          <h3 className="section-title">多空辩论</h3>
          <div className="debate">
            {result.debate.map((d, i) => (
              <div key={i}>
                <div className="debate-topic">{d.topic}</div>
                <ul>{d.positions.map((p, j) => <li key={j}>{p}</li>)}</ul>
              </div>
            ))}
          </div>
        </>
      )}

      {risk && (
        <>
          <h3 className="section-title">风控审查</h3>
          <div className={`risk-box ${risk.approved ? 'ok' : 'blocked'}`}>
            <div className="risk-verdict">{risk.approved ? '通过' : '否决'}：{risk.verdict}</div>
            <div className="risk-detail">最大建议仓位 {risk.max_position_pct}% | 止损位 {risk.stop_loss_pct}%</div>
          </div>
        </>
      )}

      {plan && (
        <>
          <h3 className="section-title">交易计划</h3>
          <div className={`plan-box action-${plan.action}`}>
            <div className="plan-action">{plan.action}</div>
            <div className="plan-detail">
              建议仓位 {plan.position_pct}%{plan.target_price ? ` | 目标价 ${plan.target_price}` : ''}
              {plan.stop_loss ? ` | 止损价 ${plan.stop_loss}` : ''}
            </div>
            <p>{plan.reasoning}</p>
            {plan.risk_warnings.length > 0 && (
              <ul>{plan.risk_warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>
            )}
          </div>
        </>
      )}

      <div className="disclaimer">{result.disclaimer}</div>
    </div>
  )
}

/* ---------------- 历史记录 ---------------- */

function HistoryPane({ onPick }: { onPick: () => void }) {
  const [items, setItems] = useState<HistoryItem[]>([])
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setItems(await api.getHistory())
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    }
  }, [])

  useEffect(() => { load() }, [load])

  return (
    <div className="pane">
      {error && <div className="error-box">{error}</div>}
      {items.length === 0 ? (
        <div className="empty">暂无分析记录，去"投研分析"页跑一次</div>
      ) : (
        <table className="history-table">
          <thead>
            <tr><th>ID</th><th>代码</th><th>时间</th><th>状态</th><th></th></tr>
          </thead>
          <tbody>
            {items.map((it) => (
              <tr key={it.id}>
                <td>{it.id}</td>
                <td>{it.ticker}</td>
                <td>{it.created_at}</td>
                <td>{it.status}</td>
                <td>
                  <button onClick={onPick}>再分析</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

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

export default App
