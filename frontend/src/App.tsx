import { useEffect, useState, lazy, Suspense } from 'react'
import { api, getToken, setToken } from './api'
import type { AuthResponse } from './types'
import LoginPage from './LoginPage'
import AlertBell from './AlertBell'
import './App.css'

// 懒加载页面组件 - 首屏只加载ChatPage，其他按需加载
const ChatPage = lazy(() => import('./ChatPage'))
const QuotePage = lazy(() => import('./QuotePage'))
const AnalyzePane = lazy(() => import('./AnalyzePage'))
const HistoryPane = lazy(() => import('./HistoryPage'))
const PortfolioPage = lazy(() => import('./PortfolioPage'))
const BacktestPage = lazy(() => import('./BacktestPage'))
const ProfilePage = lazy(() => import('./ProfilePage'))
const AdminPage = lazy(() => import('./AdminPage'))

type Tab = 'chat' | 'quote' | 'analyze' | 'portfolio' | 'backtest' | 'history' | 'profile' | 'admin'

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
  const [isAdmin, setIsAdmin] = useState(false)
  const { theme, toggle } = useTheme()

  // 启动时校验 token
  useEffect(() => {
    if (!getToken()) { setBooted(true); return }
    api.me()
      .then((r) => {
        setAuth({ token: getToken()!, user: r.user, profile: r.profile })
        setTab('chat')
        // 检查管理员权限
        api.isAdmin().then(res => setIsAdmin(res.is_admin)).catch(() => {})
      })
      .catch(() => setToken(null))
      .finally(() => setBooted(true))
  }, [])

  if (!booted) return <div className="boot-screen">加载中...</div>

  if (!auth) {
    return <LoginPage onLogin={(r) => {
      setAuth(r); setTab('chat')
      // 登录后检查管理员权限
      api.isAdmin().then(res => setIsAdmin(res.is_admin)).catch(() => {})
    }} />
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
          <button className={tab === 'portfolio' ? 'active' : ''} onClick={() => setTab('portfolio')}>投资组合</button>
          <button className={tab === 'backtest' ? 'active' : ''} onClick={() => setTab('backtest')}>策略回测</button>
          <button className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>历史记录</button>
          <button className={tab === 'profile' ? 'active' : ''} onClick={() => setTab('profile')}>个人中心</button>
          {isAdmin && <button className={tab === 'admin' ? 'active' : ''} onClick={() => setTab('admin')}>管理后台</button>}
        </nav>
        <div className="user-menu">
          <AlertBell />
          <button className="ghost" onClick={toggle} title="切换主题">{theme === 'dark' ? '亮色' : '暗色'}</button>
          <span className="user-name">{auth.user.username}</span>
          <button className="ghost" onClick={logout}>退出</button>
        </div>
      </header>
      <main>
        <Suspense fallback={<div style={{padding:'40px',textAlign:'center',color:'var(--text-2)'}}>加载中...</div>}>
          {tab === 'chat' && <ChatPage />}
          {tab === 'quote' && <QuotePage />}
          {tab === 'analyze' && <AnalyzePane />}
          {tab === 'portfolio' && <PortfolioPage />}
          {tab === 'backtest' && <BacktestPage />}
          {tab === 'history' && <HistoryPane onPick={() => setTab('analyze')} />}
          {tab === 'profile' && <ProfilePage />}
          {tab === 'admin' && <AdminPage />}
        </Suspense>
      </main>
    </div>
  )
}

export default App
