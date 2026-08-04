import { useEffect, useState } from 'react'
import { api } from './api'

// 管理后台页面
export default function AdminPage() {
  const [isAdmin, setIsAdmin] = useState(false)
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    api.isAdmin().then(r => { setIsAdmin(r.is_admin); setChecked(true) }).catch(() => setChecked(true))
  }, [])

  if (!checked) return <div className="pane">检查权限中...</div>
  if (!isAdmin) return <div className="pane"><p className="hint">需要管理员权限才能访问此页面。</p></div>

  return (
    <div className="pane admin-page">
      <div className="pane-head"><h2>管理后台</h2></div>
      <StatsSection />
      <UsersSection />
      <InviteSection />
      <AuditSection />
    </div>
  )
}

/* ---- 系统统计 ---- */
function StatsSection() {
  const [stats, setStats] = useState<Record<string, any>>({})
  useEffect(() => { api.adminStats().then(setStats).catch(() => {}) }, [])

  return (
    <div className="admin-section">
      <h3>系统概览</h3>
      <div className="portfolio-summary">
        <div className="kpi-card"><span className="kpi-label">数据库大小</span><span className="kpi-value">{stats.db_size_mb || 0} MB</span></div>
        <div className="kpi-card"><span className="kpi-label">注册用户</span><span className="kpi-value">{stats.users?.total || 0}</span></div>
        <div className="kpi-card"><span className="kpi-label">活跃用户</span><span className="kpi-value">{stats.users?.active || 0}</span></div>
        <div className="kpi-card"><span className="kpi-label">投研报告</span><span className="kpi-value">{stats.analyses || 0}</span></div>
      </div>
    </div>
  )
}

/* ---- 用户管理 ---- */
function UsersSection() {
  const [users, setUsers] = useState<any[]>([])
  const load = () => { api.adminUsers().then(setUsers).catch(() => {}) }
  useEffect(() => { load() }, [])

  const toggleActive = async (id: number) => {
    try { await api.toggleUserActive(id); load() } catch {}
  }
  const setAdmin = async (id: number, val: boolean) => {
    try { await api.setUserAdmin(id, val); load() } catch {}
  }

  return (
    <div className="admin-section">
      <h3>用户管理（{users.length}人）</h3>
      <table className="portfolio-table">
        <thead><tr><th>ID</th><th>用户名</th><th>注册时间</th><th>管理员</th><th>状态</th><th>投研</th><th>操作</th></tr></thead>
        <tbody>
          {users.map(u => (
            <tr key={u.id}>
              <td>{u.id}</td>
              <td className="pf-name">{u.username}</td>
              <td>{(u.created_at || '').slice(0, 10)}</td>
              <td>
                <label className="admin-toggle">
                  <input type="checkbox" checked={!!u.is_admin} onChange={e => setAdmin(u.id, e.target.checked)} />
                  <span>{u.is_admin ? '管理员' : '普通'}</span>
                </label>
              </td>
              <td className={u.is_active ? 'up' : 'down'}>{u.is_active ? '正常' : '禁用'}</td>
              <td>{u.analysis_count}</td>
              <td>
                <button className="admin-action-btn" onClick={() => toggleActive(u.id)}>
                  {u.is_active ? '禁用' : '启用'}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ---- 邀请码 ---- */
function InviteSection() {
  const [codes, setCodes] = useState<any[]>([])
  const [note, setNote] = useState('')
  const load = () => { api.adminInvites().then(setCodes).catch(() => {}) }
  useEffect(() => { load() }, [])

  const create = async () => {
    try { await api.createInvite(note); setNote(''); load() } catch {}
  }

  return (
    <div className="admin-section">
      <h3>邀请码管理</h3>
      <div className="trade-form">
        <input className="alert-input" placeholder="备注（可选）" value={note} onChange={e => setNote(e.target.value)} />
        <button className="btn-primary" onClick={create}>生成邀请码</button>
      </div>
      <table className="portfolio-table">
        <thead><tr><th>邀请码</th><th>创建人</th><th>创建时间</th><th>使用人</th><th>状态</th></tr></thead>
        <tbody>
          {codes.length === 0 && <tr><td colSpan={5} className="empty-row">暂无邀请码（空表时注册不需要邀请码）</td></tr>}
          {codes.map(c => (
            <tr key={c.code}>
              <td className="pf-code">{c.code}</td>
              <td>{c.created_by_name || c.created_by}</td>
              <td>{(c.created_at || '').slice(0, 16)}</td>
              <td>{c.used_by || '-'}</td>
              <td className={c.used_by ? '' : 'up'}>{c.used_by ? '已使用' : '可用'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/* ---- 审计日志 ---- */
function AuditSection() {
  const [logs, setLogs] = useState<any[]>([])
  useEffect(() => { api.adminAuditLogs().then(setLogs).catch(() => {}) }, [])

  return (
    <div className="admin-section">
      <h3>审计日志（最近{logs.length}条）</h3>
      <table className="portfolio-table">
        <thead><tr><th>时间</th><th>用户</th><th>操作</th><th>详情</th><th>IP</th></tr></thead>
        <tbody>
          {logs.length === 0 && <tr><td colSpan={5} className="empty-row">暂无日志</td></tr>}
          {logs.map(l => (
            <tr key={l.id}>
              <td>{(l.created_at || '').slice(0, 19)}</td>
              <td className="pf-name">{l.username || l.user_id}</td>
              <td>{l.action}</td>
              <td className="pf-code">{l.detail}</td>
              <td className="pf-code">{l.ip}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
