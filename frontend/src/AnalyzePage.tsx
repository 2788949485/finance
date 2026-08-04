import { useState } from 'react'
import { api } from './api'
import type { AnalysisResult } from './types'
import Markdown from './Markdown'

function AnalyzePane() {
  const [ticker, setTicker] = useState('600519')
  const [topic, setTopic] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [steps, setSteps] = useState<{ label: string; status: 'running' | 'done' }[]>([])
  const [analystViews, setAnalystViews] = useState<{ role: string; title: string; summary: string; score: number }[]>([])
  const [riskReview, setRiskReview] = useState<{ approved: boolean; verdict: string; max_position_pct: number; stop_loss_pct: number } | null>(null)
  const [userConfirmed, setUserConfirmed] = useState(false)

  const run = async () => {
    setLoading(true)
    setError('')
    setResult(null)
    setRiskReview(null)
    setUserConfirmed(false)
    setSteps([{ label: '数据收集', status: 'running' }])
    setAnalystViews([])
    try {
      await api.streamAnalysis(ticker, topic, (ev) => {
        if (ev.type === 'step') {
          setSteps((prev) => {
            const idx = prev.findIndex(s => s.label === ev.label)
            if (idx >= 0) {
              const copy = [...prev]; copy[idx] = { label: ev.label, status: ev.status }; return copy
            }
            return [...prev.filter(s => s.status === 'done'), { label: ev.label, status: ev.status }]
          })
        } else if (ev.type === 'analyst') {
          setAnalystViews((prev) => [...prev, { role: ev.role, title: ev.title, summary: ev.summary, score: ev.score }])
        } else if (ev.type === 'risk_review') {
          setRiskReview({ approved: ev.approved, verdict: ev.verdict, max_position_pct: ev.max_position_pct, stop_loss_pct: ev.stop_loss_pct })
        } else if (ev.type === 'result') {
          setResult(ev.data)
        } else if (ev.type === 'error') {
          setError(ev.message)
        }
      })
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
          placeholder="股票代码/名称（如 600519 / hk00700 / usAAPL）"
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
      {loading && (
        <div className="research-live">
          <div className="research-steps">
            {steps.map((s, i) => (
              <div key={i} className={`research-step ${s.status}`}>
                <span className="step-icon">{s.status === 'done' ? '✓' : '◌'}</span>
                <span>{s.label}</span>
              </div>
            ))}
          </div>
          {analystViews.length > 0 && (
            <div className="research-analysts">
              {analystViews.map((a, i) => (
                <div key={i} className="research-analyst">
                  <span className={`analyst-score ${a.score >= 0 ? 'up' : 'down'}`}>{a.score > 0 ? '+' : ''}{a.score}</span>
                  <span className="analyst-title">{a.title}</span>
                  <span className="analyst-summary">{a.summary.slice(0, 80)}{a.summary.length > 80 ? '...' : ''}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      {/* 风控审查 Human-in-the-loop 确认（独立于 loading，流结束后仍显示） */}
      {riskReview && !userConfirmed && (
        <div className={`risk-review-card ${riskReview.approved ? 'approved' : 'blocked'}`}>
          <div className="risk-review-head">
            风控审查{riskReview.approved ? '通过' : '否决'}
          </div>
          <div className="risk-review-verdict">{riskReview.verdict}</div>
          {riskReview.approved && (
            <div className="risk-review-meta">
              建议最大仓位 {riskReview.max_position_pct}% | 止损 {riskReview.stop_loss_pct}%
            </div>
          )}
          <div className="risk-review-actions">
            <button onClick={() => setUserConfirmed(true)}>确认继续</button>
            <button className="ghost" onClick={() => { setUserConfirmed(true) }}>跳过交易计划</button>
          </div>
        </div>
      )}
      {result && userConfirmed && <ReportView result={result} />}
      {result && !userConfirmed && !riskReview && <ReportView result={result} />}
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
        <button className="ghost report-export-btn" onClick={() => window.print()}>导出PDF</button>
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
        <div className="consensus-text"><Markdown text={result.consensus_verdict || '（无）'} /></div>
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

export default AnalyzePane
export { ReportView }
