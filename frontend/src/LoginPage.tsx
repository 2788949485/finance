import { useState } from 'react'
import { api, setToken } from './api'
import type { AuthResponse } from './types'

export default function LoginPage({ onLogin }: { onLogin: (r: AuthResponse) => void }) {
  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [username, setUsername] = useState(localStorage.getItem('fc_remember_user') || '')
  const [password, setPassword] = useState(localStorage.getItem('fc_remember_pass') || '')
  const [remember, setRemember] = useState(!!localStorage.getItem('fc_remember_user'))
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!username.trim() || !password) {
      setError('请输入用户名和密码')
      return
    }
    setBusy(true)
    setError('')
    try {
      const r = mode === 'login' ? await api.login(username.trim(), password) : await api.register(username.trim(), password)
      setToken(r.token)
      if (remember) {
        localStorage.setItem('fc_remember_user', username.trim())
        localStorage.setItem('fc_remember_pass', password)
      } else {
        localStorage.removeItem('fc_remember_user')
        localStorage.removeItem('fc_remember_pass')
      }
      onLogin(r)
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="login-wrap">
      <div className="login-card">
        <div className="login-brand">
          <div className="brand-mark">FC</div>
          <h1>FinanceCrew</h1>
          <p>金融智能体投研团队</p>
        </div>
        <div className="login-tabs">
          <button className={`ghost ${mode === 'login' ? 'active' : ''}`} onClick={() => { setMode('login'); setError('') }}>登录</button>
          <button className={`ghost ${mode === 'register' ? 'active' : ''}`} onClick={() => { setMode('register'); setError('') }}>注册</button>
        </div>
        <input placeholder="用户名" value={username} onChange={(e) => setUsername(e.target.value)} />
        <input placeholder="密码（至少 6 位）" type="password" value={password} onChange={(e) => setPassword(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && submit()} />
        <label className="login-remember">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          <span>记住账号密码</span>
        </label>
        {error && <div className="error-box">{error}</div>}
        <button onClick={submit} disabled={busy} className="login-btn">
          {busy ? '处理中...' : mode === 'login' ? '登录' : '注册并登录'}
        </button>
      </div>
    </div>
  )
}
