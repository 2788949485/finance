import { useEffect, useState } from 'react'
import { api } from './api'
import { useModal } from './Modal'

// 投研知识库页面（搜索驱动，不默认展示全部历史）
export default function KnowledgePage() {
  const { toast } = useModal()
  const [stats, setStats] = useState<any>(null)
  const [items, setItems] = useState<any[]>([])
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)

  useEffect(() => {
    api.getKnowledgeStats().then(setStats).catch(() => {})
  }, [])

  const doSearch = async (q: string) => {
    setSearching(true)
    setHasSearched(true)
    try {
      const results = await api.searchKnowledge(q.trim())
      setItems(results)
    } catch { toast('搜索失败', 'error') }
    finally { setSearching(false) }
  }

  const handleSearch = () => {
    if (!query.trim()) { setItems([]); setHasSearched(false); return }
    doSearch(query)
  }

  const handleStockClick = (ticker: string) => {
    setQuery(ticker)
    doSearch(ticker)
  }

  const handleClear = () => {
    setQuery('')
    setItems([])
    setHasSearched(false)
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
        <h2>知识库</h2>
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
        </div>
      )}

      {/* 搜索框 */}
      <div className="kb-search-bar">
        <input
          className="alert-input"
          placeholder="搜索历史投研：股票代码/名称/关键词..."
          value={query}
          onChange={e => setQuery(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && handleSearch()}
          autoFocus
        />
        <button className="btn-primary" onClick={handleSearch} disabled={searching}>
          {searching ? '搜索中...' : '搜索'}
        </button>
        {hasSearched && (
          <button className="ghost-btn" onClick={handleClear}>清除</button>
        )}
      </div>

      {/* 按股票快捷筛选 */}
      {stats?.top_stocks?.length > 0 && !hasSearched && (
        <div className="kb-stock-filter">
          <span className="kb-filter-label">快捷查看:</span>
          {stats.top_stocks.map((s: any) => (
            <button key={s.ticker} className="kb-stock-chip" onClick={() => handleStockClick(s.ticker)}>
              {s.ticker} ({s.count})
            </button>
          ))}
        </div>
      )}

      {/* 搜索结果 */}
      {hasSearched && (
        items.length === 0 ? (
          <div className="empty-state">
            <p>未找到与"{query}"相关的投研记录</p>
            <p className="hint">尝试搜索股票代码或名称</p>
          </div>
        ) : (
          <div className="kb-list">
            {Object.entries(groups).map(([ticker, records]) => (
              <div key={ticker} className="kb-group">
                <div className="kb-group-header">
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
        )
      )}
    </div>
  )
}
