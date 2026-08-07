import { useEffect, useState } from 'react'
import { api } from './api'
import { useModal } from './Modal'

// 投研知识库页面
export default function KnowledgePage() {
  const { toast } = useModal()
  const [stats, setStats] = useState<any>(null)
  const [items, setItems] = useState<any[]>([])
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [view, setView] = useState<'recent' | 'search'>('recent')

  const loadStats = async () => {
    try {
      const s = await api.getKnowledgeStats()
      setStats(s)
    } catch { /* ignore */ }
  }

  const loadRecent = async () => {
    try {
      const items = await api.listKnowledge(50)
      setItems(items)
    } catch { /* ignore */ }
  }

  useEffect(() => {
    loadStats()
    loadRecent()
  }, [])

  const handleSearch = async () => {
    if (!query.trim()) {
      setView('recent')
      loadRecent()
      return
    }
    setSearching(true)
    setView('search')
    try {
      const results = await api.searchKnowledge(query.trim())
      setItems(results)
      if (results.length === 0) toast('未找到相关记录', 'info')
    } catch { toast('搜索失败', 'error') }
    finally { setSearching(false) }
  }

  const handleStockClick = async (ticker: string) => {
    setQuery(ticker)
    setSearching(true)
    setView('search')
    try {
      const results = await api.getKnowledgeStock(ticker)
      setItems(results)
    } catch { toast('加载失败', 'error') }
    finally { setSearching(false) }
  }

  // 按股票分组
  const groups: Record<string, any[]> = {}
  for (const it of items) {
    if (!groups[it.ticker]) groups[it.ticker] = []
    groups[it.ticker].push(it)
  }

  return (
    <div className="pane">
      <div className="pane-head">
        <h2>投研知识库</h2>
      </div>

      {/* 统计卡片 */}
      {stats && (
        <div className="kb-stats">
          <div className="kpi-card">
            <span className="kpi-label">总分析数</span>
            <span className="kpi-value">{stats.total}</span>
          </div>
          <div className="kpi-card">
            <span className="kpi-label">覆盖股票</span>
            <span className="kpi-value">{stats.stock_count}</span>
          </div>
          {stats.latest_at && (
            <div className="kpi-card">
              <span className="kpi-label">最近分析</span>
              <span className="kpi-value" style={{ fontSize: 14 }}>{stats.latest_at.slice(0, 10)}</span>
            </div>
          )}
        </div>
      )}

      {/* 搜索框 */}
      <div className="kb-search-bar">
        <input
          className="alert-input"
          placeholder="搜索股票代码/名称/关键词..."
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
        />
        <button className="btn-primary" onClick={handleSearch} disabled={searching}>
          {searching ? '搜索中...' : '搜索'}
        </button>
        {view === 'search' && (
          <button className="ghost-btn" onClick={() => { setQuery(''); setView('recent'); loadRecent() }}>清除</button>
        )}
      </div>

      {/* 按股票快捷筛选 */}
      {stats?.top_stocks?.length > 0 && (
        <div className="kb-stock-filter">
          {stats.top_stocks.map((s: any) => (
            <button key={s.ticker} className="kb-stock-chip" onClick={() => handleStockClick(s.ticker)}>
              {s.ticker} ({s.count})
            </button>
          ))}
        </div>
      )}

      {/* 分析列表 */}
      {items.length === 0 ? (
        <div className="empty-state">
          <p>{view === 'search' ? '未找到匹配的记录' : '暂无投研分析'}</p>
          <p className="hint">在投研分析或智能对话中分析股票后，记录会自动出现在这里</p>
        </div>
      ) : (
        <div className="kb-list">
          {Object.entries(groups).map(([ticker, records]) => (
            <div key={ticker} className="kb-group">
              <div className="kb-group-header" onClick={() => handleStockClick(ticker)}>
                <span className="kb-group-name">{records[0].name}</span>
                <span className="kb-group-code">{ticker}</span>
                <span className="kb-group-count">{records.length}次分析</span>
              </div>
              <div className="kb-group-body">
                {records.map((r: any, i: number) => (
                  <div key={i} className="kb-item">
                    <div className="kb-item-header">
                      <span className="kb-item-date">{r.created_at?.slice(0, 16)}</span>
                      <span className={`kb-item-score ${r.consensus_score >= 0 ? 'up' : 'down'}`}>
                        评分{r.consensus_score > 0 ? '+' : ''}{r.consensus_score?.toFixed(1)}
                      </span>
                      {r.action && <span className="kb-item-action">{r.action}</span>}
                      {r.price && <span className="kb-item-price">价格{r.price}</span>}
                    </div>
                    {r.consensus_verdict && (
                      <p className="kb-item-verdict">{r.consensus_verdict}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
