import { useEffect, useState } from 'react'
import { api, getToken, setToken } from './api'
import type { AuthResponse } from './types'
import LoginPage from './LoginPage'
import ChatPage from './ChatPage'
import QuotePage from './QuotePage'
import AnalyzePane from './AnalyzePage'
import HistoryPane from './HistoryPage'
import ConfigPane from './ConfigPage'
import AlertBell from './AlertBell'
import './App.css'

type Tab = 'chat' | 'quote' | 'analyze' | 'history' | 'config'

function useTheme() {
  const [theme, setTheme] = useState(() => localStorage.getItem('fc_theme') || 'dark')
  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
    localStorage.setItem('fc_theme', theme)
  }, [theme])
  return { theme, toggle: () => setTheme(t => t === 'dark' ? 'light' : 'dark') }
}

function App() {
  const [tab, setTab] = useState<Tab>('chat')
  const [auth, setAuth] = useState<AuthResponse | null>(null)
  const [booted, setBooted] = useState(false)
  const { theme, toggle } = useTheme()

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
          <button className={tab === 'quote' ? 'active' : ''} onClick={() => setTab('quote')}>行情</button>
          <button className={tab === 'analyze' ? 'active' : ''} onClick={() => setTab('analyze')}>投研分析</button>
          <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>历史记录</button>
          <button className={tab === 'config' ? 'active' : ''} onClick={() => setTab('config')}>模型配置</button>
        </nav>
        <div className="user-menu">
          <AlertBell />
          <button className="ghost" onClick={toggle} title="切换主题">{theme === 'dark' ? '亮色' : '暗色'}</button>
          <span className="user-name">{auth.user.username}</span>
          <button className="ghost" onClick={logout}>退出</button>
        </div>
      </header>
      <main>
        {tab === 'chat' && <ChatPage />}
        {tab === 'quote' && <QuotePage />}
        {tab === 'analyze' && <AnalyzePane />}
        {tab === 'history' && <HistoryPane onPick={() => setTab('analyze')} />}
        {tab === 'config' && <ConfigPane />}
      </main>
    </div>
  )
}

export default App
