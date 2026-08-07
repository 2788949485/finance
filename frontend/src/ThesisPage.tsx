import { useEffect, useState } from 'react'
import { api } from './api'
import { useModal } from './Modal'

// 投资论文追踪页面
export default function ThesisPage() {
  const { toast, confirm } = useModal()
  const [theses, setTheses] = useState<any[]>([])
  const [filter, setFilter] = useState<'active' | 'invalidated' | 'all'>('active')
  const [showForm, setShowForm] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [checks, setChecks] = useState<any[]>([])
  const [drift, setDrift] = useState<any>(null)
  const [checkingAll, setCheckingAll] = useState(false)
  const [checkingIds, setCheckingIds] = useState<Set<number>>(new Set())

  const load = async () => {
    try {
      const t = await api.listTheses(filter === 'all' ? undefined : filter)
      setTheses(t)
    } catch { /* ignore */ }
  }

  useEffect(() => { load() }, [filter])

  const loadChecks = async (id: number) => {
    try {
      const c = await api.getThesisChecks(id)
      setChecks(c)
    } catch { /* ignore */ }
  }

  const handleDelete = async (id: number) => {
    const ok = await confirm('确定删除这条投资论文？', { danger: true, confirmText: '删除' })
    if (!ok) return
    try { await api.deleteThesis(id); toast('已删除', 'success'); load() } catch { toast('删除失败', 'error') }
  }

  const handleCheck = async (id: number) => {
    setCheckingIds(prev => new Set(prev).add(id))
    try {
      const r = await api.checkThesis(id)
      if (r.status === 'invalidated') {
        const triggered = r.checks?.filter((c: any) => c.triggered).map((c: any) => c.condition).join('; ')
        toast(`论文已被证伪: ${triggered || ''}`, 'error')
      } else if (r.status === 'warning') {
        toast('部分证伪条件触发，请关注', 'warning')
      } else {
        toast('检查通过，论文有效', 'success')
      }
      load()
      if (expandedId === id) loadChecks(id)
    } catch (e: any) {
      toast('检查失败: ' + (e.message || ''), 'error')
    } finally {
      setCheckingIds(prev => { const n = new Set(prev); n.delete(id); return n })
    }
  }

  const handleCheckAll = async () => {
    setCheckingAll(true)
    try {
      const results = await api.checkAllTheses()
      const invalidated = results.filter((r: any) => r.status === 'invalidated')
      const warnings = results.filter((r: any) => r.status === 'warning')
      let msg = `检查完成: ${results.length}条论文`
      if (invalidated.length) msg += `，${invalidated.length}条已被证伪`
      if (warnings.length) msg += `，${warnings.length}条有警告`
      if (!invalidated.length && !warnings.length) msg += '，全部正常'
      toast(msg, invalidated.length ? 'error' : warnings.length ? 'warning' : 'success')
      load()
    } catch { toast('批量检查失败', 'error') }
    finally { setCheckingAll(false) }
  }

  const handleDrift = async (ticker: string) => {
    try {
      const d = await api.getThesisDrift(ticker)
      setDrift(d)
    } catch (e: any) {
      toast(e.message || '需要至少2次分析记录才能做漂移检测', 'warning')
    }
  }

  const toggleExpand = (id: number) => {
    if (expandedId === id) {
      setExpandedId(null)
    } else {
      setExpandedId(id)
      loadChecks(id)
    }
  }

  return (
    <div className="pane">
      <div className="pane-head">
        <h2>投资论文</h2>
        <div className="thesis-controls">
          <button className="ghost-btn" onClick={handleCheckAll} disabled={checkingAll || theses.length === 0}>
            {checkingAll ? '检查中...' : '一键检查'}
          </button>
          <button className="btn-primary" onClick={() => setShowForm(!showForm)}>
            {showForm ? '取消' : '+ 记录论文'}
          </button>
        </div>
      </div>

      {showForm && <ThesisForm onDone={() => { setShowForm(false); load() }} />}

      <div className="thesis-filters">
        {(['active', 'invalidated', 'all'] as const).map(f => (
          <button key={f} className={filter === f ? 'active' : ''} onClick={() => setFilter(f)}>
            {f === 'active' ? '追踪中' : f === 'invalidated' ? '已证伪' : '全部'}
          </button>
        ))}
      </div>

      {drift && (
        <div className="drift-panel">
          <div className="drift-header">
            <h4>{drift.name}({drift.ticker}) 漂移检测</h4>
            <button className="ghost-btn" onClick={() => setDrift(null)}>关闭</button>
          </div>
          <div className="drift-body">
            <div className="drift-stat">
              <span className="label">评分变化</span>
              <span className={`val ${drift.score_drift > 0 ? 'up' : drift.score_drift < 0 ? 'down' : ''}`}>
                {drift.score_drift > 0 ? '+' : ''}{drift.score_drift}
              </span>
            </div>
            {drift.direction_flipped && <span className="drift-flag">方向翻转</span>}
            {drift.action_changed && (
              <div className="drift-stat">
                <span className="label">建议变化</span>
                <span className="val">{drift.old_action} → {drift.new_action}</span>
              </div>
            )}
            {drift.added_risks?.length > 0 && (
              <div className="drift-risks">
                <span className="label">新增风险:</span>
                {drift.added_risks.map((r: string, i: number) => <span key={i} className="risk-tag">{r}</span>)}
              </div>
            )}
            {drift.summary && <p className="drift-summary">{drift.summary}</p>}
          </div>
        </div>
      )}

      {theses.length === 0 ? (
        <div className="empty-state">
          <p>暂无投资论文</p>
          <p className="hint">记录你的投资逻辑、关键假设和证伪条件，系统会持续监控</p>
        </div>
      ) : (
        <div className="thesis-list">
          {theses.map(t => {
            const isChecking = checkingIds.has(t.id)
            return (
              <div key={t.id} className={`thesis-card ${t.status}`}>
                <div className="thesis-header" onClick={() => toggleExpand(t.id)}>
                  <div className="thesis-info">
                    <span className="thesis-stock">{t.name || t.ticker} <span className="thesis-code">{t.ticker}</span></span>
                    {t.horizon && <span className="thesis-horizon">{t.horizon}</span>}
                    <span className={`thesis-status ${t.status}`}>
                      {t.status === 'active' ? '追踪中' : '已证伪'}
                    </span>
                  </div>
                  <div className="thesis-actions">
                    {t.status === 'active' && (
                      <>
                        <button className="ghost-btn" disabled={isChecking} onClick={(e) => { e.stopPropagation(); handleCheck(t.id) }}>
                          {isChecking ? '检查中' : '检查'}
                        </button>
                        <button className="ghost-btn" onClick={(e) => { e.stopPropagation(); handleDrift(t.ticker) }}>漂移</button>
                      </>
                    )}
                    <button className="ghost-btn danger" onClick={(e) => { e.stopPropagation(); handleDelete(t.id) }}>删除</button>
                  </div>
                </div>

                <div className="thesis-body">
                  <div className="thesis-score">
                    评分: <span className={t.score >= 0 ? 'up' : 'down'}>{t.score > 0 ? '+' : ''}{t.score}</span>
                  </div>
                  <div className="thesis-text">{t.thesis_text}</div>
                  {t.key_assumptions?.length > 0 && (
                    <div className="thesis-section">
                      <span className="label">关键假设:</span>
                      <ul>{t.key_assumptions.map((a: string, i: number) => <li key={i}>{a}</li>)}</ul>
                    </div>
                  )}
                  {t.invalidation_conditions?.length > 0 && (
                    <div className="thesis-section">
                      <span className="label">证伪条件:</span>
                      <ul>{t.invalidation_conditions.map((c: string, i: number) => <li key={i} className="invalidation">{c}</li>)}</ul>
                    </div>
                  )}
                  {t.invalidation_reason && (
                    <div className="thesis-invalidated-reason">
                      证伪原因: {t.invalidation_reason}
                    </div>
                  )}
                </div>

                {expandedId === t.id && (
                  <div className="thesis-checks">
                    <h4>检查历史</h4>
                    {checks.length === 0 ? (
                      <p className="hint">暂无检查记录</p>
                    ) : (
                      checks.map((c: any, i: number) => (
                        <div key={i} className="check-item">
                          <span className="check-time">{c.checked_at}</span>
                          <span className={`check-status ${c.status}`}>
                            {c.status === 'valid' ? '正常' : c.status === 'warning' ? '警告' : '证伪'}
                          </span>
                          {c.price_at_check && <span className="check-price">价格 {c.price_at_check}</span>}
                          {c.checks_detail?.map((d: any, j: number) => (
                            d.triggered && <span key={j} className="check-triggered">{d.condition}</span>
                          ))}
                        </div>
                      ))
                    )}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function ThesisForm({ onDone }: { onDone: () => void }) {
  const { toast } = useModal()
  const [ticker, setTicker] = useState('')
  const [name, setName] = useState('')
  const [thesisText, setThesisText] = useState('')
  const [assumptionsText, setAssumptionsText] = useState('')
  const [invalidationText, setInvalidationText] = useState('')
  const [score, setScore] = useState('5')
  const [horizon, setHorizon] = useState('中线')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async () => {
    if (!ticker.trim() || !thesisText.trim()) { setError('请填写股票代码和投资论文'); return }
    setLoading(true); setError('')
    try {
      await api.createThesis({
        ticker: ticker.trim(),
        name: name.trim(),
        thesis_text: thesisText.trim(),
        key_assumptions: assumptionsText.split('\n').map(s => s.trim()).filter(Boolean),
        invalidation_conditions: invalidationText.split('\n').map(s => s.trim()).filter(Boolean),
        score: parseFloat(score) || 0,
        horizon,
      })
      toast('论文保存成功', 'success')
      setTicker(''); setName(''); setThesisText(''); setAssumptionsText(''); setInvalidationText('')
      onDone()
    } catch (e: any) { setError(e.message || '创建失败') }
    finally { setLoading(false) }
  }

  return (
    <div className="thesis-form">
      <div className="form-row">
        <input className="alert-input" placeholder="股票代码（如 600519）" value={ticker} onChange={e => setTicker(e.target.value)} />
        <input className="alert-input" placeholder="股票名称（如 贵州茅台）" value={name} onChange={e => setName(e.target.value)} />
      </div>
      <div className="form-row">
        <textarea
          className="alert-input thesis-textarea"
          placeholder="投资论文：为什么看好/看空这只股票？（核心逻辑）"
          value={thesisText}
          onChange={e => setThesisText(e.target.value)}
          rows={3}
        />
      </div>
      <div className="form-row">
        <textarea
          className="alert-input thesis-textarea"
          placeholder="关键假设（每行一条，如：毛利率保持90%以上）"
          value={assumptionsText}
          onChange={e => setAssumptionsText(e.target.value)}
          rows={3}
        />
      </div>
      <div className="form-row">
        <textarea
          className="alert-input thesis-textarea"
          placeholder="证伪条件（每行一条，如：毛利率跌破80%）"
          value={invalidationText}
          onChange={e => setInvalidationText(e.target.value)}
          rows={3}
        />
      </div>
      <div className="form-row">
        <select className="alert-select" value={horizon} onChange={e => setHorizon(e.target.value)}>
          <option value="短线">短线</option>
          <option value="中线">中线</option>
          <option value="长线">长线</option>
        </select>
        <input className="alert-input" placeholder="评分(-10~+10)" type="number" step="0.1" min="-10" max="10" value={score} onChange={e => setScore(e.target.value)} />
      </div>
      {error && <span className="alert-error">{error}</span>}
      <button className="btn-primary" onClick={submit} disabled={loading}>
        {loading ? '保存中...' : '保存论文'}
      </button>
    </div>
  )
}
