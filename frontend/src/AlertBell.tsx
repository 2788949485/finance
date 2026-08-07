import { useEffect, useState, useRef } from 'react'
import { api } from './api'
import { useModal } from './Modal'
import type { AlertItem, SearchItem } from './types'

// 全局预警通知：铃铛图标 + 轮询检查 + 弹窗通知
export default function AlertBell() {
  const { toast } = useModal()
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [showPanel, setShowPanel] = useState(false)
  const [notifications, setNotifications] = useState<AlertItem[]>([])
  const [showNotif, setShowNotif] = useState(false)
  const [panelTab, setPanelTab] = useState<'active' | 'triggered'>('active')
  const pollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)

  const loadAlerts = async () => {
    try {
      const data = await api.listAlerts('all')
      setAlerts(data)
    } catch { /* ignore */ }
  }

  useEffect(() => {
    loadAlerts()
    const check = async () => {
      try {
        const result = await api.checkAlerts()
        if (result.triggered.length > 0) {
          setNotifications(prev => [...result.triggered, ...prev].slice(0, 20))
          setShowNotif(true)
          loadAlerts()
        }
      } catch { /* ignore */ }
    }
    check()
    pollRef.current = setInterval(check, 30000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  const activeAlerts = alerts.filter(a => a.status === 'active')
  const triggeredAlerts = alerts.filter(a => a.status === 'triggered')

  const handleDelete = async (id: number) => {
    try { await api.deleteAlert(id); loadAlerts() } catch { toast('删除失败', 'error') }
  }

  const handleReactivate = async (id: number) => {
    try { await api.reactivateAlert(id); loadAlerts() } catch { toast('激活失败', 'error') }
  }

  return (
    <>
      <button
        className="alert-bell-btn"
        onClick={() => setShowPanel(!showPanel)}
        title="价格预警"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
        {activeAlerts.length > 0 && <span className="alert-badge">{activeAlerts.length}</span>}
      </button>

      {showNotif && notifications.length > 0 && (
        <div className="alert-notif-toast">
          <div className="alert-notif-header">
            <span>预警触发通知</span>
            <button onClick={() => setShowNotif(false)}>x</button>
          </div>
          {notifications.slice(0, 5).map((n, i) => (
            <div key={i} className="alert-notif-item">
              <span className="alert-notif-msg">{n.message}</span>
              <span className="alert-notif-time">{n.triggered_at?.slice(11, 16) || ''}</span>
            </div>
          ))}
        </div>
      )}

      {showPanel && (
        <AlertPanel
          activeAlerts={activeAlerts}
          triggeredAlerts={triggeredAlerts}
          panelTab={panelTab}
          setPanelTab={setPanelTab}
          onDelete={handleDelete}
          onReactivate={handleReactivate}
          onRefresh={loadAlerts}
        />
      )}
    </>
  )
}

const TYPE_LABELS: Record<string, string> = {
  price_above: '价格突破 ≥',
  price_below: '价格跌破 ≤',
  change_pct_up: '涨幅超 %',
  change_pct_down: '跌幅超 %',
  ma_cross_up: 'MA5金叉MA20',
  ma_cross_down: 'MA5死叉MA20',
  volume_surge: '放量突破(量比)',
}

function AlertPanel({ activeAlerts, triggeredAlerts, panelTab, setPanelTab, onDelete, onReactivate, onRefresh }: {
  activeAlerts: AlertItem[]
  triggeredAlerts: AlertItem[]
  panelTab: 'active' | 'triggered'
  setPanelTab: (t: 'active' | 'triggered') => void
  onDelete: (id: number) => void
  onReactivate: (id: number) => void
  onRefresh: () => void
}) {
  const [showForm, setShowForm] = useState(false)
  const list = panelTab === 'active' ? activeAlerts : triggeredAlerts

  return (
    <div className="alert-panel">
      <div className="alert-panel-header">
        <h3>价格预警</h3>
        <button className="alert-add-btn" onClick={() => setShowForm(!showForm)}>
          {showForm ? '取消' : '+ 新建'}
        </button>
      </div>

      {showForm && <AlertForm onCreated={() => { setShowForm(false); onRefresh() }} />}

      <div className="alert-tabs">
        <button className={panelTab === 'active' ? 'active' : ''} onClick={() => setPanelTab('active')}>
          监控中 ({activeAlerts.length})
        </button>
        <button className={panelTab === 'triggered' ? 'active' : ''} onClick={() => setPanelTab('triggered')}>
          已触发 ({triggeredAlerts.length})
        </button>
      </div>

      <div className="alert-list">
        {list.length === 0 && (
          <p className="alert-empty">{panelTab === 'active' ? '暂无监控中的预警' : '暂无已触发的预警'}</p>
        )}
        {list.map(a => (
          <div key={a.id} className={`alert-item ${a.status}`}>
            <div className="alert-item-main">
              <div className="alert-item-info">
                <span className="alert-item-symbol">{a.symbol_name || a.symbol}</span>
                <span className="alert-item-type">{TYPE_LABELS[a.alert_type] || a.alert_type} {a.threshold}</span>
              </div>
              {a.message && a.status === 'triggered' && (
                <div className="alert-item-msg">{a.message}</div>
              )}
            </div>
            <div className="alert-item-right">
              {a.status === 'active' && <span className="alert-tag active">监控中</span>}
              {a.status === 'triggered' && <span className="alert-tag triggered">已触发</span>}
              {a.status === 'triggered' && (
                <button className="alert-react-btn" onClick={() => onReactivate(a.id)} title="重新激活">↻</button>
              )}
              <button className="alert-del-btn" onClick={() => onDelete(a.id)}>x</button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function AlertForm({ onCreated }: { onCreated: () => void }) {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchItem[]>([])
  const [selected, setSelected] = useState<{ code: string; name: string } | null>(null)
  const [alertType, setAlertType] = useState('price_above')
  const [threshold, setThreshold] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  // 股票搜索
  useEffect(() => {
    if (!query.trim() || selected) { setResults([]); return }
    const timer = setTimeout(async () => {
      try {
        const r = await api.search(query.trim())
        setResults(r.results.slice(0, 5))
      } catch { setResults([]) }
    }, 300)
    return () => clearTimeout(timer)
  }, [query, selected])

  const submit = async () => {
    if (!selected || !threshold.trim()) { setError('请选择股票并填写阈值'); return }
    setLoading(true); setError('')
    try {
      await api.createAlert(selected.code, selected.name, alertType, parseFloat(threshold))
      setQuery(''); setThreshold(''); setSelected(null)
      onCreated()
    } catch (e: any) { setError(e.message || '创建失败') }
    finally { setLoading(false) }
  }

  const isTechAlert = alertType.startsWith('ma_cross') || alertType === 'volume_surge'

  return (
    <div className="alert-form">
      {!selected ? (
        <>
          <input
            className="alert-input" placeholder="搜索股票名称/代码（如 茅台/600519）"
            value={query} onChange={e => setQuery(e.target.value)}
          />
          {results.length > 0 && (
            <div className="alert-search-results">
              {results.map(r => (
                <button key={r.code} className="alert-search-item"
                  onClick={() => { setSelected({ code: r.code, name: r.name }); setQuery(''); setResults([]) }}>
                  <span>{r.name}</span>
                  <span className="alert-search-code">{r.code}</span>
                </button>
              ))}
            </div>
          )}
        </>
      ) : (
        <div className="alert-selected">
          <span>{selected.name} ({selected.code})</span>
          <button onClick={() => setSelected(null)}>换</button>
        </div>
      )}

      <select className="alert-select" value={alertType} onChange={e => setAlertType(e.target.value)}>
        <option value="price_above">价格突破 ≥</option>
        <option value="price_below">价格跌破 ≤</option>
        <option value="change_pct_up">当日涨幅超 %</option>
        <option value="change_pct_down">当日跌幅超 %</option>
        <option value="ma_cross_up">MA5金叉MA20</option>
        <option value="ma_cross_down">MA5死叉MA20</option>
        <option value="volume_surge">放量突破(量比)</option>
      </select>

      {!isTechAlert && (
        <input
          className="alert-input"
          placeholder={alertType.startsWith('change_pct') ? '百分比（如 5）' : '价格（如 1400）'}
          value={threshold} onChange={e => setThreshold(e.target.value)}
          type="number" step="0.01"
        />
      )}
      {alertType === 'volume_surge' && (
        <input
          className="alert-input"
          placeholder="量比倍数（如 2 表示放量2倍）"
          value={threshold} onChange={e => setThreshold(e.target.value)}
          type="number" step="0.1"
        />
      )}

      {error && <span className="alert-error">{error}</span>}
      <button className="alert-submit-btn" onClick={submit} disabled={loading || !selected}>
        {loading ? '创建中...' : '创建预警'}
      </button>
    </div>
  )
}
