import { useEffect, useState, useRef } from 'react'
import { api } from './api'
import type { AlertItem } from './types'

// 全局预警通知：铃铛图标 + 轮询检查 + 弹窗通知
export default function AlertBell() {
  const [alerts, setAlerts] = useState<AlertItem[]>([])
  const [showPanel, setShowPanel] = useState(false)
  const [notifications, setNotifications] = useState<AlertItem[]>([])
  const [showNotif, setShowNotif] = useState(false)
  const pollRef = useRef<ReturnType<typeof setInterval> | undefined>(undefined)

  // 加载已有预警
  const loadAlerts = async () => {
    try {
      const data = await api.listAlerts('all')
      setAlerts(data)
    } catch { /* ignore */ }
  }

  useEffect(() => {
    loadAlerts()
    // 每30秒检查预警触发
    const check = async () => {
      try {
        const result = await api.checkAlerts()
        if (result.triggered.length > 0) {
          setNotifications(prev => [...result.triggered, ...prev].slice(0, 20))
          setShowNotif(true)
          loadAlerts() // 刷新列表
        }
      } catch { /* ignore */ }
    }
    check() // 首次立即检查
    pollRef.current = setInterval(check, 30000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [])

  const activeCount = alerts.filter(a => a.status === 'active').length

  const handleDelete = async (id: number) => {
    try {
      await api.deleteAlert(id)
      loadAlerts()
    } catch { /* ignore */ }
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
        {activeCount > 0 && <span className="alert-badge">{activeCount}</span>}
      </button>

      {/* 通知弹窗（右上角浮动） */}
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

      {/* 预警管理面板 */}
      {showPanel && (
        <AlertPanel alerts={alerts} onDelete={handleDelete} onRefresh={loadAlerts} />
      )}
    </>
  )
}

// 预警管理面板
function AlertPanel({ alerts, onDelete, onRefresh }: {
  alerts: AlertItem[]
  onDelete: (id: number) => void
  onRefresh: () => void
}) {
  const [showForm, setShowForm] = useState(false)

  return (
    <div className="alert-panel">
      <div className="alert-panel-header">
        <h3>价格预警</h3>
        <button className="alert-add-btn" onClick={() => setShowForm(!showForm)}>
          {showForm ? '取消' : '+ 新建预警'}
        </button>
      </div>

      {showForm && <AlertForm onCreated={() => { setShowForm(false); onRefresh() }} />}

      <div className="alert-list">
        {alerts.length === 0 && <p className="alert-empty">暂无预警规则</p>}
        {alerts.map(a => (
          <div key={a.id} className={`alert-item ${a.status}`}>
            <div className="alert-item-info">
              <span className="alert-item-symbol">{a.symbol_name || a.symbol}</span>
              <span className="alert-item-type">
                {a.alert_type === 'price_above' && `价格 ≥ ${a.threshold}`}
                {a.alert_type === 'price_below' && `价格 ≤ ${a.threshold}`}
                {a.alert_type === 'change_pct_up' && `涨幅 >= ${a.threshold}%`}
                {a.alert_type === 'change_pct_down' && `跌幅 >= ${a.threshold}%`}
              </span>
            </div>
            <div className="alert-item-right">
              {a.status === 'active' && <span className="alert-tag active">监控中</span>}
              {a.status === 'triggered' && <span className="alert-tag triggered">已触发</span>}
              <button className="alert-del-btn" onClick={() => onDelete(a.id)}>x</button>
            </div>
            {a.message && a.status === 'triggered' && (
              <div className="alert-item-msg">{a.message}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

// 新建预警表单
function AlertForm({ onCreated }: { onCreated: () => void }) {
  const [symbol, setSymbol] = useState('')
  const [alertType, setAlertType] = useState('price_above')
  const [threshold, setThreshold] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async () => {
    if (!symbol.trim() || !threshold.trim()) {
      setError('请填写股票代码和阈值')
      return
    }
    setLoading(true)
    setError('')
    try {
      await api.createAlert(symbol.trim(), '', alertType, parseFloat(threshold))
      setSymbol(''); setThreshold('')
      onCreated()
    } catch (e: any) {
      setError(e.message || '创建失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="alert-form">
      <input
        className="alert-input" placeholder="股票代码（如 600519）"
        value={symbol} onChange={e => setSymbol(e.target.value)}
      />
      <select className="alert-select" value={alertType} onChange={e => setAlertType(e.target.value)}>
        <option value="price_above">价格突破 ≥</option>
        <option value="price_below">价格跌破 ≤</option>
        <option value="change_pct_up">涨幅超 %</option>
        <option value="change_pct_down">跌幅超 %</option>
      </select>
      <input
        className="alert-input" placeholder="阈值（如 1400 或 5）"
        value={threshold} onChange={e => setThreshold(e.target.value)}
        type="number" step="0.01"
      />
      {error && <span className="alert-error">{error}</span>}
      <button className="alert-submit-btn" onClick={submit} disabled={loading}>
        {loading ? '创建中...' : '创建预警'}
      </button>
    </div>
  )
}
