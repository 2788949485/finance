// 自绘 SVG 图表：日K蜡烛图 + 分时折线图（无第三方库）
// 日K支持：滚轮缩放（放大=减少K线数量，蜡烛更宽更清晰）、拖动平移、双击重置
import { useEffect, useRef, useState } from 'react'
import type { KlineBar, MinutePoint } from './types'

const UP = '#22c55e'
const DOWN = '#ef4444'
const MAX_ZOOM = 200
const MIN_WIN = 10

interface Props {
  bars: KlineBar[]
  minute?: MinutePoint[]
  lastClose?: number | null
  symbol: string
  mode: 'day' | 'minute'
  onMode?: (m: 'day' | 'minute') => void
  dataDate?: string
  isToday?: boolean
  subIndicator?: 'macd' | 'kdj'
  onSubIndicator?: (v: 'macd' | 'kdj') => void
  fullscreen?: boolean
  onFullscreen?: (v: boolean) => void
}

export default function KLineChart({ bars, minute, lastClose, symbol, mode, onMode: _onMode, dataDate, isToday, subIndicator: extSub, onSubIndicator: _onSubInd, fullscreen: extFs, onFullscreen }: Props) {
  const [subIndicator, setSubIndicator] = useState<'macd' | 'kdj'>(extSub ?? 'macd')
  const [fullscreen, setFullscreen] = useState(extFs ?? false)

  // 同步外部 props
  useEffect(() => { if (extSub) setSubIndicator(extSub) }, [extSub])
  useEffect(() => { if (extFs !== undefined) setFullscreen(extFs) }, [extFs])
  const [range] = useState<number | 'all'>('all')
  const [hover, setHover] = useState<KlineBar | null>(null)
  // 缩放：zoom 放大倍数（1=显示当前 range 全部），pan 0~1 窗口位置（0=最新端，1=最旧端）
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState(0)
  const [drag, setDrag] = useState<{ x: number; x0: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const [crosshair, setCrosshair] = useState<{ x: number; y: number } | null>(null)

  // ESC 退出全屏
  useEffect(() => {
    if (!fullscreen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setFullscreen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fullscreen])

  const W = 680, H = 320, PAD = { t: 14, r: 10, b: 22, l: 56 }

  const resetZoom = () => { setZoom(1); setPan(0) }

  // 缩放：用 native event listener（passive: false 才能 preventDefault 阻止页面滚动）
  // 必须在所有 return 之前调用（React Hooks 规则）
  useEffect(() => {
    const svg = svgRef.current
    if (!svg) return
    // 分时模式不做缩放，但仍需阻止滚轮事件冒泡导致页面滚动
    if (mode === 'minute') {
      const block = (e: WheelEvent) => e.preventDefault()
      svg.addEventListener('wheel', block, { passive: false })
      return () => svg.removeEventListener('wheel', block)
    }
    const rawLen = (range === 'all' ? bars : bars.slice(-range)).length
    const handler = (e: WheelEvent) => {
      e.preventDefault()
      const rect = svg.getBoundingClientRect()
      if (!rect.width) return
      const ratio = (e.clientX - rect.left) / rect.width
      const newZoom = Math.min(MAX_ZOOM, Math.max(1, zoom * (e.deltaY < 0 ? 1.6 : 0.625)))
      const newWin = Math.max(MIN_WIN, Math.round(rawLen / newZoom))
      const curWin = Math.max(MIN_WIN, Math.round(rawLen / zoom))
      const maxStart = rawLen - curWin
      const curStart = Math.round(maxStart * (1 - pan))
      const anchorIdx = curStart + ratio * curWin
      const newStart = Math.min(Math.max(0, Math.round(anchorIdx - ratio * newWin)), Math.max(0, rawLen - newWin))
      setZoom(newZoom)
      setPan(1 - newStart / Math.max(1, Math.max(0, rawLen - newWin)))
    }
    svg.addEventListener('wheel', handler, { passive: false })
    return () => svg.removeEventListener('wheel', handler)
  }, [mode, zoom, pan, range, bars])

  // ---------- 分时模式 ----------
  if (mode === 'minute') {
    if (!minute || minute.length < 1) {
            return (
        <div className="kline-wrap">
          <div className="kline-toolbar">
            <span className="kline-symbol">{symbol} 分时</span>
          </div>
          <div className="kline-empty">分时数据暂无（非交易时段，A股/港股 9:30-15:00，美股 21:30-04:00 北京时间）</div>
        </div>
      )
    }
    const prices = minute.map((p) => p.price)
    const avgs = minute.map((p) => p.avg).filter((v): v is number => v != null)
    const base = lastClose ?? prices[0]
    const all = [...prices, ...avgs, base]
    const maxV = Math.max(...all)
    const minV = Math.min(...all)
    // 以昨收为中心对称展开（专业分时图标准）
    const dev = Math.max(maxV - base, base - minV, base * 0.001)
    const top = base + dev * 1.05
    const bot = base - dev * 1.05
    const spanM = top - bot || 1
    const lastP = minute[minute.length - 1]
    const trend = lastP.price >= base ? UP : DOWN
    const pct = ((lastP.price - base) / base * 100)

    // 图表分区：上半部分价格区(70%) + 下半部分成交量区(30%)
    const MH = 320  // 分时图固定高度（与日K一致）
    // 分时模式用更宽的左/右 padding 给 Y 轴价格+涨跌幅留空间
    const mPAD = { ...PAD, l: 72, r: 56 }
    const priceH = Math.round((MH - mPAD.t - mPAD.b) * 0.7)
    const volTop = mPAD.t + priceH + 8
    const volH = MH - volTop - mPAD.b
    const vw = W - mPAD.l - mPAD.r
    const yP = (v: number) => mPAD.t + ((top - v) / spanM) * priceH

    // 时间坐标：按实际数据的时间范围线性映射到 0~vw（适配 A股/港股/美股不同交易时段）
    // 将交易时段分为上午段和下午段（中间午休跳过），用分段线性映射
    const allTimes = minute.map(m => m.time).filter(Boolean).sort()
    // 自动检测午休分界点：找到 11xx -> 13xx 的跳变
    let breakIdx = -1
    for (let k = 1; k < allTimes.length; k++) {
      const prev = parseInt(allTimes[k - 1].slice(0, 2))
      const cur = parseInt(allTimes[k].slice(0, 2))
      if (prev <= 12 && cur >= 13) { breakIdx = k; break }
    }
    const t2m = (t: string) => parseInt(t.slice(0, 2)) * 60 + parseInt(t.slice(2, 4))
    // 上午段时间轴
    const amTimes = breakIdx > 0 ? allTimes.slice(0, breakIdx) : allTimes
    const pmTimes = breakIdx > 0 ? allTimes.slice(breakIdx) : []
    const amStart = t2m(amTimes[0])
    const amEnd = t2m(amTimes[amTimes.length - 1])
    const pmStart = pmTimes.length > 0 ? t2m(pmTimes[0]) : 0
    const pmEnd = pmTimes.length > 0 ? t2m(pmTimes[pmTimes.length - 1]) : 0
    const amLen = Math.max(amEnd - amStart, 1)
    const pmLen = Math.max(pmEnd - pmStart, 0)
    const totalLen = amLen + pmLen
    const amRatio = amLen / totalLen  // 上午段占图表宽度的比例
    const timeToX = (t: string) => {
      const m = t2m(t)
      if (pmTimes.length > 0 && m >= pmStart) {
        // 下午段：映射到 amRatio~1
        const frac = (m - pmStart) / pmLen
        return mPAD.l + (amRatio + frac * (1 - amRatio)) * vw
      } else {
        // 上午段：映射到 0~amRatio
        const frac = (m - amStart) / amLen
        return mPAD.l + frac * amRatio * vw
      }
    }

    // 价格点坐标：用实际时间定位（而非按序号平铺），这样未交易时段留白
    const pts = (arr: (number | null)[]) => arr.map((v, i) => {
      if (v == null) return ''
      return `${timeToX(minute[i].time)},${yP(v)}`
    }).join(' ')

    const lastX = timeToX(lastP.time)

    // 涨跌填充区域路径
    const fillPath = `${timeToX(minute[0].time)},${yP(base)} ${pts(prices)} ${lastX},${yP(base)}`
    const fillColor = trend === UP ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)'

    // 时间轴标签：从实际数据均匀取样5个时间点
    const timeLabels = []
    const labelCount = Math.min(5, allTimes.length)
    for (let k = 0; k < labelCount; k++) {
      const denom = labelCount > 1 ? (labelCount - 1) : 1
      const idx = Math.min(allTimes.length - 1, Math.round(k * (allTimes.length - 1) / denom))
      const t = allTimes[idx]
      const label = `${parseInt(t.slice(0, 2))}:${t.slice(2, 4)}`
      timeLabels.push({ t, label })
    }

    // 最大成交量
    const maxVol = Math.max(...minute.map(m => m.volume ?? 0), 1)

    return (
      <div className={`kline-wrap ${fullscreen ? 'kline-fullscreen' : ''}`}>
        <div className="kline-head">
          <span className="kline-symbol">{symbol} 分时{dataDate && !isToday ? ` (${dataDate})` : ''}</span>
          <span className="kline-price" style={{ color: trend }}>
            {lastP.price.toFixed(2)} <small>{trend === UP ? '▲' : '▼'} {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</small>
          </span>
          </div>
        <svg
          viewBox={`0 0 ${W} ${MH}`}
          width="100%"
          height="100%"
          preserveAspectRatio={fullscreen ? 'none' : 'xMidYMid meet'}
          className="kline-svg"
          style={{ cursor: 'crosshair' }}
          onMouseMove={(e) => {
            const svg = e.currentTarget
            const rect = svg.getBoundingClientRect()
            const sx = (e.clientX - rect.left) / rect.width * W
            const sy = (e.clientY - rect.top) / rect.height * MH
            if (sx >= mPAD.l && sx <= W - mPAD.r && sy >= mPAD.t && sy <= mPAD.t + priceH) {
              setCrosshair({ x: sx, y: sy })
            } else {
              setCrosshair(null)
            }
          }}
          onMouseLeave={() => setCrosshair(null)}
        >
          {/* 价格区网格 */}
          {[0, 0.25, 0.5, 0.75, 1].map((r) => (
            <line key={r} x1={mPAD.l} x2={W - mPAD.r} y1={mPAD.t + priceH * r} y2={mPAD.t + priceH * r} stroke="#1e293b" strokeWidth="1" />
          ))}
          {/* 昨收基准线 */}
          <line x1={mPAD.l} x2={W - mPAD.r} y1={yP(base)} y2={yP(base)} stroke="#64748b" strokeWidth="1" strokeDasharray="4 4" />
          {/* 涨跌填充区域 */}
          <polygon points={fillPath} fill={fillColor} />
          {/* 价格线 */}
          <polyline points={pts(prices)} fill="none" stroke={trend} strokeWidth="1.6" />
          {/* 均价线 */}
          {avgs.length > 1 && (
            <polyline points={pts(minute.map(m => m.avg))} fill="none" stroke="#f59e0b" strokeWidth="1.1" opacity="0.85" />
          )}
          {/* Y轴价格刻度（对称：上+涨幅 下-跌幅） */}
          {[0, 0.25, 0.5, 0.75, 1].map((r) => {
            const v = top - spanM * r
            const changePct = ((v - base) / base * 100)
            return (
              <text key={r} x={mPAD.l - 6} y={mPAD.t + priceH * r + 4} textAnchor="end" fontSize="10" fill="#64748b">
                {v.toFixed(2)}
                <tspan fill={changePct >= 0 ? UP : DOWN} dx="2">{changePct >= 0 ? '+' : ''}{changePct.toFixed(1)}%</tspan>
              </text>
            )
          })}
          {/* 最新价格标签（跟随当前数据末端，不拉满全宽） */}
          <line x1={mPAD.l} x2={lastX} y1={yP(lastP.price)} y2={yP(lastP.price)} stroke={trend} strokeWidth="0.6" strokeDasharray="3 3" opacity="0.6" />
          <rect x={Math.min(lastX + 2, W - mPAD.r - 52)} y={yP(lastP.price) - 8} width="50" height="16" rx="2" fill={trend} />
          <text x={Math.min(lastX + 27, W - mPAD.r - 27)} y={yP(lastP.price) + 3} textAnchor="middle" fontSize="10" fill="#fff" fontWeight="bold">{lastP.price.toFixed(2)}</text>

          {/* 分隔线 */}
          <line x1={mPAD.l} x2={W - mPAD.r} y1={volTop - 4} y2={volTop - 4} stroke="#1e293b" strokeWidth="1" />
          {/* 成交量柱状图（按实际时间定位） */}
          {minute.map((m, i) => {
            const h = ((m.volume ?? 0) / maxVol) * volH * 0.9
            const x = timeToX(m.time)
            const barW = Math.max(vw / minute.length * 0.7, 1)
            const barUp = m.price >= (i > 0 ? minute[i-1].price : base)
            return h > 0.5 ? (
              <rect key={i} x={x - barW / 2} y={volTop + volH - h} width={barW} height={h} fill={barUp ? UP : DOWN} opacity="0.5" />
            ) : null
          })}
          {/* 成交量标签 */}
          <text x={mPAD.l - 6} y={volTop + 10} textAnchor="end" fontSize="9" fill="#64748b">成交量</text>

          {/* 时间轴标签 */}
          {timeLabels.map(({ t, label }) => (
            <text key={t} x={timeToX(t)} y={MH - 6} textAnchor="middle" fontSize="9.5" fill="#64748b">{label}</text>
          ))}
          {/* 十字光标 */}
          {crosshair && (() => {
            const cy = crosshair.y
            const val = top - ((cy - mPAD.t) / priceH) * spanM
            const valColor = val >= base ? UP : DOWN
            return (
              <g pointerEvents="none">
                <line x1={mPAD.l} y1={cy} x2={W - mPAD.r} y2={cy} stroke="#94a3b8" strokeWidth="0.8" strokeDasharray="4 3" />
                <line x1={crosshair.x} y1={mPAD.t} x2={crosshair.x} y2={mPAD.t + priceH} stroke="#94a3b8" strokeWidth="0.8" strokeDasharray="4 3" />
                <rect x={mPAD.l - 54} y={cy - 8} width="50" height="16" rx="2" fill="#334155" />
                <text x={mPAD.l - 29} y={cy + 3} textAnchor="middle" fontSize="10" fill={valColor} fontWeight="bold">{val.toFixed(2)}</text>
              </g>
            )
          })()}
        </svg>
      </div>
    )
  }

  // ---------- 日K模式 ----------
  const rawData = range === 'all' ? bars : bars.slice(-range)
  if (rawData.length < 2) {
    return <div className="kline-empty">K线数据不足</div>
  }

  // 数据窗口缩放：放大 → 窗口内K线数量变少，蜡烛更宽更清晰（不是像素放大）
  const rawLen = rawData.length
  const winCount = Math.max(MIN_WIN, Math.round(rawLen / zoom))
  const maxStart = rawLen - winCount
  const start = Math.round(maxStart * (1 - pan))
  const winData = rawData.slice(start, start + winCount)
  // 窗口内仍过多则均匀降采样（保证渲染性能）
  const data = winData.length > 600
    ? winData.filter((_, i) => i % Math.ceil(winData.length / 600) === 0)
    : winData
  if (data.length < 2) {
    return <div className="kline-empty">K线数据不足</div>
  }

  // 拖动平移（放大后可用）
  const onMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if (zoom <= 1) return
    setDrag({ x: e.clientX, x0: start })
    e.preventDefault()
  }
  const onMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    // 十字光标（拖动模式以外）
    const svg = svgRef.current
    if (svg) {
      const rect = svg.getBoundingClientRect()
      const sx = (e.clientX - rect.left) / rect.width * W
      const sy = (e.clientY - rect.top) / rect.height * H
      // 鼠标在整个图表区域（主图+副图）都响应十字光标
      if (!drag && sx >= PAD.l && sx <= W - PAD.r && sy >= PAD.t && sy <= H - PAD.b) {
        setCrosshair({ x: sx, y: sy })
        // 根据X坐标找到对应的K线，更新hover（副图移动时主图也跟着高亮）
        const idx = Math.min(data.length - 1, Math.max(0, Math.round((sx - PAD.l) / step - 0.5)))
        if (data[idx]) {
          setHover(data[idx])
        }
      }
    }
    // 拖动平移
    if (!drag) return
    if (!svg) return
    const rect = svg.getBoundingClientRect()
    if (!rect.width) return
    const roots = ((e.clientX - drag.x) / rect.width) * winCount
    const ns = Math.min(Math.max(0, Math.round(drag.x0 - roots)), rawLen - winCount)
    setPan(1 - ns / Math.max(1, rawLen - winCount))
  }
  const endDrag = () => setDrag(null)

  const vw = W - PAD.l - PAD.r
  const priceH = Math.round((H - PAD.t - PAD.b) * 0.72)  // 价格区72%
  const macdTop = PAD.t + priceH + 6
  const macdH = H - PAD.t - PAD.b - priceH - 6  // MACD区
  const vh = H - PAD.t - PAD.b
  const highs = data.map((d) => d.high)
  const lows = data.map((d) => d.low)
  const maxV = Math.max(...highs)
  const minV = Math.min(...lows)
  const span = maxV - minV || 1
  const y = (v: number) => PAD.t + ((maxV - v) / span) * priceH
  const step = vw / data.length
  const bw = Math.max(2, step * 0.6)

  const ma = (n: number) => data.map((_, i) => {
    if (i < n - 1) return null
    const s = data.slice(i - n + 1, i + 1)
    return s.reduce((a, b) => a + b.close, 0) / n
  })
  const ma5 = ma(5)
  const ma20 = ma(20)

  // MACD 计算（12,26,9）
  const ema = (period: number) => {
    const k = 2 / (period + 1)
    const result: (number | null)[] = []
    let prev: number | null = null
    for (let i = 0; i < data.length; i++) {
      if (i < period - 1) { result.push(null); continue }
      if (prev === null) {
        const s = data.slice(0, period).reduce((a, b) => a + b.close, 0) / period
        prev = s; result.push(s)
      } else {
        prev = data[i].close * k + prev * (1 - k)
        result.push(prev)
      }
    }
    return result
  }
  const ema12 = ema(12)
  const ema26 = ema(26)
  const dif = data.map((_, i) => (ema12[i] != null && ema26[i] != null) ? (ema12[i]! - ema26[i]!) : null)
  const dea = (() => {
    const k = 2 / (9 + 1)
    const result: (number | null)[] = []
    let prev: number | null = null
    for (let i = 0; i < dif.length; i++) {
      if (dif[i] == null) { result.push(null); continue }
      const d = dif[i]!
      if (prev === null) { prev = d; result.push(d) }
      else { prev = d * k + prev * (1 - k); result.push(prev) }
    }
    return result
  })()
  const macdBars = dif.map((d, i) => (d != null && dea[i] != null) ? (d - dea[i]!) * 2 : null)
  // 缩放范围取 DIF/DEA/MACD 三者的最大绝对值（否则柱子和线可能超出副图区域）
  const allMacdVals = [...dif, ...dea, ...macdBars].filter((v): v is number => v != null).map(Math.abs)
  const macdMax = Math.max(...allMacdVals, 0.01)
  const macdY = (v: number) => macdTop + macdH / 2 - (v / macdMax) * (macdH / 2 - 2)

  // KDJ 计算（9,3,3）
  const kdj = (() => {
    const kArr: (number | null)[] = []
    const dArr: (number | null)[] = []
    const jArr: (number | null)[] = []
    let prevK = 50, prevD = 50
    for (let i = 0; i < data.length; i++) {
      if (i < 8) { kArr.push(null); dArr.push(null); jArr.push(null); continue }
      const window = data.slice(i - 8, i + 1)
      const hn = Math.max(...window.map(d => d.high))
      const ln = Math.min(...window.map(d => d.low))
      const rsv = hn === ln ? 50 : ((data[i].close - ln) / (hn - ln)) * 100
      const k = (2 / 3) * prevK + (1 / 3) * rsv
      const d = (2 / 3) * prevD + (1 / 3) * k
      const j = 3 * k - 2 * d
      prevK = k; prevD = d
      kArr.push(k); dArr.push(d); jArr.push(j)
    }
    return { k: kArr, d: dArr, j: jArr }
  })()
  const kdjVals = [...kdj.k, ...kdj.d, ...kdj.j].filter((v): v is number => v != null)
  const kdjMin = Math.min(...kdjVals, 0)
  const kdjMax = Math.max(...kdjVals, 100)
  const kdjY = (v: number) => macdTop + macdH - 4 - ((v - kdjMin) / (kdjMax - kdjMin || 1)) * (macdH - 8)

  // BOLL 计算（20,2）
  const boll = (() => {
    const mid: (number | null)[] = []
    const upper: (number | null)[] = []
    const lower: (number | null)[] = []
    for (let i = 0; i < data.length; i++) {
      if (i < 19) { mid.push(null); upper.push(null); lower.push(null); continue }
      const window = data.slice(i - 19, i + 1)
      const m = window.reduce((a, b) => a + b.close, 0) / 20
      const variance = window.reduce((a, b) => a + (b.close - m) ** 2, 0) / 20
      const sd = Math.sqrt(variance)
      mid.push(m); upper.push(m + 2 * sd); lower.push(m - 2 * sd)
    }
    return { mid, upper, lower }
  })()

  const last = data[data.length - 1]
  const trend = last.close >= last.open ? UP : DOWN

  return (
    <div className={`kline-wrap ${fullscreen ? 'kline-fullscreen' : ''}`}>
      <div className="kline-head">
        <span className="kline-symbol">{symbol}</span>
        <span className="kline-price" style={{ color: trend }}>
          {last.close} <small>{trend === UP ? '▲' : '▼'}</small>
        </span>
        {fullscreen && (
          <span className="kline-range">
            <button className="ghost" onClick={() => onFullscreen?.(false)}>退出全屏</button>
          </span>
        )}
        {!fullscreen && zoom > 1 && (
          <span className="kline-range">
            <span className="zoom-hint">滚轮缩放 · 拖动平移 · 双击重置</span>
            <button className="ghost zoom-ind" onClick={resetZoom} title="重置缩放（或双击图表）">{zoom.toFixed(0)}x ⇲</button>
          </span>
        )}
        {!fullscreen && zoom <= 1 && rawLen > 200 && (
          <span className="kline-range">
            <span className="zoom-hint">滚轮缩放查看细节</span>
          </span>
        )}
      </div>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height="100%"
        preserveAspectRatio={fullscreen ? 'none' : 'xMidYMid meet'}
        className="kline-svg"
        style={{ cursor: zoom > 1 ? (drag ? 'grabbing' : 'grab') : 'crosshair', touchAction: 'none' }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={() => { endDrag(); setHover(null); setCrosshair(null) }}
        onDoubleClick={resetZoom}
      >
        {[0.25, 0.5, 0.75].map((r) => (
          <line key={r} x1={PAD.l} x2={W - PAD.r} y1={PAD.t + priceH * r} y2={PAD.t + priceH * r} stroke="#1e293b" strokeWidth="1" />
        ))}
        <polyline
          points={ma5.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${y(v)}`).join(' ')}
          fill="none" stroke="#f59e0b" strokeWidth="1.2" opacity="0.9"
        />
        <polyline
          points={ma20.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${y(v)}`).join(' ')}
          fill="none" stroke="#0d9488" strokeWidth="1.2" opacity="0.9"
        />
        {/* BOLL 上轨 */}
        <polyline points={boll.upper.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${y(v)}`).join(' ')} fill="none" stroke="#64748b" strokeWidth="0.8" opacity="0.5" strokeDasharray="3 2" />
        {/* BOLL 中轨 */}
        <polyline points={boll.mid.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${y(v)}`).join(' ')} fill="none" stroke="#64748b" strokeWidth="0.8" opacity="0.4" />
        {/* BOLL 下轨 */}
        <polyline points={boll.lower.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${y(v)}`).join(' ')} fill="none" stroke="#64748b" strokeWidth="0.8" opacity="0.5" strokeDasharray="3 2" />
        {data.map((d, i) => {
          const x = PAD.l + i * step + step / 2
          const up = d.close >= d.open
          const c = up ? UP : DOWN
          const top = Math.min(y(d.open), y(d.close))
          const h = Math.max(Math.abs(y(d.open) - y(d.close)), 1)
          return (
            <g key={d.date} onMouseEnter={() => setHover(d)}>
              <line x1={x} x2={x} y1={y(d.high)} y2={y(d.low)} stroke={c} strokeWidth="1" />
              <rect x={x - bw / 2} y={top} width={bw} height={h} fill={c} opacity={0.95} stroke={c} strokeWidth="0.5" />
            </g>
          )
        })}
        {[0, 0.5, 1].map((r) => {
          const v = maxV - span * r
          return (
            <text key={r} x={PAD.l - 6} y={PAD.t + priceH * r + 4} textAnchor="end" fontSize="10" fill="#64748b">
              {v.toFixed(2)}
            </text>
          )
        })}
        {/* X轴日期标签：均匀取5个点 */}
        {(() => {
          const n = Math.min(5, data.length)
          const interval = Math.floor(data.length / n)
          const labels = []
          for (let i = 0; i < n; i++) {
            const idx = Math.min(data.length - 1, i * interval)
            const d = data[idx]
            const x = PAD.l + idx * step + step / 2
            // 日期格式化：根据数据类型智能显示
            const dateStr = d.date || ''
            const hasTime = dateStr.includes(' ') || dateStr.includes(':')
            let label: string
            if (hasTime) {
              // 分钟级K线：显示 MM-DD HH:MM
              const parts = dateStr.split(' ')
              const datePart = parts[0] || ''
              const timePart = parts[1] || ''
              const md = datePart.length >= 10 ? datePart.slice(5) : datePart
              const hm = timePart.length >= 5 ? timePart.slice(0, 5) : timePart
              label = `${md} ${hm}`
            } else {
              // 日K/周K/月K：本年显示 MM-DD，往年显示 YY-MM-DD
              const year = dateStr.slice(0, 4)
              const thisYear = String(new Date().getFullYear())
              label = dateStr.length >= 10
                ? (year === thisYear ? dateStr.slice(5) : dateStr.slice(2))
                : dateStr
            }
            labels.push({ x, label })
          }
          return labels.map((l, i) => (
            <text key={i} x={l.x} y={H - 6} textAnchor="middle" fontSize="9.5" fill="#64748b">{l.label}</text>
          ))
        })()}
        {hover && (
          <g>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(hover.close)} y2={y(hover.close)} stroke="#64748b" strokeWidth="0.6" strokeDasharray="3 3" />
            <text x={W - PAD.r} y={y(hover.close) - 4} textAnchor="end" fontSize="10" fill="#94a3b8">
              {hover.close.toFixed(2)}
            </text>
          </g>
        )}
        {crosshair && !drag && (() => {
          const cy = crosshair.y
          const cx = crosshair.x
          // 找最近的K线索引
          const idx = Math.min(data.length - 1, Math.max(0, Math.round((cx - PAD.l) / step - 0.5)))
          const barX = PAD.l + idx * step + step / 2
          // 判断鼠标是否在主图区域
          const inMainChart = cy >= PAD.t && cy <= PAD.t + priceH
          const val = inMainChart ? maxV - ((cy - PAD.t) / vh) * span : null
          return (
            <g pointerEvents="none">
              {/* 竖虚线贯穿主图+副图 */}
              <line x1={barX} y1={PAD.t} x2={barX} y2={H - PAD.b} stroke="#94a3b8" strokeWidth="0.8" strokeDasharray="4 3" />
              {/* 水平虚线+价格标签只在主图区域 */}
              {inMainChart && val !== null && (
                <>
                  <line x1={PAD.l} y1={cy} x2={W - PAD.r} y2={cy} stroke="#94a3b8" strokeWidth="0.8" strokeDasharray="4 3" />
                  <rect x={PAD.l - 54} y={cy - 8} width="50" height="16" rx="2" fill="#334155" />
                  <text x={PAD.l - 29} y={cy + 3} textAnchor="middle" fontSize="10" fill="#e2e8f0" fontWeight="bold">{val.toFixed(2)}</text>
                </>
              )}
            </g>
          )
        })()}
        {/* 副图分隔线 */}
        <line x1={PAD.l} x2={W - PAD.r} y1={macdTop - 1} y2={macdTop - 1} stroke="#1e293b" strokeWidth="1" />
        {subIndicator === 'macd' ? (
          <>
        <text x={PAD.l + 4} y={macdTop + 11} fontSize="9" fill="#64748b">MACD(12,26,9)</text>
        {(() => {
          const idx = hover ? Math.min(data.length - 1, Math.max(0, Math.round((crosshair?.x ?? 0 - PAD.l) / step - 0.5))) : data.length - 1
          const d = dif[idx] ?? 0
          const e = dea[idx] ?? 0
          const m = macdBars[idx] ?? 0
          return (
            <text x={PAD.l + 90} y={macdTop + 11} fontSize="9" fill="#64748b">
              <tspan fill="#f59e0b">DIF {d.toFixed(3)}</tspan>
              <tspan dx="6" fill="#0d9488">DEA {e.toFixed(3)}</tspan>
              <tspan dx="6" fill={m >= 0 ? UP : DOWN}>MACD {m.toFixed(3)}</tspan>
            </text>
          )
        })()}
        {/* MACD 零轴 */}
        <line x1={PAD.l} x2={W - PAD.r} y1={macdTop + macdH / 2} y2={macdTop + macdH / 2} stroke="#334155" strokeWidth="0.5" />
        {/* MACD Y轴刻度（左侧） */}
        {(() => {
          const ticks = [-macdMax, -macdMax / 2, 0, macdMax / 2, macdMax]
          const labels = [macdMax.toFixed(2), (macdMax / 2).toFixed(2), '0.00', (macdMax / 2).toFixed(2), macdMax.toFixed(2)]
          return ticks.map((v, i) => (
            <g key={'ytick'+i}>
              <line x1={PAD.l} x2={W - PAD.r} y1={macdY(v)} y2={macdY(v)} stroke="#1e293b" strokeWidth="0.5" strokeDasharray="1 3" />
              <text x={PAD.l - 6} y={macdY(v) + 3} textAnchor="end" fontSize="9" fill="#475569">{labels[i]}</text>
            </g>
          ))
        })()}
        {/* MACD 鼠标跟随虚线 */}
        {crosshair && (() => {
          const cx = Math.min(W - PAD.r, Math.max(PAD.l, crosshair.x))
          const idx = Math.min(data.length - 1, Math.max(0, Math.round((cx - PAD.l) / step - 0.5)))
          const dVal = dif[idx] ?? 0
          return (
            <g pointerEvents="none">
              <line x1={cx} x2={cx} y1={macdTop} y2={macdTop + macdH} stroke="#0d9488" strokeWidth="0.5" strokeDasharray="3 3" opacity="0.5" />
              <circle cx={cx} cy={macdY(dVal)} r="2" fill="#f59e0b" />
            </g>
          )
        })()}
        {/* MACD 柱状图 */}
        {macdBars.map((v, i) => {
          if (v == null) return null
          const x = PAD.l + i * step + step / 2
          const zeroY = macdTop + macdH / 2
          const h = Math.abs(macdY(v) - zeroY)
          const isUp = v >= 0
          return <rect key={'m'+i} x={x - bw * 0.35} y={Math.min(macdY(v), zeroY)} width={bw * 0.7} height={Math.max(h, 0.5)} fill={isUp ? UP : DOWN} opacity="0.6" />
        })}
        {/* DIF 线 */}
        <polyline points={dif.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${macdY(v)}`).join(' ')} fill="none" stroke="#f59e0b" strokeWidth="1" opacity="0.85" />
        {/* DEA 线 */}
        <polyline points={dea.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${macdY(v)}`).join(' ')} fill="none" stroke="#0d9488" strokeWidth="1" opacity="0.85" />
          </>
        ) : (
          <>
        <text x={PAD.l + 4} y={macdTop + 11} fontSize="9" fill="#64748b">KDJ(9,3,3)</text>
        {(() => {
          const idx = hover ? Math.min(data.length - 1, Math.max(0, Math.round((crosshair?.x ?? 0 - PAD.l) / step - 0.5))) : data.length - 1
          const kv = kdj.k[idx] ?? 0
          const dv = kdj.d[idx] ?? 0
          const jv = kdj.j[idx] ?? 0
          return (
            <text x={PAD.l + 70} y={macdTop + 11} fontSize="9" fill="#64748b">
              <tspan fill="#a855f7">K {kv.toFixed(1)}</tspan>
              <tspan dx="6" fill="#0d9488">D {dv.toFixed(1)}</tspan>
              <tspan dx="6" fill="#f59e0b">J {jv.toFixed(1)}</tspan>
            </text>
          )
        })()}
        {/* KDJ 20/50/80 参考线（左侧标签） */}
        {[20, 50, 80].map((lvl) => (
          <g key={lvl}>
            <line x1={PAD.l} x2={W - PAD.r} y1={kdjY(lvl)} y2={kdjY(lvl)} stroke="#1e293b" strokeWidth="0.5" strokeDasharray="2 4" />
            <text x={PAD.l - 6} y={kdjY(lvl) + 3} textAnchor="end" fontSize="8" fill="#475569">{lvl}</text>
          </g>
        ))}
        {/* KDJ 鼠标跟随虚线 */}
        {crosshair && (() => {
          const cx = Math.min(W - PAD.r, Math.max(PAD.l, crosshair.x))
          const idx = Math.min(data.length - 1, Math.max(0, Math.round((cx - PAD.l) / step - 0.5)))
          const kv = kdj.k[idx] ?? 0
          return (
            <g pointerEvents="none">
              <line x1={cx} x2={cx} y1={macdTop} y2={macdTop + macdH} stroke="#0d9488" strokeWidth="0.5" strokeDasharray="3 3" opacity="0.5" />
              <circle cx={cx} cy={kdjY(kv)} r="2" fill="#a855f7" />
            </g>
          )
        })()}
        {/* K 线 */}
        <polyline points={kdj.k.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${kdjY(v)}`).join(' ')} fill="none" stroke="#a855f7" strokeWidth="1" opacity="0.85" />
        {/* D 线 */}
        <polyline points={kdj.d.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${kdjY(v)}`).join(' ')} fill="none" stroke="#0d9488" strokeWidth="1" opacity="0.85" />
        {/* J 线 */}
        <polyline points={kdj.j.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${kdjY(v)}`).join(' ')} fill="none" stroke="#f59e0b" strokeWidth="1" opacity="0.85" />
          </>
        )}
      </svg>
      {hover && (() => {
        const idx = data.findIndex(d => d.date === hover.date)
        const d = dif[idx] ?? 0
        const e = dea[idx] ?? 0
        const m = macdBars[idx] ?? 0
        return (
          <div className="kline-tooltip">
            {hover.date} 开 {hover.open} 高 {hover.high} 低 {hover.low} 收 {hover.close}
            <span style={{ marginLeft: 12, color: '#f59e0b' }}>DIF {d.toFixed(3)}</span>
            <span style={{ marginLeft: 6, color: '#0d9488' }}>DEA {e.toFixed(3)}</span>
            <span style={{ marginLeft: 6, color: m >= 0 ? '#22c55e' : '#ef4444' }}>MACD {m.toFixed(3)}</span>
          </div>
        )
      })()}
    </div>
  )
}
