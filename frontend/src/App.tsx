import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { AnalysisResult, HistoryItem, LLMConfig } from './types'
import './App.css'

type Tab = 'analyze' | 'config' | 'history'

function App() {
  const [tab, setTab] = useState<Tab>('analyze')

  return (
    <div className="app">
      <header className="topbar">
        <h1>FinanceCrew 金融智能体团队</h1>
        <nav>
          <button className={tab === 'analyze' ? 'active' : ''} onClick={() => setTab('analyze')}>投研分析</button>
          <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>历史记录</button>
          <button className={tab === 'config' ? 'active' : ''} onClick={() => setTab('config')}>模型配置</button>
        </nav>
      </header>
      <main>
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

  return (
    <div className="report">
      <div className="report-head">
        <div>
          <h2>{result.name || result.ticker} <span className="ticker-code">{result.ticker}</span></h2>
          <div className="meta">
            现价 {result.price ?? '--'} | 共识评分 {score}（{trend}）| {result.created_at}
          </div>
        </div>
        <div className={`score-badge ${trend === '偏多' ? 'bull' : trend === '偏空' ? 'bear' : 'neutral'}`}>
          {score > 0 ? '+' : ''}{score}
        </div>
      </div>

      <div className="consensus">
        <h3>共识结论</h3>
        <p>{result.consensus_verdict || '（无）'}</p>
      </div>

      <h3>分析师观点</h3>
      <div className="views-grid">
        {result.analyst_views.map((v) => (
          <div className="view-card" key={v.role}>
            <div className="view-head">
              <span className="view-title">{v.title}</span>
              <span className={`view-score ${v.score >= 3 ? 'bull' : v.score <= -3 ? 'bear' : 'neutral'}`}>
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
          <h3>多空辩论</h3>
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
          <h3>风控审查</h3>
          <div className={`risk-box ${risk.approved ? 'ok' : 'blocked'}`}>
            <div className="risk-verdict">{risk.approved ? '通过' : '否决'}：{risk.verdict}</div>
            <div className="risk-detail">最大建议仓位 {risk.max_position_pct}% | 止损位 {risk.stop_loss_pct}%</div>
          </div>
        </>
      )}

      {plan && (
        <>
          <h3>交易计划</h3>
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
    </div>
  )
}

export default App
