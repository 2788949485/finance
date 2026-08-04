// 自绘 SVG 图表：日K蜡烛图 + 分时折线图（无第三方库）
// 日K支持：滚轮缩放（放大=减少K线数量，蜡烛更宽更清晰）、拖动平移、双击重置
import { useRef, useState } from 'react'
import type { KlineBar, MinutePoint } from './types'

const UP = '#22c55e'
const DOWN = '#ef4444'
const MAX_ZOOM = 8
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

  const W = 680, H = 240, PAD = { t: 14, r: 10, b: 22, l: 56 }

  const resetZoom = () => { setZoom(1); setPan(0) }

  const switchRange = (n: number | 'all') => { setRange(n); resetZoom() }

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
    const span = maxV - minV || 1
    const vw = W - PAD.l - PAD.r
    const vh = H - PAD.t - PAD.b
    const y = (v: number) => PAD.t + ((maxV - v) / span) * vh
    const step = vw / minute.length
    const pts = (arr: number[]) => arr.map((v, i) => `${PAD.l + i * step},${y(v)}`).join(' ')
    const lastP = minute[minute.length - 1]
    const trend = lastP.price >= (lastClose ?? lastP.price) ? UP : DOWN
    return (
      <div className="kline-wrap">
        <div className="kline-head">
          <span className="kline-symbol">{symbol} 分时</span>
          <span className="kline-price" style={{ color: trend }}>
            {lastP.price} <small>{trend === UP ? '▲' : '▼'}</small>
          </span>
          <span className="kline-range">
            {onMode && <button className="ghost" onClick={() => onMode('day')}>日K</button>}
            <button className="ghost active">分时</button>
          </span>
        </div>
        <svg viewBox={`0 0 ${W} ${H}`} className="kline-svg">
          {/* 昨收基准线 */}
          <line x1={PAD.l} x2={W - PAD.r} y1={y(base)} y2={y(base)} stroke="#64748b" strokeWidth="1" strokeDasharray="4 4" />
          <text x={PAD.l + 4} y={y(base) - 4} fontSize="10" fill="#64748b">昨收 {base.toFixed(2)}</text>
          {/* 网格 */}
          {[0.25, 0.5, 0.75].map((r) => (
            <line key={r} x1={PAD.l} x2={W - PAD.r} y1={PAD.t + vh * r} y2={PAD.t + vh * r} stroke="#1e293b" strokeWidth="1" />
          ))}
          {/* 价格线 */}
          <polyline points={pts(prices)} fill="none" stroke={trend} strokeWidth="1.6" />
          {/* 均价线 */}
          {avgs.length > 1 && (
            <polyline points={pts(avgs)} fill="none" stroke="#f59e0b" strokeWidth="1.1" opacity="0.85" />
          )}
          {/* Y 轴刻度 */}
          {[0, 0.5, 1].map((r) => (
            <text key={r} x={PAD.l - 6} y={PAD.t + vh * r + 4} textAnchor="end" fontSize="10" fill="#64748b">
              {(maxV - span * r).toFixed(2)}
            </text>
          ))}
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

  // 缩放事件：闭包引用当前窗口，每次渲染重建
  const onWheel = (e: React.WheelEvent<SVGSVGElement>) => {
    e.preventDefault()
    const svg = svgRef.current
    if (!svg) return
    const rect = svg.getBoundingClientRect()
    if (!rect.width) return
    const ratio = (e.clientX - rect.left) / rect.width
    const newZoom = Math.min(MAX_ZOOM, Math.max(1, zoom * (e.deltaY < 0 ? 1.25 : 0.8)))
    const newWin = Math.max(MIN_WIN, Math.round(rawLen / newZoom))
    const anchorIdx = start + ratio * winCount
    const newStart = Math.min(Math.max(0, Math.round(anchorIdx - ratio * newWin)), rawLen - newWin)
    setZoom(newZoom)
    setPan(1 - newStart / Math.max(1, rawLen - newWin))
  }

  // 拖动平移（仅放大后可用）
  const onMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    if (zoom <= 1) return
    setDrag({ x: e.clientX, x0: start })
    e.preventDefault()
  }
  const onMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!drag) return
    const svg = svgRef.current
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
    <div className="kline-wrap">
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
            <button className="ghost zoom-ind" onClick={resetZoom} title="重置缩放（或双击图表）">{zoom.toFixed(1)}x ⇲</button>
          )}
        </span>
      </div>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="kline-svg"
        style={{ cursor: zoom > 1 ? (drag ? 'grabbing' : 'grab') : 'crosshair', touchAction: 'none' }}
        onWheel={onWheel}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={endDrag}
        onMouseLeave={() => { endDrag(); setHover(null) }}
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
        {hover && (
          <g>
            <line x1={PAD.l} x2={W - PAD.r} y1={y(hover.close)} y2={y(hover.close)} stroke="#64748b" strokeWidth="0.6" strokeDasharray="3 3" />
            <text x={W - PAD.r} y={y(hover.close) - 4} textAnchor="end" fontSize="10" fill="#94a3b8">
              {hover.close.toFixed(2)}
            </text>
          </g>
        )}
      </svg>
      {hover && (
        <div className="kline-tooltip">
          {hover.date} 开 {hover.open} 高 {hover.high} 低 {hover.low} 收 {hover.close}
        </div>
      )}
    </div>
  )
}
