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
}

export default function KLineChart({ bars, minute, lastClose, symbol, mode, onMode }: Props) {
  const [range, setRange] = useState<number | 'all'>(60)
  const [hover, setHover] = useState<KlineBar | null>(null)
  // 缩放：zoom 放大倍数（1=显示当前 range 全部），pan 0~1 窗口位置（0=最新端，1=最旧端）
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState(0)
  const [drag, setDrag] = useState<{ x: number; x0: number } | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const [crosshair, setCrosshair] = useState<{ x: number; y: number } | null>(null)
  const [fullscreen, setFullscreen] = useState(false)

  // ESC 退出全屏
  useEffect(() => {
    if (!fullscreen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setFullscreen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [fullscreen])

  const W = 680, H = 240, PAD = { t: 14, r: 10, b: 22, l: 56 }

  const resetZoom = () => { setZoom(1); setPan(0) }

  const switchRange = (n: number | 'all') => { setRange(n); resetZoom() }

  // 缩放：用 native event listener（passive: false 才能 preventDefault 阻止页面滚动）
  // 必须在所有 return 之前调用（React Hooks 规则）
  useEffect(() => {
    if (mode === 'minute') return  // 分时模式不需要滚轮缩放
    const svg = svgRef.current
    if (!svg) return
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
    if (!minute || minute.length < 2) {
      return (
        <div className="kline-wrap">
          <div className="kline-head">
            <span className="kline-symbol">{symbol} 分时</span>
            <span className="kline-range">
              {onMode && <button className="ghost" onClick={() => onMode('day')}>日K</button>}
              <button className="ghost active">分时</button>
            </span>
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
    const MH = H + 80  // 分时图加高
    const priceH = Math.round((MH - PAD.t - PAD.b) * 0.7)
    const volTop = PAD.t + priceH + 8
    const volH = MH - volTop - PAD.b
    const vw = W - PAD.l - PAD.r
    const yP = (v: number) => PAD.t + ((top - v) / spanM) * priceH

    // 时间坐标：将交易时段(0930-1130, 1300-1500)映射到 0~vw，午休跳过
    const totalMins = 4 * 60  // 4小时交易时间
    const timeToX = (t: string) => {
      const hh = parseInt(t.slice(0, 2))
      const mm = parseInt(t.slice(2, 4))
      const morning = (hh < 11 || (hh === 11 && mm <= 30)) ? (hh * 60 + mm - 570) : -1
      const afternoon = (hh >= 13) ? (hh * 60 + mm - 780 + 120) : -1
      const mins = morning >= 0 ? morning : (afternoon >= 0 ? afternoon : 0)
      return PAD.l + (mins / totalMins) * vw
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

    // 时间轴标签
    const timeLabels = [
      { t: '0930', label: '9:30' }, { t: '1030', label: '10:30' },
      { t: '1130', label: '11:30/13:00' }, { t: '1400', label: '14:00' }, { t: '1500', label: '15:00' },
    ]

    // 最大成交量
    const maxVol = Math.max(...minute.map(m => m.volume ?? 0), 1)

    return (
      <div className={`kline-wrap ${fullscreen ? 'kline-fullscreen' : ''}`}>
        <div className="kline-head">
          <span className="kline-symbol">{symbol} 分时</span>
          <span className="kline-price" style={{ color: trend }}>
            {lastP.price.toFixed(2)} <small>{trend === UP ? '▲' : '▼'} {pct >= 0 ? '+' : ''}{pct.toFixed(2)}%</small>
          </span>
          <span className="kline-range">
            {onMode && <button className="ghost" onClick={() => onMode('day')}>日K</button>}
            <button className="ghost active">分时</button>
            <button className="ghost" onClick={() => setFullscreen(!fullscreen)} title="全屏">{fullscreen ? '退出全屏' : '全屏'}</button>
          </span>
        </div>
        <svg
          viewBox={`0 0 ${W} ${MH}`}
          className="kline-svg"
          style={{ cursor: 'crosshair' }}
          onMouseMove={(e) => {
            const svg = e.currentTarget
            const rect = svg.getBoundingClientRect()
            const sx = (e.clientX - rect.left) / rect.width * W
            const sy = (e.clientY - rect.top) / rect.height * MH
            if (sx >= PAD.l && sx <= W - PAD.r && sy >= PAD.t && sy <= PAD.t + priceH) {
              setCrosshair({ x: sx, y: sy })
            } else {
              setCrosshair(null)
            }
          }}
          onMouseLeave={() => setCrosshair(null)}
        >
          {/* 价格区网格 */}
          {[0, 0.25, 0.5, 0.75, 1].map((r) => (
            <line key={r} x1={PAD.l} x2={W - PAD.r} y1={PAD.t + priceH * r} y2={PAD.t + priceH * r} stroke="#1e293b" strokeWidth="1" />
          ))}
          {/* 昨收基准线 */}
          <line x1={PAD.l} x2={W - PAD.r} y1={yP(base)} y2={yP(base)} stroke="#64748b" strokeWidth="1" strokeDasharray="4 4" />
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
              <text key={r} x={PAD.l - 6} y={PAD.t + priceH * r + 4} textAnchor="end" fontSize="10" fill="#64748b">
                {v.toFixed(2)}
                <tspan fill={changePct >= 0 ? UP : DOWN} dx="2">{changePct >= 0 ? '+' : ''}{changePct.toFixed(1)}%</tspan>
              </text>
            )
          })}
          {/* 最新价格标签（跟随当前数据末端，不拉满全宽） */}
          <line x1={PAD.l} x2={lastX} y1={yP(lastP.price)} y2={yP(lastP.price)} stroke={trend} strokeWidth="0.6" strokeDasharray="3 3" opacity="0.6" />
          <rect x={lastX + 2} y={yP(lastP.price) - 8} width="50" height="16" rx="2" fill={trend} />
          <text x={lastX + 27} y={yP(lastP.price) + 3} textAnchor="middle" fontSize="10" fill="#fff" fontWeight="bold">{lastP.price.toFixed(2)}</text>

          {/* 分隔线 */}
          <line x1={PAD.l} x2={W - PAD.r} y1={volTop - 4} y2={volTop - 4} stroke="#1e293b" strokeWidth="1" />
          {/* 成交量柱状图（按实际时间定位） */}
          {minute.map((m, i) => {
            const h = ((m.volume ?? 0) / maxVol) * volH * 0.9
            const x = timeToX(m.time)
            const barW = Math.max(vw / totalMins * 0.7, 1)
            const barUp = m.price >= (i > 0 ? minute[i-1].price : base)
            return h > 0.5 ? (
              <rect key={i} x={x - barW / 2} y={volTop + volH - h} width={barW} height={h} fill={barUp ? UP : DOWN} opacity="0.5" />
            ) : null
          })}
          {/* 成交量标签 */}
          <text x={PAD.l - 6} y={volTop + 10} textAnchor="end" fontSize="9" fill="#64748b">成交量</text>

          {/* 时间轴标签 */}
          {timeLabels.map(({ t, label }) => (
            <text key={t} x={timeToX(t)} y={MH - 6} textAnchor="middle" fontSize="9.5" fill="#64748b">{label}</text>
          ))}
          {/* 十字光标 */}
          {crosshair && (() => {
            const cy = crosshair.y
            const val = top - ((cy - PAD.t) / priceH) * spanM
            const valColor = val >= base ? UP : DOWN
            return (
              <g pointerEvents="none">
                <line x1={PAD.l} y1={cy} x2={W - PAD.r} y2={cy} stroke="#94a3b8" strokeWidth="0.8" strokeDasharray="4 3" />
                <line x1={crosshair.x} y1={PAD.t} x2={crosshair.x} y2={PAD.t + priceH} stroke="#94a3b8" strokeWidth="0.8" strokeDasharray="4 3" />
                <rect x={PAD.l - 54} y={cy - 8} width="50" height="16" rx="2" fill="#334155" />
                <text x={PAD.l - 29} y={cy + 3} textAnchor="middle" fontSize="10" fill={valColor} fontWeight="bold">{val.toFixed(2)}</text>
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
      if (!drag && sx >= PAD.l && sx <= W - PAD.r && sy >= PAD.t && sy <= PAD.t + vh) {
        setCrosshair({ x: sx, y: sy })
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
  const vh = H - PAD.t - PAD.b
  const highs = data.map((d) => d.high)
  const lows = data.map((d) => d.low)
  const maxV = Math.max(...highs)
  const minV = Math.min(...lows)
  const span = maxV - minV || 1
  const y = (v: number) => PAD.t + ((maxV - v) / span) * vh
  const step = vw / data.length
  const bw = Math.max(2, step * 0.6)

  const ma = (n: number) => data.map((_, i) => {
    if (i < n - 1) return null
    const s = data.slice(i - n + 1, i + 1)
    return s.reduce((a, b) => a + b.close, 0) / n
  })
  const ma5 = ma(5)
  const ma20 = ma(20)

  const last = data[data.length - 1]
  const trend = last.close >= last.open ? UP : DOWN

  return (
    <div className={`kline-wrap ${fullscreen ? 'kline-fullscreen' : ''}`}>
      <div className="kline-head">
        <span className="kline-symbol">{symbol}</span>
        <span className="kline-price" style={{ color: trend }}>
          {last.close} <small>{trend === UP ? '▲' : '▼'}</small>
        </span>
        <span className="kline-range">
          {[30, 60, 120].map((n) => (
            <button key={n} className={`ghost ${range === n ? 'active' : ''}`} onClick={() => switchRange(n)}>{n}日</button>
          ))}
          <button className={`ghost ${range === 'all' ? 'active' : ''}`} onClick={() => switchRange('all')}>全部</button>
          {onMode && <button className="ghost" onClick={() => onMode('minute')}>分时</button>}
          {zoom > 1 && (
            <>
              <span className="zoom-hint">滚轮缩放 · 拖动平移 · 双击重置</span>
              <button className="ghost zoom-ind" onClick={resetZoom} title="重置缩放（或双击图表）">{zoom.toFixed(0)}x ⇲</button>
            </>
          )}
          {zoom <= 1 && range === 'all' && rawLen > 200 && (
            <span className="zoom-hint">滚轮缩放查看细节</span>
          )}
          <button className="ghost" onClick={() => setFullscreen(!fullscreen)} title="全屏">{fullscreen ? '退出全屏' : '全屏'}</button>
        </span>
      </div>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="kline-svg"
        style={{ cursor: zoom > 1 ? (drag ? 'grabbing' : 'grab') : 'crosshair', touchAction: 'none' }}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={() => { endDrag(); setHover(null); setCrosshair(null) }}
        onDoubleClick={resetZoom}
      >
        {[0.25, 0.5, 0.75].map((r) => (
          <line key={r} x1={PAD.l} x2={W - PAD.r} y1={PAD.t + vh * r} y2={PAD.t + vh * r} stroke="#1e293b" strokeWidth="1" />
        ))}
        <polyline
          points={ma5.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${y(v)}`).join(' ')}
          fill="none" stroke="#f59e0b" strokeWidth="1.2" opacity="0.9"
        />
        <polyline
          points={ma20.map((v, i) => v == null ? '' : `${PAD.l + i * step + step / 2},${y(v)}`).join(' ')}
          fill="none" stroke="#3b82f6" strokeWidth="1.2" opacity="0.9"
        />
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
            <text key={r} x={PAD.l - 6} y={PAD.t + vh * r + 4} textAnchor="end" fontSize="10" fill="#64748b">
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
            // 日期格式化：本年显示 MM-DD，往年显示 YY-MM-DD
            const dateStr = d.date || ''
            const year = dateStr.slice(0, 4)
            const thisYear = String(new Date().getFullYear())
            const short = dateStr.length >= 10
              ? (year === thisYear ? dateStr.slice(5) : dateStr.slice(2))
              : dateStr
            labels.push({ x, label: short })
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
          const val = maxV - ((cy - PAD.t) / vh) * span
          const cx = crosshair.x
          // 找最近的K线索引
          const idx = Math.min(data.length - 1, Math.max(0, Math.round((cx - PAD.l) / step - 0.5)))
          const barX = PAD.l + idx * step + step / 2
          return (
            <g pointerEvents="none">
              <line x1={PAD.l} y1={cy} x2={W - PAD.r} y2={cy} stroke="#94a3b8" strokeWidth="0.8" strokeDasharray="4 3" />
              <line x1={barX} y1={PAD.t} x2={barX} y2={PAD.t + vh} stroke="#94a3b8" strokeWidth="0.8" strokeDasharray="4 3" />
              <rect x={PAD.l - 54} y={cy - 8} width="50" height="16" rx="2" fill="#334155" />
              <text x={PAD.l - 29} y={cy + 3} textAnchor="middle" fontSize="10" fill="#e2e8f0" fontWeight="bold">{val.toFixed(2)}</text>
            </g>
          )
        })()}
      </svg>
      {hover && (
        <div className="kline-tooltip">
          {hover.date} 开 {hover.open} 高 {hover.high} 低 {hover.low} 收 {hover.close}
        </div>
      )}
    </div>
  )
}
