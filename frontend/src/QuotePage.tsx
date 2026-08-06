// 行情页（东财式三栏布局）：搜索栏 → 三栏主区 → 新闻
//   左栏 220px：自选股列表（常驻）
//   中栏 1fr ：标题+价格+操作 / 周期工具栏 / K线 / 对比表
//   右栏 300px：盘口数据表 / 资金流向卡 / K线形态卡
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { KlineBar, MinutePoint, NewsItem, QuoteResponse } from './types'
import KLineChart from './KLineChart'
import { StarButton } from './QuoteCard'

const HOT_FALLBACK = [
  { code: '600519', name: '贵州茅台' },
  { code: 'hk00700', name: '腾讯控股' },
  { code: 'usAAPL', name: '苹果' },
  { code: '300750', name: '宁德时代' },
]

interface SearchItem { market: string; code: string; name: string; type: string }

// 推断市场前缀（watchlist 里的 code 形如 sh600519 / hk00700 / usAAPL / 600519）
function inferMarket(code: string): string {
  if (code.startsWith('hk')) return 'hk'
  if (code.startsWith('us')) return 'us'
  if (/^(sh|sz|bj)/i.test(code)) return code.slice(0, 2).toLowerCase()
  if (/^\d{6}$/.test(code)) {
    const c = code[0]
    if (c === '6') return 'sh'      // 沪市
    if (c === '0' || c === '3') return 'sz' // 深市/创业板
    if (c === '8' || c === '4') return 'bj' // 北交所
    return 'sh'
  }
  return 'sh'
}

// 把任意 code 归一化为 "纯代码"（去掉市场前缀），后端 quote 接口接受带前缀或不带前缀
function stripMarket(code: string): string {
  return code.replace(/^(sh|sz|bj|hk|us)/i, '')
}

export default function QuotePage() {
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchItem[]>([])
  const [selected, setSelected] = useState<SearchItem>({ market: 'sh', code: '600519', name: '贵州茅台', type: 'GP' })
  const [hotItems, setHotItems] = useState(HOT_FALLBACK)
  const [compareCode, setCompareCode] = useState('')
  const [compareData, setCompareData] = useState<QuoteResponse | null>(null)
  const [industry, setIndustry] = useState<{ peers: { code: string; name: string; pe: number; pb: number; change_pct: number; market_cap: number; is_target: boolean }[]; avg_pe: number | null; avg_pb: number | null } | null>(null)
  const [data, setData] = useState<QuoteResponse | null>(null)
  const [news, setNews] = useState<NewsItem[]>([])
  const [mode, setMode] = useState<'day' | 'minute'>('day')
  const [period, setPeriod] = useState<string>('day')
  const [multiDay, setMultiDay] = useState<number>(0)
  const [subIndicator, setSubIndicator] = useState<'macd' | 'kdj'>('macd')
  const [klineFullscreen, setKlineFullscreen] = useState(false)
  const [live, setLive] = useState(false)
  const [err, setErr] = useState('')
  const [searching, setSearching] = useState(false)
  const [allBars, setAllBars] = useState<KlineBar[]>([])
  // watchlist 版本号：星标/删除/添加后自增以刷新左栏
  const [wlVersion, setWlVersion] = useState(0)
  const timerRef = useRef<number | null>(null)
  const searchTimer = useRef<number | null>(null)

  // 加载每日热门股票
  useEffect(() => {
    api.getHotStocks().then((items) => {
      if (items && items.length >= 3) {
        setHotItems(items.map((i) => ({ code: i.code, name: i.name })))
      }
    }).catch(() => {})
  }, [])

  const load = useCallback(async (code: string, m: 'day' | 'minute', fresh: number) => {
    try {
      const q = await api.getQuote(code, 120, m, fresh)
      setData(q)
      setErr('')
    } catch {
      setErr('行情加载失败')
    }
  }, [])

  // 加载多日分时（2日/3日/4日/5日）：5分钟K线按交易日截取
  const loadMultiDay = useCallback(async (code: string, days: number) => {
    try {
      setAllBars([])
      const token = localStorage.getItem('financecrew_token')
      // A股用腾讯5分钟，美股用yfinance 5分钟（后端自动选择）
      const count = 48 * (days + 3)
      const r = await fetch(`/api/kline/${code}?period=5min&count=${count}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      const d = await r.json()
      if (d.bars) {
        // 按交易日分组，只保留最近N个交易日
        const dayMap = new Map<string, any[]>()
        for (const b of d.bars) {
          const day = b.date.split(' ')[0]
          if (!dayMap.has(day)) dayMap.set(day, [])
          dayMap.get(day)!.push(b)
        }
        const allDays = Array.from(dayMap.keys()).sort()
        const recentDays = allDays.slice(-days)
        const filtered = d.bars.filter((b: any) => recentDays.includes(b.date.split(' ')[0]))
        // 走日K模式展示5分钟K线蜡烛图
        setData(prev => ({
          brief: prev?.brief ?? {},
          kline: filtered.map((b: any) => ({ date: b.date, open: b.open, close: b.close, high: b.high, low: b.low, volume: b.volume })),
          tech: {},
          last_close: filtered[filtered.length - 1]?.close ?? null,
        }))
      }
      setErr('')
    } catch {
      setErr('多日分时加载失败')
    }
  }, [])

  // 加载多周期K线（周K/月K/分钟级）
  const loadPeriod = useCallback(async (code: string, p: string) => {
    try {
      setAllBars([]) // 清空旧日K全量数据，避免覆盖周期数据
      const token = localStorage.getItem('financecrew_token')
      const r = await fetch(`/api/kline/${code}?period=${p}&count=250`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      const d = await r.json()
      if (d.bars) {
        setData(prev => ({
          brief: prev?.brief ?? {},
          kline: d.bars.map((b: any) => ({ date: b.date, open: b.open, close: b.close, high: b.high, low: b.low, volume: b.volume })),
          tech: d.tech ?? {},
          last_close: d.tech?.price ?? null,
        }))
      } else if (d.detail) {
        setErr(d.detail)
      }
      setErr('')
    } catch {
      setErr('周期数据加载失败')
    }
  }, [])

  // 选中变化时加载行情 + 新闻 + 全量K线
  useEffect(() => {
    setMode('day')
    setPeriod('day')
    setMultiDay(0)
    setLive(false)
    load(selected.code, 'day', 0)
    api.getQuote(selected.code, 60, 'day', 0, 1).then((q) => {
      if (q.kline.length > 60) setAllBars(q.kline as KlineBar[])
    }).catch(() => setAllBars([]))
    api.getNews(selected.code).then((n) => setNews(n.news)).catch(() => setNews([]))
    api.getIndustry(selected.code).then(setIndustry).catch(() => setIndustry(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected.code])

  // 实时轮询
  useEffect(() => {
    if (!live) return
    // 切到非日K周期时停掉轮询，避免覆盖数据
    const effectiveLive = live && period === 'day'
    if (!effectiveLive) { if (timerRef.current) window.clearInterval(timerRef.current); return }
    timerRef.current = window.setInterval(() => load(selected.code, mode, 1), 15000)
    return () => { if (timerRef.current) window.clearInterval(timerRef.current) }
  }, [live, mode, period, selected.code, load])

  // 搜索防抖
  const doSearch = useCallback(async (q: string) => {
    if (!q.trim()) { setResults([]); return }
    setSearching(true)
    try {
      const r = await api.search(q.trim())
      setResults(r.results)
      // 如果只有一个结果，自动选中
      if (r.results.length === 1) {
        setSelected(r.results[0])
        setQuery(r.results[0].name)
        setResults([])
      }
    } catch { setResults([]) } finally { setSearching(false) }
  }, [])

  // 回车时：如果有搜索结果选第一个，否则搜完自动选
  const onSearchEnter = async () => {
    const q = query.trim()
    if (!q) return
    if (results.length > 0) {
      pick(results[0])
      return
    }
    setSearching(true)
    try {
      const r = await api.search(q)
      if (r.results.length > 0) {
        setSelected(r.results[0])
        setQuery(r.results[0].name)
      }
      setResults([])
    } catch { setResults([]) } finally { setSearching(false) }
  }

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

  // 自选股点击切换：尝试用名称（拉取 brief 后再展示真实名称）
  const pickWatchlistCode = (code: string) => {
    setSelected({ market: inferMarket(code), code: stripMarket(code), name: code, type: 'GP' })
    setQuery('')
    setResults([])
  }

  const b = data?.brief as {
    name?: string; price?: number; change_pct?: number
    pe?: number; pb?: number; turnover?: number; market_cap?: number
    open?: number; pre_close?: number; high?: number; low?: number
    limit_up?: number; limit_down?: number; volume?: number; amount?: number
    volume_ratio?: number
  } | undefined
  const change = b?.change_pct ?? 0
  const bars = (data?.kline as KlineBar[])?.filter((k) => k.date && typeof k.close === 'number') ?? []
  const minute = (data?.kline as MinutePoint[])?.filter((k) => k.time && typeof k.price === 'number') ?? []

  return (
    <div className="quote-page">
      {/* 顶部：搜索栏 + 热门股（跨三栏全宽） */}
      <div className="qp-search">
        <input
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSearchEnter()}
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

      <div className="qp-hot">
        {hotItems.map((h) => (
          <button
            key={h.code}
            className={`qp-hot-item ${selected.code === h.code ? 'active' : ''}`}
            onClick={() => pick({ market: h.code.startsWith('hk') ? 'hk' : h.code.startsWith('us') ? 'us' : 'sh', code: h.code, name: h.name, type: 'GP' })}
          >
            {h.name}
          </button>
        ))}
      </div>

      {err && !data && <div className="error-box">{err}</div>}

      {/* 三栏主区 */}
      {data && (
        <div className="quote-grid">
          {/* ========== 左栏：自选股 ========== */}
          <aside className="qp-left">
            <WatchlistPanel
              currentCode={selected.code}
              wlVersion={wlVersion}
              onPick={pickWatchlistCode}
              onAdd={async (codeToAdd) => {
                try {
                  const p = await api.getProfile()
                  const list = p.watchlist || []
                  const next = [...new Set([...list, codeToAdd])]
                  await api.saveProfile({ watchlist: next })
                  setWlVersion((v) => v + 1)
                } catch { /* ignore */ }
              }}
              onRemove={async (codeToRemove) => {
                try {
                  const p = await api.getProfile()
                  const list = p.watchlist || []
                  const next = list.filter((c) => c !== codeToRemove)
                  await api.saveProfile({ watchlist: next })
                  setWlVersion((v) => v + 1)
                } catch { /* ignore */ }
              }}
            />

            {/* 行业对比移到左栏底部 */}
            {industry && industry.peers.length > 0 && (
              <details className="industry-compare">
                <summary className="industry-head">行业对比 {industry.avg_pe != null && <span className="industry-avg">均PE {industry.avg_pe}</span>}</summary>
                <div className="industry-peers">
                  {industry.peers.map((p) => (
                    <div key={p.code} className={`industry-peer ${p.is_target ? 'target' : ''}`}>
                      <span className="industry-name">{p.name}</span>
                      <span className="industry-code">{p.code}</span>
                      <span className="industry-pe">PE {p.pe?.toFixed(1) ?? '--'}</span>
                      <span className="industry-pb">PB {p.pb?.toFixed(2) ?? '--'}</span>
                      <span className={`industry-chg ${(p.change_pct ?? 0) >= 0 ? 'up' : 'down'}`}>
                        {p.change_pct != null ? `${p.change_pct >= 0 ? '+' : ''}${p.change_pct.toFixed(2)}%` : ''}
                      </span>
                    </div>
                  ))}
                </div>
              </details>
            )}
          </aside>

          {/* ========== 中栏：标题+操作 / 周期 / K线 / 对比 ========== */}
          <main className="qp-center">
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
                  <StarButton code={selected.code} />
                  <input
                    className="compare-input"
                    placeholder="对比代码"
                    value={compareCode}
                    onChange={(e) => setCompareCode(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && compareCode.trim()) {
                        api.getQuote(compareCode.trim(), 1, 'day', 0)
                          .then((q) => setCompareData(q))
                          .catch(() => setCompareData(null))
                      }
                    }}
                  />
                  {compareData && <button className="mode-btn" onClick={() => { setCompareData(null); setCompareCode('') }}>清除对比</button>}
                  <button className={`live-btn ${live ? 'on' : ''}`} onClick={() => setLive((v) => !v)}>
                    {live ? '● 实时' : '○ 实时'}
                  </button>
                </div>
              </div>

              {/* 周期切换工具栏 - 单独一行 */}
              <div className="qp-toolbar">
                <select className="period-select" value={multiDay ? `day${multiDay}` : (mode === 'minute' && !multiDay ? 'minute' : 'none')} onChange={(e) => {
                  const v = e.target.value
                  if (v === 'minute') { setMode('minute'); setPeriod(''); setMultiDay(0); load(selected.code, 'minute', 0) }
                  else if (v.startsWith('day')) { const n = parseInt(v.slice(3)); setMode('day'); setMultiDay(n); setPeriod(''); loadMultiDay(selected.code, n) }
                }}>
                  <option value="none" disabled>选择分时</option>
                  <option value="minute">分时</option>
                  <option value="day2">2日</option>
                  <option value="day3">3日</option>
                  <option value="day4">4日</option>
                  <option value="day5">5日</option>
                </select>
                <span className="toolbar-sep" />
                <button className={`mode-btn ${mode === 'day' && period === 'day' ? 'active' : ''}`} onClick={() => { setMode('day'); setPeriod('day'); load(selected.code, 'day', 0) }}>日K</button>
                <button className={`mode-btn ${period === 'week' ? 'active' : ''}`} onClick={() => { setPeriod('week'); setMode('day'); loadPeriod(selected.code, 'week') }}>周K</button>
                <button className={`mode-btn ${period === 'month' ? 'active' : ''}`} onClick={() => { setPeriod('month'); setMode('day'); loadPeriod(selected.code, 'month') }}>月K</button>
                <span className="toolbar-sep" />
                <button className={`mode-btn ${period === '5min' ? 'active' : ''}`} onClick={() => { setPeriod('5min'); setMode('day'); loadPeriod(selected.code, '5min') }}>5分</button>
                <button className={`mode-btn ${period === '15min' ? 'active' : ''}`} onClick={() => { setPeriod('15min'); setMode('day'); loadPeriod(selected.code, '15min') }}>15分</button>
                <button className={`mode-btn ${period === '30min' ? 'active' : ''}`} onClick={() => { setPeriod('30min'); setMode('day'); loadPeriod(selected.code, '30min') }}>30分</button>
                <button className={`mode-btn ${period === '60min' ? 'active' : ''}`} onClick={() => { setPeriod('60min'); setMode('day'); loadPeriod(selected.code, '60min') }}>60分</button>
                <span className="toolbar-sep" />
                <button className={`mode-btn ${subIndicator === 'macd' ? 'active' : ''}`} onClick={() => setSubIndicator('macd')}>MACD</button>
                <button className={`mode-btn ${subIndicator === 'kdj' ? 'active' : ''}`} onClick={() => setSubIndicator('kdj')}>KDJ</button>
                <span className="toolbar-sep" />
                <button className="mode-btn" onClick={() => setKlineFullscreen(true)}>全屏</button>
              </div>

              <div className="qp-chart">
                <KLineChart
                  bars={allBars.length > 60 ? allBars : bars}
                  minute={minute}
                  lastClose={data.last_close ?? null}
                  symbol={b?.name ?? selected.name}
                  mode={mode}
                  onMode={(m) => { setMode(m); load(selected.code, m, 0) }}
                  dataDate={data.data_date}
                  isToday={data.is_today}
                  subIndicator={subIndicator}
                  onSubIndicator={setSubIndicator}
                  fullscreen={klineFullscreen}
                  onFullscreen={setKlineFullscreen}
                />
              </div>

              {/* 对比表格（K线下方） */}
              {compareData && compareData.brief && (() => {
                const cb = compareData.brief as any
                const rows: [string, any, any][] = [
                  ['名称', b?.name ?? selected.name, cb?.name ?? compareCode],
                  ['现价', b?.price ?? '--', cb?.price ?? '--'],
                  ['涨跌幅', `${(b?.change_pct ?? 0) >= 0 ? '+' : ''}${b?.change_pct ?? '--'}%`, `${(cb?.change_pct ?? 0) >= 0 ? '+' : ''}${cb?.change_pct ?? '--'}%`],
                  ['PE', b?.pe ?? '--', cb?.pe ?? '--'],
                  ['PB', b?.pb ?? '--', cb?.pb ?? '--'],
                  ['换手率', b?.turnover != null ? `${b.turnover}%` : '--', cb?.turnover != null ? `${cb.turnover}%` : '--'],
                  ['市值(亿)', b?.market_cap ?? '--', cb?.market_cap ?? '--'],
                ]
                return (
                  <table className="compare-table">
                    <thead><tr><th>指标</th><th>{b?.name ?? selected.name}</th><th>{cb?.name ?? compareCode}</th></tr></thead>
                    <tbody>
                      {rows.map(([label, v1, v2], i) => (
                        <tr key={i}>
                          <td className="compare-label">{label}</td>
                          <td>{v1}</td>
                          <td>{v2}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )
              })()}
            </div>
          </main>

          {/* ========== 右栏：盘口 + 资金流向 + K线形态 ========== */}
          <aside className="qp-right">
            <OrderBookPanel brief={data.brief as any} />
            <FundFlowCard code={selected.code} />
            <PatternCard code={selected.code} />
          </aside>
        </div>
      )}

      {/* 底部新闻（跨三栏全宽） */}
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
  )
}

// ========== 左栏：自选股面板 ==========
function WatchlistPanel({
  currentCode, wlVersion, onPick, onAdd, onRemove,
}: {
  currentCode: string
  wlVersion: number
  onPick: (code: string) => void
  onAdd: (code: string) => void
  onRemove: (code: string) => void
}) {
  const [codes, setCodes] = useState<string[]>([])
  const [items, setItems] = useState<{ code: string; name: string; price?: number; change_pct?: number }[]>([])
  const [loading, setLoading] = useState(false)
  const [addInput, setAddInput] = useState('')
  const [searchHits, setSearchHits] = useState<SearchItem[]>([])

  // 1) 拉取自选股 code 列表
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.getProfile().then((p) => {
      if (!cancelled) { setCodes(p.watchlist || []); setLoading(false) }
    }).catch(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [wlVersion])

  // 2) 根据 code 列表并发拉取行情（名称+价格+涨跌幅）
  useEffect(() => {
    if (codes.length === 0) { setItems([]); return }
    let cancelled = false
    const results: { code: string; name: string; price?: number; change_pct?: number }[] = []
    let done = 0
    codes.forEach((code) => {
      api.getQuote(code, 1, 'day', 0).then((q) => {
        if (cancelled) return
        const bf = q.brief as any
        results.push({ code, name: bf?.name ?? code, price: bf?.price, change_pct: bf?.change_pct })
      }).catch(() => {
        if (!cancelled) results.push({ code, name: code })
      }).finally(() => {
        done += 1
        if (!cancelled && done === codes.length) setItems([...results])
      })
    })
    return () => { cancelled = true }
  }, [codes])

  const onSearchAdd = (v: string) => {
    setAddInput(v)
    if (!v.trim()) { setSearchHits([]); return }
    // 复用搜索防抖
    window.clearTimeout((onSearchAdd as any)._t)
    ;(onSearchAdd as any)._t = window.setTimeout(async () => {
      try {
        const r = await api.search(v.trim())
        setSearchHits(r.results.slice(0, 5))
      } catch { setSearchHits([]) }
    }, 300)
  }

  const commitAdd = (code: string) => {
    onAdd(code)
    setAddInput('')
    setSearchHits([])
  }

  return (
    <div className="qp-watchlist">
      <div className="qp-watchlist-head">自选股</div>

      <div className="qp-watchlist-add">
        <input
          value={addInput}
          onChange={(e) => onSearchAdd(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              if (searchHits.length > 0) {
                commitAdd(`${inferMarket(searchHits[0].code)}${searchHits[0].code}`)
              } else if (addInput.trim()) {
                // 纯代码兜底
                const c = addInput.trim()
                commitAdd(/^\d{6}$/.test(c) || /^(sh|sz|hk|us)/i.test(c) ? c : `sh${c}`)
              }
            }
          }}
          placeholder="添加代码/名称"
        />
        {searchHits.length > 0 && (
          <div className="qp-results qp-watchlist-results">
            {searchHits.map((r) => (
              <button key={r.code} className="qp-result" onClick={() => commitAdd(`${inferMarket(r.code)}${r.code}`)}>
                <span className={`qp-market m-${r.market}`}>{r.market.toUpperCase()}</span>
                <span className="qp-name">{r.name}</span>
                <span className="qp-code">{r.code}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {loading && <div className="qp-watchlist-empty">加载中...</div>}
      {!loading && codes.length === 0 && (
        <div className="qp-watchlist-empty">暂无自选股，搜索添加</div>
      )}
      <div className="qp-watchlist-list">
        {items.map((it) => {
          const stripCode = stripMarket(it.code)
          const active = stripCode === currentCode || it.code === currentCode
          const chg = it.change_pct
          return (
            <div
              key={it.code}
              className={`qp-watchlist-item ${active ? 'active' : ''}`}
              onClick={() => onPick(it.code)}
            >
              <span className="qp-watchlist-name">{it.name}</span>
              <span className="qp-watchlist-code">{stripCode}</span>
              <span className={`qp-watchlist-chg ${chg == null ? '' : chg >= 0 ? 'up' : 'down'}`}>
                {chg != null ? `${chg >= 0 ? '+' : ''}${chg.toFixed(2)}%` : (it.price != null ? it.price : '--')}
              </span>
              <button
                className="qp-watchlist-del"
                title="移除"
                onClick={(e) => { e.stopPropagation(); onRemove(it.code) }}
              >×</button>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ========== 右栏：盘口数据表 ==========
function OrderBookPanel({ brief }: { brief: Record<string, unknown> }) {
  const b = brief as {
    name?: string
    open?: number; pre_close?: number; high?: number; low?: number
    limit_up?: number; limit_down?: number
    volume?: number; amount?: number; turnover?: number; volume_ratio?: number
    pe?: number; pb?: number; market_cap?: number
    circ_market_cap?: number
    price?: number; change_pct?: number
  }
  // 港股/美股没有涨跌停概念，按是否存在判断
  const hasLimit = b.limit_up != null || b.limit_down != null

  // 单元格：label + value，涨跌上色（红涨绿跌中国习惯 = up/down 变量）
  const Cell = ({ label, value, tone }: { label: string; value: React.ReactNode; tone?: 'up' | 'down' }) => (
    <div className="qp-ob-cell">
      <span className="qp-ob-label">{label}</span>
      <span className={`qp-ob-value ${tone ?? ''}`}>{value}</span>
    </div>
  )

  const fmtNum = (v?: number) => (v == null ? '--' : v.toFixed(2))
  const fmtVol = (v?: number) => (v == null ? '--' : v >= 10000 ? (v / 10000).toFixed(2) + '万手' : v.toFixed(0) + '手')
  const fmtAmt = (v?: number) => (v == null ? '--' : v >= 10000 ? (v / 10000).toFixed(2) + '亿' : v.toFixed(0) + '万')
  const fmtCap = (v?: number) => (v == null ? '--' : v >= 10000 ? (v / 10000).toFixed(2) + '万亿' : v.toFixed(1) + '亿')

  return (
    <div className="qp-orderbook">
      <div className="qp-ob-row">
        <Cell label="今开" value={fmtNum(b.open)} tone={b.open != null && b.pre_close != null ? (b.open >= b.pre_close ? 'up' : 'down') : undefined} />
        <Cell label="昨收" value={fmtNum(b.pre_close)} />
        <Cell label="最高" value={fmtNum(b.high)} tone={b.high != null && b.pre_close != null ? (b.high >= b.pre_close ? 'up' : 'down') : undefined} />
        <Cell label="最低" value={fmtNum(b.low)} tone={b.low != null && b.pre_close != null ? (b.low >= b.pre_close ? 'up' : 'down') : undefined} />
        <Cell label="涨停" value={hasLimit ? fmtNum(b.limit_up) : '--'} tone="up" />
        <Cell label="跌停" value={hasLimit ? fmtNum(b.limit_down) : '--'} tone="down" />
      </div>
      <div className="qp-ob-row">
        <Cell label="成交量" value={fmtVol(b.volume)} />
        <Cell label="成交额" value={fmtAmt(b.amount)} />
        <Cell label="换手率" value={b.turnover != null ? `${b.turnover}%` : '--'} />
        <Cell label="量比" value={fmtNum(b.volume_ratio)} />
        <Cell label="PE" value={fmtNum(b.pe)} />
        <Cell label="PB" value={fmtNum(b.pb)} />
      </div>
      <div className="qp-ob-row">
        <Cell label="总市值" value={fmtCap(b.market_cap)} />
        <Cell label="流通市值" value={fmtCap(b.circ_market_cap)} />
        <Cell label="现价" value={fmtNum(b.price)} tone={b.change_pct != null ? (b.change_pct >= 0 ? 'up' : 'down') : undefined} />
        <Cell label="涨跌幅" value={b.change_pct != null ? `${b.change_pct >= 0 ? '+' : ''}${b.change_pct}%` : '--'} tone={b.change_pct != null ? (b.change_pct >= 0 ? 'up' : 'down') : undefined} />
      </div>
    </div>
  )
}

// ========== 资金流向卡片 ==========
function FundFlowCard({ code }: { code: string }) {
  const [data, setData] = useState<any>(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    // 后端API获取资金流向（服务器环境直连东财，本地开发可能被代理拦截）
    const token = localStorage.getItem('financecrew_token')
    fetch(`/api/fund-flow/${code}?days=5`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then(r => r.json())
      .then(d => { if (!cancelled) { setData(d); setLoading(false) } })
      .catch(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [code])

  if (loading) return <div className="qp-card"><span className="qp-card-title">资金流向</span><span className="qp-card-loading">加载中...</span></div>
  if (!data || data.error || data.latest_main_net == null) return (
    <div className="qp-card">
      <span className="qp-card-title">资金流向</span>
      <span className="qp-card-empty">东财接口被代理拦截，服务器部署后可用</span>
    </div>
  )

  const mainNet = data.latest_main_net ?? 0
  const isPositive = mainNet >= 0
  const history = (data.history ?? []).slice(-5).reverse()

  return (
    <div className="qp-card">
      <span className="qp-card-title">资金流向 {data.latest_date}</span>
      <div className="qp-card-row">
        <span className={isPositive ? 'text-up' : 'text-down'}>
          {isPositive ? '▲' : '▼'} 主力{isPositive ? '净流入' : '净流出'} {Math.abs(mainNet)}亿
        </span>
      </div>
      <div className="qp-card-row">
        <span>超大单 {data.latest_super_net >= 0 ? '+' : ''}{data.latest_super_net}亿</span>
        <span>大单 {data.latest_large_net >= 0 ? '+' : ''}{data.latest_large_net}亿</span>
      </div>
      {history.length > 1 && (
        <div className="qp-card-mini-chart">
          {history.map((h: any, i: number) => (
            <div key={i} className="qp-bar-item">
              <div
                className={`qp-bar ${h.main_net >= 0 ? 'up' : 'down'}`}
                style={{ height: `${Math.min(Math.abs(h.main_net) * 8, 24)}px` }}
                title={`${h.date}: ${h.main_net}亿`}
              />
              <span className="qp-bar-date">{h.date.slice(5)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ========== K线形态卡片 ==========
function PatternCard({ code }: { code: string }) {
  const [data, setData] = useState<any>(null)

  useEffect(() => {
    let cancelled = false
    const token = localStorage.getItem('financecrew_token')
    fetch(`/api/patterns/${code}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then(r => r.json())
      .then(d => { if (!cancelled) setData(d) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [code])

  if (!data || !data.pattern) return null

  const dir = data.direction
  const dirClass = dir === '看涨' ? 'text-up' : dir === '看跌' ? 'text-down' : 'text-neutral'

  return (
    <div className="qp-card">
      <span className="qp-card-title">K线形态</span>
      <div className="qp-card-row">
        <span className={dirClass}>{data.pattern}</span>
        <span className={`qp-badge ${dir === '看涨' ? 'badge-up' : dir === '看跌' ? 'badge-down' : 'badge-neutral'}`}>{dir}</span>
      </div>
      <p className="qp-card-desc">{data.description}</p>
      {data.all_patterns && data.all_patterns.length > 1 && (
        <div className="qp-pattern-list">
          {data.all_patterns.slice(0, 4).map((p: any, i: number) => (
            <div key={i} className="qp-pattern-item">
              <span className="qp-pattern-date">{p.date.slice(5)}</span>
              <span className={p.direction === '看涨' ? 'text-up' : p.direction === '看跌' ? 'text-down' : 'text-neutral'}>
                {p.name}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
