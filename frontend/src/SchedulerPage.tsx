import { useEffect, useState } from 'react'
import { api } from './api'
import { useModal } from './Modal'

// 定时/自动化分析页面
export default function SchedulerPage() {
  const { toast, confirm } = useModal()
  const [tasks, setTasks] = useState<any[]>([])
  const [tradingDay, setTradingDay] = useState<boolean>(true)
  const [showForm, setShowForm] = useState(false)
  const [expandedId, setExpandedId] = useState<number | null>(null)
  const [results, setResults] = useState<any[]>([])
  const [runningIds, setRunningIds] = useState<Set<number>>(new Set())

  const load = async () => {
    try {
      const [t, d] = await Promise.all([api.listScheduledTasks(), api.checkTradingDay()])
      setTasks(t)
      setTradingDay(d.trading_day)
    } catch { /* ignore */ }
  }

  useEffect(() => { load() }, [])

  const loadResults = async (id: number) => {
    try {
      const r = await api.getScheduledResults(id)
      setResults(r)
    } catch { /* ignore */ }
  }

  const handleDelete = async (id: number) => {
    const ok = await confirm('确定删除这个定时任务？', { danger: true, confirmText: '删除' })
    if (!ok) return
    try { await api.deleteScheduledTask(id); toast('已删除', 'success'); load() } catch { toast('删除失败', 'error') }
  }

  const handleToggle = async (task: any) => {
    try {
      await api.updateScheduledTask(task.id, { enabled: !task.enabled })
      toast(task.enabled ? '已暂停' : '已启用', 'success')
      load()
    } catch { toast('操作失败', 'error') }
  }

  const handleRunNow = async (id: number) => {
    setRunningIds(prev => new Set(prev).add(id))
    toast('正在执行分析，请稍候...', 'info')
    try {
      const result = await api.runScheduledTaskNow(id)
      if (result.skipped) {
        toast(`已跳过: ${result.reason}`, 'warning')
      } else if (result.symbols) {
        const detail = Object.entries(result.symbols).map(([k, v]: [string, any]) =>
          v.error ? `${k}失败` : `${v.name || k} ${v.verdict || ''} ${v.action || ''}`
        ).join('; ')
        toast(`分析完成: ${detail}`, 'success')
      } else {
        toast('分析完成', 'success')
      }
      load()
      if (expandedId === id) loadResults(id)
    } catch (e: any) {
      toast('执行失败: ' + (e.message || ''), 'error')
    } finally {
      setRunningIds(prev => { const n = new Set(prev); n.delete(id); return n })
    }
  }

  const toggleExpand = (id: number) => {
    if (expandedId === id) {
      setExpandedId(null)
    } else {
      setExpandedId(id)
      loadResults(id)
    }
  }

  return (
    <div className="pane">
      <div className="pane-head">
        <h2>定时分析</h2>
        <div className="scheduler-status">
          <span className={`trading-badge ${tradingDay ? 'open' : 'closed'}`}>
            {tradingDay ? '交易日' : '非交易日'}
          </span>
          <button className="btn-primary" onClick={() => setShowForm(!showForm)}>
            {showForm ? '取消' : '+ 新建任务'}
          </button>
        </div>
      </div>

      {showForm && <TaskForm onDone={() => { setShowForm(false); load() }} />}

      {tasks.length === 0 ? (
        <div className="empty-state">
          <p>暂无定时分析任务</p>
          <p className="hint">创建任务后，系统会在每个交易日指定时间自动分析选定的股票</p>
        </div>
      ) : (
        <div className="task-list">
          {tasks.map(t => {
            const isRunning = runningIds.has(t.id)
            return (
              <div key={t.id} className={`task-card ${t.enabled ? '' : 'disabled'}`}>
                <div className="task-header" onClick={() => !isRunning && toggleExpand(t.id)}>
                  <div className="task-info">
                    <span className="task-name">
                      {t.name}
                      {isRunning && <span className="task-running-tag">分析中...</span>}
                    </span>
                    <span className="task-meta">
                      {String(t.cron_hour).padStart(2, '0')}:{String(t.cron_minute).padStart(2, '0')} |
                      {t.symbols.length}只 | {t.mode === 'agentic' ? 'Agent模式' : '标准模式'}
                    </span>
                  </div>
                  <div className="task-actions">
                    <button className="ghost-btn" disabled={isRunning} onClick={(e) => { e.stopPropagation(); handleRunNow(t.id) }}>
                      {isRunning ? '运行中' : '立即运行'}
                    </button>
                    <button className="ghost-btn" disabled={isRunning} onClick={(e) => { e.stopPropagation(); handleToggle(t) }}>
                      {t.enabled ? '暂停' : '启用'}
                    </button>
                    <button className="ghost-btn danger" disabled={isRunning} onClick={(e) => { e.stopPropagation(); handleDelete(t.id) }}>删除</button>
                  </div>
                </div>
                <div className="task-symbols">
                  {t.symbols.map((s: string) => (
                    <span key={s} className="symbol-tag">{s}</span>
                  ))}
                </div>
                {t.last_run_at && (
                  <div className="task-last-run">
                    <span className="label">上次运行: {t.last_run_at}</span>
                    {t.last_result_summary && <span className="summary">{t.last_result_summary}</span>}
                  </div>
                )}
                {isRunning && (
                  <div className="task-running-status">
                    <div className="task-spinner" />
                    <span>正在分析 {t.symbols.join(', ')}，请稍候...</span>
                  </div>
                )}
                {expandedId === t.id && !isRunning && (
                  <div className="task-results">
                    <h4>历史结果</h4>
                    {results.length === 0 ? (
                      <p className="hint">暂无执行记录</p>
                    ) : (
                      results.map((r: any, i: number) => (
                        <div key={i} className="result-item">
                          <span className="result-time">{r.run_at}</span>
                          {r.results?.skipped ? (
                            <span className="result-skipped">跳过: {r.results.reason}</span>
                          ) : (
                            <div className="result-detail">
                              {r.results?.summary && <span className="result-summary">{r.results.summary}</span>}
                            </div>
                          )}
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

function TaskForm({ onDone }: { onDone: () => void }) {
  const { toast } = useModal()
  const [name, setName] = useState('')
  const [symbolsText, setSymbolsText] = useState('')
  const [mode, setMode] = useState('standard')
  const [hour, setHour] = useState('15')
  const [minute, setMinute] = useState('30')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async () => {
    const symbols = symbolsText.split(/[,，\s]+/).map(s => s.trim()).filter(Boolean)
    if (symbols.length === 0) { setError('请输入至少一个股票代码'); return }
    setLoading(true); setError('')
    try {
      await api.createScheduledTask({
        name: name || `定时分析 ${symbols[0]}`,
        symbols,
        mode,
        cron_hour: parseInt(hour),
        cron_minute: parseInt(minute),
      })
      toast('任务创建成功', 'success')
      setName(''); setSymbolsText('')
      onDone()
    } catch (e: any) { setError(e.message || '创建失败') }
    finally { setLoading(false) }
  }

  return (
    <div className="task-form">
      <div className="form-row">
        <input className="alert-input" placeholder="任务名称（可选）" value={name} onChange={e => setName(e.target.value)} />
      </div>
      <div className="form-row">
        <input className="alert-input" placeholder="股票代码，逗号分隔（如 600519,000858）" value={symbolsText} onChange={e => setSymbolsText(e.target.value)} />
      </div>
      <div className="form-row">
        <select className="alert-select" value={mode} onChange={e => setMode(e.target.value)}>
          <option value="standard">标准模式</option>
          <option value="agentic">Agent模式</option>
        </select>
        <select className="alert-select" value={hour} onChange={e => setHour(e.target.value)}>
          {Array.from({ length: 24 }, (_, i) => <option key={i} value={String(i)}>{String(i).padStart(2, '0')}时</option>)}
        </select>
        <select className="alert-select" value={minute} onChange={e => setMinute(e.target.value)}>
          {['00', '10', '15', '20', '30', '45'].map(m => <option key={m} value={m.replace('0','')}>{m}分</option>)}
        </select>
      </div>
      {error && <span className="alert-error">{error}</span>}
      <button className="btn-primary" onClick={submit} disabled={loading}>
        {loading ? '创建中...' : '创建任务'}
      </button>
    </div>
  )
}
