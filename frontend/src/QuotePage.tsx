// 行情页：股票搜索（代码/名称/拼音）+ 大 K线图 + 分时 + 实时刷新 + 新闻
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { KlineBar, MinutePoint, NewsItem, QuoteResponse } from './types'
import KLineChart from './KLineChart'

const HOT = [
  { code: '600519', name: '贵州茅台' },
  { code: 'hk00700', name: '腾讯控股' },
  { code: 'usAAPL', name: '苹果' },
  { code: '300750', name: '宁德时代' },
]

interface SearchItem { market: string; code: string; name: string; type: string }

export default function QuotePage() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchItem[]>([])
  const [selected, setSelected] = useState<SearchItem>({ market: 'sh', code: '600519', name: '贵州茅台', type: 'GP' })
  const [data, setData] = useState<QuoteResponse | null>(null)
  const [news, setNews] = useState<NewsItem[]>([])
  const [mode, setMode] = useState<'day' | 'minute'>('day')
  const [live, setLive] = useState(false)
  const [err, setErr] = useState('')
  const [searching, setSearching] = useState(false)
  const [allBars, setAllBars] = useState<KlineBar[]>([])
  const timerRef = useRef<number | null>(null)
  const searchTimer = useRef<number | null>(null)

  const load = useCallback(async (code: string, m: 'day' | 'minute', fresh: number) => {
    try {
      const q = await api.getQuote(code, 120, m, fresh)
      setData(q)
      setErr('')
    } catch {
      setErr('行情加载失败')
    }
  }, [])

  // 选中变化时加载行情 + 新闻 + 全量K线
  useEffect(() => {
    setMode('day')
    setLive(false)
    load(selected.code, 'day', 0)
    api.getQuote(selected.code, 60, 'day', 0, 1).then((q) => {
      if (q.kline.length > 60) setAllBars(q.kline as KlineBar[])
    }).catch(() => setAllBars([]))
    api.getNews(selected.code).then((n) => setNews(n.news)).catch(() => setNews([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected.code])

  // 实时轮询
  useEffect(() => {
    if (!live) return
    timerRef.current = window.setInterval(() => load(selected.code, mode, 1), 15000)
    return () => { if (timerRef.current) window.clearInterval(timerRef.current) }
  }, [live, mode, selected.code, load])

  // 搜索防抖
  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) { setResults([]); return }
    setSearching(true)
    try {
      const r = await api.search(q.trim())
      setResults(r.results)
    } catch { setResults([]) } finally { setSearching(false) }
  }, [])

  const onQueryChange = (v: string) => {
    setQuery(v)
    if (searchTimer.current) window.clearTimeout(searchTimer.current)
    searchTimer.current = window.setTimeout(() => doSearch(v), 300)
  }

  const pick = (item: SearchItem) => {
    setSelected(item)
    setQuery(item.name)
    setResults([])
  }

  const b = data?.brief as {
    name?: string; price?: number; change_pct?: number
    pe?: number; pb?: number; turnover?: number; market_cap?: number
  } | undefined
  const change = b?.change_pct ?? 0
  const bars = (data?.kline as KlineBar[])?.filter((k) => k.date && typeof k.close === 'number') ?? []
  const minute = (data?.kline as MinutePoint[])?.filter((k) => k.time && typeof k.price === 'number') ?? []

  return (
    <div className="quote-page">
      {/* 搜索栏 */}
      <div className="qp-search">
        <input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && doSearch(query)}
          placeholder="搜索股票代码 / 名称 / 拼音（如 600519 / 茅台 / maotai）"
        />
        {searching && <span className="qp-searching">搜索中...</span>}
        {results.length > 0 && (
          <div className="qp-results">
            {results.map((r) => (
              <button key={r.code} className="qp-result" onClick={() => pick(r)}>
                <span className={`qp-market m-${r.market}`}>{r.market.toUpperCase()}</span>
                <span className="qp-name">{r.name}</span>
                <span className="qp-code">{r.code}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 热门默认展示 */}
      <div className="qp-hot">
        {HOT.map((h) => (
          <button
            key={h.code}
            className={`qp-hot-item ${selected.code === h.code ? 'active' : ''}`}
            onClick={() => pick({ market: h.code.startsWith('hk') ? 'hk' : h.code.startsWith('us') ? 'us' : 'sh', code: h.code, name: h.name, type: 'GP' })}
          >
            {h.name}
          </button>
        ))}
      </div>

      {/* 行情主体 */}
      {err && !data && <div className="error-box">{err}</div>}
      {data && (
        <div className="qp-main">
          <div className="qp-head">
            <div className="qp-title">
              <span className="qp-name">{b?.name ?? selected.name}</span>
              <span className="qp-code">{selected.code}</span>
            </div>
            <div className={`qp-price ${change >= 0 ? 'up' : 'down'}`}>
              {b?.price ?? '--'} <small>{change >= 0 ? '+' : ''}{change}%</small>
            </div>
            <div className="qp-actions">
              <button className={`live-btn ${live ? 'on' : ''}`} onClick={() => setLive((v) => !v)}>
                {live ? '● 实时' : '○ 实时'}
              </button>
              <button className={`mode-btn ${mode === 'day' ? 'active' : ''}`} onClick={() => { setMode('day'); load(selected.code, 'day', 0) }}>日K</button>
              <button className={`mode-btn ${mode === 'minute' ? 'active' : ''}`} onClick={() => { setMode('minute'); load(selected.code, 'minute', 0) }}>分时</button>
            </div>
          </div>

          <div className="qp-meta">
            {b?.pe != null && <span>PE {b.pe}</span>}
            {b?.pb != null && <span>PB {b.pb}</span>}
            {b?.turnover != null && <span>换手 {b.turnover}%</span>}
            {b?.market_cap != null && <span>市值 {b.market_cap}亿</span>}
          </div>

          <div className="qp-chart">
            <KLineChart
              bars={allBars.length > 60 ? allBars : bars}
              minute={minute}
              lastClose={data.last_close ?? null}
              symbol={b?.name ?? selected.name}
              mode={mode}
              onMode={(m) => { setMode(m); load(selected.code, m, 0) }}
            />
          </div>

          {news.length > 0 && (
            <div className="qp-news">
              <div className="qp-news-head">最新新闻</div>
              {news.map((n, i) => (
                <div className="quote-news-item" key={i}>
                  <span className="quote-news-time">{n.time.slice(5, 16)}</span>
                  <span className="quote-news-title">{n.title}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
