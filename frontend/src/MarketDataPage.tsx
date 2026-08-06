import { useEffect, useState, useCallback } from 'react'
import { api } from './api'

// ============== 类型定义 ==============
type PageTab = 'sector' | 'screener' | 'margin' | 'north'
type SectorType = 'concept' | 'industry'

interface Sector {
  code: string
  name: string
  change_pct: number | null
  turnover: number | null
  main_net_inflow: number | null
  main_net_pct: number | null
  leading_stock: string
  leading_code: string
}

interface Stock {
  code: string
  name: string
  price: number | null
  change_pct: number | null
  pe: number | null
  pb: number | null
  turnover: number | null
  market_cap: number | null
  negotiable_cap: number | null
}

interface MarginRow {
  exchange: string
  code: string
  name: string
  margin_balance: number | null
  margin_buy: number | null
  margin_repay: number | null
  short_volume: number | null
  short_sell: number | null
  short_repay: number | null
}

interface NorthDay {
  date: string
  net_buy: number | null
  buy_amount: number | null
  sell_amount: number | null
  cumulative: number | null
}

interface NorthStockDay {
  date: string
  close: number | null
  change_pct: number | null
  hold_shares: number | null
  hold_value: number | null
  hold_pct: number | null
  change_shares: number | null
}

// ============== 工具函数 ==============
const fmtNum = (v: number | null | undefined, digits = 2): string => {
  if (v === null || v === undefined || v !== v) return '—'
  return v.toLocaleString('en-US', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}
const fmtPct = (v: number | null | undefined): string => {
  if (v === null || v === undefined || v !== v) return '—'
  return `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`
}
const pctClass = (v: number | null | undefined): string => {
  if (v === null || v === undefined || v !== v) return ''
  return v >= 0 ? 'up' : 'down'
}
// 金额（元）-> 亿
const fmtYi = (v: number | null | undefined): string => {
  if (v === null || v === undefined || v !== v) return '—'
  const yi = v / 1e8
  return fmtNum(yi, 2)
}
// 万元 -> 亿
const fmtWanToYi = (v: number | null | undefined): string => {
  if (v === null || v === undefined || v !== v) return '—'
  return fmtNum(v / 1e4, 2)
}
// 股数 -> 万股
const fmtWanGu = (v: number | null | undefined): string => {
  if (v === null || v === undefined || v !== v) return '—'
  return fmtNum(v / 1e4, 2)
}
const fmtYiCap = (v: number | null | undefined): string => {
  if (v === null || v === undefined || v !== v) return '—'
  return fmtNum(v, 2)
}

// ============== 主组件 ==============
export default function MarketDataPage() {
  const [tab, setTab] = useState<PageTab>('sector')

  return (
    <div className="pane">
      <div className="pane-head">
        <h2>市场数据</h2>
        <div className="bt-tabs">
          <button className={tab === 'sector' ? 'active' : ''} onClick={() => setTab('sector')}>板块轮动</button>
          <button className={tab === 'screener' ? 'active' : ''} onClick={() => setTab('screener')}>条件选股</button>
          <button className={tab === 'margin' ? 'active' : ''} onClick={() => setTab('margin')}>融资融券</button>
          <button className={tab === 'north' ? 'active' : ''} onClick={() => setTab('north')}>北向资金</button>
        </div>
      </div>

      {tab === 'sector' && <SectorTab />}
      {tab === 'screener' && <ScreenerTab />}
      {tab === 'margin' && <MarginTab />}
      {tab === 'north' && <NorthTab />}
    </div>
  )
}

// ============== 通用状态条 ==============
function StatusBar({ loading, error }: { loading: boolean; error: string }) {
  if (loading) return <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-2)' }}>加载中...</div>
  if (error) return <div className="alert-error" style={{ padding: 12 }}>{error}</div>
  return null
}

// ============== Tab1: 板块轮动 ==============
function SectorTab() {
  const [type, setType] = useState<SectorType>('concept')
  const [sectors, setSectors] = useState<Sector[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const url = type === 'concept' ? '/api/sectors/concepts?limit=20' : '/api/sectors/industries?limit=20'
      const d = await api.get<any>(url)
      if (d.error) { setError(d.error); setSectors([]); return }
      setSectors(d.sectors || [])
    } catch (e: any) { setError(e.message || '加载失败'); setSectors([]) }
    finally { setLoading(false) }
  }, [type])

  useEffect(() => { load() }, [load])

  // 板块轮动30秒自动刷新
  const [autoRefresh, setAutoRefresh] = useState(true)
  useEffect(() => {
    if (!autoRefresh) return
    const t = window.setInterval(() => load(), 30000)
    return () => window.clearInterval(t)
  }, [autoRefresh, load])

  return (
    <>
      <div className="bt-tabs" style={{ marginBottom: 12 }}>
        <button className={type === 'concept' ? 'active' : ''} onClick={() => setType('concept')}>概念板块</button>
        <button className={type === 'industry' ? 'active' : ''} onClick={() => setType('industry')}>行业板块</button>
        <button className={autoRefresh ? 'active' : ''} onClick={() => setAutoRefresh(v => !v)} style={{ marginLeft: 'auto' }}>
          {autoRefresh ? '● 自动刷新' : '○ 自动刷新'}
        </button>
      </div>

      <StatusBar loading={loading} error={error} />

      {!loading && !error && (
        <table className="portfolio-table">
          <thead>
            <tr>
              <th style={{ width: 50 }}>排名</th>
              <th>板块名称</th>
              <th>涨跌幅</th>
              <th>换手率</th>
              <th>主力净流入(亿)</th>
              <th>领涨股</th>
            </tr>
          </thead>
          <tbody>
            {sectors.length === 0 && (
              <tr><td className="empty-row" colSpan={6}>暂无数据</td></tr>
            )}
            {sectors.map((s, i) => (
              <tr key={s.code || i}>
                <td style={{ color: 'var(--text-3)', fontFamily: 'var(--mono)' }}>{i + 1}</td>
                <td className="pf-name">{s.name}<span className="pf-code">{s.code}</span></td>
                <td className={pctClass(s.change_pct)} style={{ fontFamily: 'var(--mono)' }}>{fmtPct(s.change_pct)}</td>
                <td style={{ fontFamily: 'var(--mono)' }}>{fmtNum(s.turnover, 2)}%</td>
                <td style={{ fontFamily: 'var(--mono)' }}>{fmtNum(s.main_net_inflow, 2)}</td>
                <td>{s.leading_stock || '—'}{s.leading_code && <span className="pf-code">{s.leading_code}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}

// ============== Tab2: 条件选股 ==============
function ScreenerTab() {
  const [peMin, setPeMin] = useState('')
  const [peMax, setPeMax] = useState('')
  const [pbMin, setPbMin] = useState('')
  const [pbMax, setPbMax] = useState('')
  const [chgMin, setChgMin] = useState('')
  const [chgMax, setChgMax] = useState('')
  const [sortBy, setSortBy] = useState('change_pct')
  const [sortDesc, setSortDesc] = useState(true)
  const [stocks, setStocks] = useState<Stock[]>([])
  const [totalMarket, setTotalMarket] = useState<number | null>(null)
  const [matched, setMatched] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [hasResult, setHasResult] = useState(false)
  const [limit, setLimit] = useState('50')

  const screen = async () => {
    setLoading(true); setError('')
    try {
      const p = new URLSearchParams()
      p.set('limit', limit || '50')
      if (peMin) p.set('pe_min', peMin)
      if (peMax) p.set('pe_max', peMax)
      if (pbMin) p.set('pb_min', pbMin)
      if (pbMax) p.set('pb_max', pbMax)
      if (chgMin) p.set('change_pct_min', chgMin)
      if (chgMax) p.set('change_pct_max', chgMax)
      p.set('sort_by', sortBy)
      p.set('sort_desc', sortDesc ? 'true' : 'false')
      const d = await api.get<any>(`/api/screener?${p.toString()}`)
      if (d.error) { setError(d.error); return }
      setStocks(d.stocks || [])
      setTotalMarket(d.total_market ?? null)
      setMatched(d.matched ?? null)
      setHasResult(true)
    } catch (e: any) { setError(e.message || '筛选失败') }
    finally { setLoading(false) }
  }

  return (
    <>
      <div className="backtest-controls">
        <label className="bt-param-item">
          <span>PE</span>
          <input className="bt-param-input" style={{ width: 60 }} type="number" placeholder="最小" value={peMin} onChange={e => setPeMin(e.target.value)} />
          <span>~</span>
          <input className="bt-param-input" style={{ width: 60 }} type="number" placeholder="最大" value={peMax} onChange={e => setPeMax(e.target.value)} />
        </label>
        <label className="bt-param-item">
          <span>PB</span>
          <input className="bt-param-input" style={{ width: 60 }} type="number" placeholder="最小" value={pbMin} onChange={e => setPbMin(e.target.value)} />
          <span>~</span>
          <input className="bt-param-input" style={{ width: 60 }} type="number" placeholder="最大" value={pbMax} onChange={e => setPbMax(e.target.value)} />
        </label>
        <label className="bt-param-item">
          <span>涨跌幅%</span>
          <input className="bt-param-input" style={{ width: 60 }} type="number" placeholder="最小" value={chgMin} onChange={e => setChgMin(e.target.value)} />
          <span>~</span>
          <input className="bt-param-input" style={{ width: 60 }} type="number" placeholder="最大" value={chgMax} onChange={e => setChgMax(e.target.value)} />
        </label>
        <select className="alert-select" style={{ flex: '0 0 auto', minWidth: 110 }} value={sortBy} onChange={e => setSortBy(e.target.value)}>
          <option value="change_pct">排序: 涨跌幅</option>
          <option value="turnover">排序: 换手率</option>
          <option value="pe">排序: PE</option>
          <option value="pb">排序: PB</option>
          <option value="market_cap">排序: 市值</option>
        </select>
        <select className="alert-select" style={{ flex: '0 0 auto', minWidth: 80 }} value={sortDesc ? 'desc' : 'asc'} onChange={e => setSortDesc(e.target.value === 'desc')}>
          <option value="desc">降序</option>
          <option value="asc">升序</option>
        </select>
        <label className="bt-param-item">
          <span>显示</span>
          <input className="bt-param-input" style={{ width: 50 }} type="number" value={limit} onChange={e => setLimit(e.target.value)} />
          <span>只</span>
        </label>
        <button className="btn-primary" onClick={screen} disabled={loading}>{loading ? '筛选中...' : '筛选'}</button>
      </div>

      {hasResult && (
        <div style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 8, fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>
          共 {totalMarket ?? '—'} 只 | 筛选出 {matched ?? '—'} 只 | 显示 {stocks.length} 只
        </div>
      )}

      <StatusBar loading={loading} error={error} />

      {!loading && !error && hasResult && (
        <table className="portfolio-table">
          <thead>
            <tr>
              <th>代码</th>
              <th>名称</th>
              <th>最新价</th>
              <th>涨跌幅</th>
              <th>PE</th>
              <th>PB</th>
              <th>换手率</th>
              <th>市值(亿)</th>
            </tr>
          </thead>
          <tbody>
            {stocks.length === 0 && (
              <tr><td className="empty-row" colSpan={8}>无符合条件的股票</td></tr>
            )}
            {stocks.map((s, i) => (
              <tr key={s.code || i}>
                <td style={{ fontFamily: 'var(--mono)', color: 'var(--text-3)' }}>{s.code}</td>
                <td className="pf-name">{s.name}</td>
                <td style={{ fontFamily: 'var(--mono)' }}>{fmtNum(s.price, 2)}</td>
                <td className={pctClass(s.change_pct)} style={{ fontFamily: 'var(--mono)' }}>{fmtPct(s.change_pct)}</td>
                <td style={{ fontFamily: 'var(--mono)' }}>{fmtNum(s.pe, 2)}</td>
                <td style={{ fontFamily: 'var(--mono)' }}>{fmtNum(s.pb, 2)}</td>
                <td style={{ fontFamily: 'var(--mono)' }}>{fmtNum(s.turnover, 2)}%</td>
                <td style={{ fontFamily: 'var(--mono)' }}>{fmtYiCap(s.market_cap)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  )
}

// ============== Tab3: 融资融券 ==============
function MarginTab() {
  const today = new Date()
  const yesterday = new Date(today.getTime() - 86400000)
  const defaultDate = yesterday.toISOString().slice(0, 10).replace(/-/g, '')
  const [dateInput, setDateInput] = useState(yesterday.toISOString().slice(0, 10))
  const [marginTop, setMarginTop] = useState<MarginRow[]>([])
  const [shortTop, setShortTop] = useState<MarginRow[]>([])
  const [actDate, setActDate] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [hasResult, setHasResult] = useState(false)

  const query = async () => {
    setLoading(true); setError('')
    try {
      const d8 = dateInput.replace(/-/g, '') || defaultDate
      const d = await api.get<any>(`/api/margin/top?date=${d8}&limit=20`)
      if (d.error) { setError(d.error); return }
      setMarginTop(d.margin_top || [])
      setShortTop(d.short_top || [])
      setActDate(d.date || d8)
      setHasResult(true)
    } catch (e: any) { setError(e.message || '查询失败') }
    finally { setLoading(false) }
  }

  return (
    <>
      <div className="backtest-controls">
        <label className="bt-param-item">
          <span>日期</span>
          <input className="alert-input" type="date" value={dateInput} onChange={e => setDateInput(e.target.value)} style={{ flex: '0 0 auto', minWidth: 150 }} />
        </label>
        <button className="btn-primary" onClick={query} disabled={loading}>{loading ? '查询中...' : '查询'}</button>
      </div>

      <StatusBar loading={loading} error={error} />

      {!loading && !error && hasResult && (
        <>
          {actDate && <div style={{ fontSize: 12, color: 'var(--text-2)', marginBottom: 8 }}>日期：{actDate}</div>}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 20 }}>
            <div>
              <h4 style={{ fontSize: 13, margin: '0 0 8px' }}>融资余额 TOP20</h4>
              <table className="portfolio-table">
                <thead>
                  <tr>
                    <th>代码</th>
                    <th>名称</th>
                    <th>融资余额(亿)</th>
                    <th>融资买入额(亿)</th>
                    <th>融券余量(万股)</th>
                  </tr>
                </thead>
                <tbody>
                  {marginTop.length === 0 && <tr><td className="empty-row" colSpan={5}>暂无数据</td></tr>}
                  {marginTop.map((r, i) => (
                    <tr key={(r.exchange || '') + r.code + i}>
                      <td style={{ fontFamily: 'var(--mono)', color: 'var(--text-3)' }}>{r.code}</td>
                      <td className="pf-name">{r.name}</td>
                      <td style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{fmtYi(r.margin_balance)}</td>
                      <td style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{fmtYi(r.margin_buy)}</td>
                      <td style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{fmtWanGu(r.short_volume)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h4 style={{ fontSize: 13, margin: '0 0 8px' }}>融券余量 TOP20</h4>
              <table className="portfolio-table">
                <thead>
                  <tr>
                    <th>代码</th>
                    <th>名称</th>
                    <th>融券余量(万股)</th>
                    <th>融券卖出量(万股)</th>
                    <th>融资余额(亿)</th>
                  </tr>
                </thead>
                <tbody>
                  {shortTop.length === 0 && <tr><td className="empty-row" colSpan={5}>暂无数据</td></tr>}
                  {shortTop.map((r, i) => (
                    <tr key={(r.exchange || '') + r.code + i}>
                      <td style={{ fontFamily: 'var(--mono)', color: 'var(--text-3)' }}>{r.code}</td>
                      <td className="pf-name">{r.name}</td>
                      <td style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{fmtWanGu(r.short_volume)}</td>
                      <td style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{fmtWanGu(r.short_sell)}</td>
                      <td style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{fmtYi(r.margin_balance)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </>
  )
}

// ============== Tab4: 北向资金 ==============
function NorthTab() {
  const [symbol, setSymbol] = useState('600519')
  const [market, setMarket] = useState<'沪股通' | '深股通'>('沪股通')
  const [overview, setOverview] = useState<{ latest_date: string; latest_net: number | null; cumulative: number | null; history: NorthDay[] } | null>(null)
  const [stockHist, setStockHist] = useState<NorthStockDay[]>([])
  const [topStocks, setTopStocks] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const query = useCallback(async (sym?: string) => {
    const s = (sym ?? symbol).trim()
    if (!s) return
    setLoading(true); setError('')
    try {
      const [ov, sh, ts] = await Promise.all([
        api.get<any>('/api/north-flow/overview'),
        api.get<any>(`/api/north-flow/stock/${s}`),
        api.get<any>(`/api/north-flow/top-stocks?market=${market}&period=5日排行`),
      ])
      if (ov.error && sh.error && ts.error) { setError('全部数据源不可用'); return }
      if (!ov.error) setOverview(ov)
      if (!sh.error) setStockHist(sh.history || [])
      if (!ts.error) setTopStocks(ts.top || [])
    } catch (e: any) { setError(e.message || '查询失败') }
    finally { setLoading(false) }
  }, [symbol, market])

  useEffect(() => { query('600519') }, [])

  // 北向排行30秒自动刷新
  const [autoRefresh, setAutoRefresh] = useState(true)
  useEffect(() => {
    if (!autoRefresh) return
    const t = window.setInterval(() => {
      // 只刷新排行数据，不刷新个股历史
      api.get<any>(`/api/north-flow/top-stocks?market=${market}&period=5日排行`).then(ts => {
        if (!ts.error) setTopStocks(ts.top || [])
      }).catch(() => {})
    }, 30000)
    return () => window.clearInterval(t)
  }, [autoRefresh, market])

  // SVG 折线图：最近30天净流入趋势
  const recent = overview?.history?.slice(-30) ?? []
  const W = 760, H = 160, PAD = 40
  const xLabelN = Math.min(recent.length, 6)
  let pathD = ''
  let areaD = ''
  let points: { x: number; y: number; v: number; date: string }[] = []
  if (recent.length > 1) {
    const vals = recent.map(r => r.net_buy ?? 0)
    const maxV = Math.max(...vals), minV = Math.min(...vals)
    const range = maxV - minV || 1
    const stepX = (W - PAD * 2) / (recent.length - 1)
    points = recent.map((r, i) => {
      const v = r.net_buy ?? 0
      const x = PAD + i * stepX
      const y = PAD + (H - PAD * 2) * (1 - (v - minV) / range)
      return { x, y, v, date: r.date }
    })
    pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')
    const baseY = PAD + (H - PAD * 2)
    areaD = pathD + ` L${points[points.length - 1].x.toFixed(1)},${baseY} L${points[0].x.toFixed(1)},${baseY} Z`
  }

  const fmtNet = (v: number | null | undefined) => {
    if (v === null || v === undefined || v !== v) return '—'
    return fmtNum(v / 1e4, 2) // 万元 -> 亿
  }

  return (
    <>
      <div className="backtest-controls">
        <input className="alert-input" placeholder="股票代码（如 600519）" value={symbol} onChange={e => setSymbol(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') query() }} style={{ flex: '0 0 auto', minWidth: 200 }} />
        <button className="btn-primary" onClick={() => query()} disabled={loading}>{loading ? '查询中...' : '查询'}</button>
        <button className={`mode-btn ${autoRefresh ? 'active' : ''}`} onClick={() => setAutoRefresh(v => !v)} style={{ flex: '0 0 auto' }}>
          {autoRefresh ? '● 排行自动刷新' : '○ 排行自动刷新'}
        </button>
      </div>

      <StatusBar loading={loading} error={error} />

      {!loading && !error && overview && (
        <>
          {/* 总览 */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: 16 }}>
            <div className="kpi-card">
              <span className="kpi-label">最新日期</span>
              <span className="kpi-value" style={{ fontSize: 14 }}>{overview.latest_date || '—'}</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">当日净流入(亿)</span>
              <span className={`kpi-value ${(overview.latest_net ?? 0) >= 0 ? 'up' : 'down'}`}>{fmtNet(overview.latest_net)}</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">累计净流入(亿)</span>
              <span className={`kpi-value ${(overview.cumulative ?? 0) >= 0 ? 'up' : 'down'}`}>{fmtNet(overview.cumulative)}</span>
            </div>
          </div>

          {/* SVG 折线图 */}
          <div className="backtest-equity">
            <h4>最近30天净流入趋势（亿）</h4>
            {points.length > 1 ? (
              <svg className="equity-svg" width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet">
                {/* 0 轴（如果跨越正负） */}
                {(() => {
                  const vals = recent.map(r => r.net_buy ?? 0)
                  const maxV = Math.max(...vals), minV = Math.min(...vals)
                  if (minV < 0 && maxV > 0) {
                    const range = maxV - minV
                    const y0 = PAD + (H - PAD * 2) * (1 - (0 - minV) / range)
                    return <line x1={PAD} y1={y0} x2={W - PAD} y2={y0} stroke="var(--border-strong)" strokeDasharray="3,3" />
                  }
                  return null
                })()}
                <path d={areaD} fill="var(--accent)" opacity={0.08} />
                <path d={pathD} fill="none" stroke="var(--accent)" strokeWidth={1.5} />
                {/* x 轴日期标签 */}
                {points.filter((_, i) => i % Math.ceil(points.length / xLabelN) === 0 || i === points.length - 1).map((p, i) => (
                  <text key={i} x={p.x} y={H - 8} textAnchor="middle" className="axis-label">{p.date.slice(5)}</text>
                ))}
              </svg>
            ) : <div style={{ padding: 20, color: 'var(--text-3)', textAlign: 'center' }}>数据不足</div>}
          </div>

          {/* 个股持股历史 */}
          <div className="backtest-trades">
            <h4 style={{ margin: '16px 0 8px' }}>{symbol} 北向持股历史</h4>
            <table className="portfolio-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>持股数量(万股)</th>
                  <th>持股市值(亿)</th>
                  <th>持股比例</th>
                  <th>当日增减持(万股)</th>
                </tr>
              </thead>
              <tbody>
                {stockHist.length === 0 && <tr><td className="empty-row" colSpan={5}>暂无数据</td></tr>}
                {stockHist.map((d, i) => (
                  <tr key={i}>
                    <td>{d.date}</td>
                    <td style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{fmtWanGu(d.hold_shares)}</td>
                    <td style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{fmtWanToYi(d.hold_value)}</td>
                    <td style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{d.hold_pct !== null && d.hold_pct !== undefined ? fmtNum(d.hold_pct, 3) + '%' : '—'}</td>
                    <td className={pctClass(d.change_shares)} style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{fmtWanGu(d.change_shares)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* 北向持股排行 */}
          <div className="backtest-trades">
            <h4 style={{ margin: '16px 0 8px' }}>
              {market} 北向持股排行（5日资金净流入TOP20）
              <button className="mode-btn" style={{ marginLeft: 12, padding: '4px 12px', fontSize: 11 }}
                onClick={() => { const m = market === '沪股通' ? '深股通' : '沪股通'; setMarket(m); setTimeout(() => query(), 0) }}>
                切换到{market === '沪股通' ? '深股通' : '沪股通'}
              </button>
            </h4>
            <table className="portfolio-table">
              <thead>
                <tr><th>代码</th><th>名称</th><th>最新价</th><th>涨跌幅</th><th>主力净流入(亿)</th><th>持股比例</th></tr>
              </thead>
              <tbody>
                {topStocks.length === 0 && <tr><td className="empty-row" colSpan={6}>暂无数据</td></tr>}
                {topStocks.map((s: any, i: number) => (
                  <tr key={i}>
                    <td className="pf-code">{s.code}</td>
                    <td>{s.name}</td>
                    <td style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{s.price ?? '—'}</td>
                    <td className={pctClass(s.change_pct)} style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>
                      {s.change_pct != null ? fmtNum(s.change_pct, 2) + '%' : '—'}
                    </td>
                    <td style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{s.net_inflow != null ? fmtNum(s.net_inflow / 1e8, 2) : '—'}</td>
                    <td style={{ fontFamily: 'var(--mono)', fontVariantNumeric: 'tabular-nums' }}>{s.hold_pct != null ? fmtNum(s.hold_pct, 2) + '%' : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </>
  )
}
