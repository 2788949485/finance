import { useState } from 'react'
import { api } from './api'
import type { BacktestResult } from './types'
import BacktestAnalysis from './BacktestAnalysis'

const STRATEGIES = [
  { key: 'ma_cross', label: 'MA均线交叉' },
  { key: 'grid', label: '网格交易' },
  { key: 'hold', label: '买入持有(基准)' },
  { key: 'ai', label: 'AI增强策略' },
]

type PageTab = 'basic' | 'analysis'

export default function BacktestPage() {
  const [pageTab, setPageTab] = useState<PageTab>('basic')
  const [symbol, setSymbol] = useState('')
  const [strategy, setStrategy] = useState('ma_cross')
  const [days, setDays] = useState(120)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const run = async () => {
    if (!symbol.trim()) { setError('请输入股票代码'); return }
    setLoading(true); setError('')
    try {
      const r = await api.getBacktest(symbol.trim(), strategy, days)
      setResult(r)
    } catch (e: any) { setError(e.message || '回测失败') }
    finally { setLoading(false) }
  }

  return (
    <div className="pane">
      <div className="pane-head">
        <h2>策略回测</h2>
        <div className="bt-tabs">
          <button className={pageTab === 'basic' ? 'active' : ''} onClick={() => setPageTab('basic')}>基础回测</button>
          <button className={pageTab === 'analysis' ? 'active' : ''} onClick={() => setPageTab('analysis')}>深度分析</button>
        </div>
      </div>

      {pageTab === 'analysis' ? (
        <BacktestAnalysis />
      ) : (
      <>
      <div className="backtest-controls">
        <input className="alert-input" placeholder="股票代码（如 600519）"
          value={symbol} onChange={e => setSymbol(e.target.value)} />
        <select className="alert-select" value={strategy} onChange={e => setStrategy(e.target.value)}>
          {STRATEGIES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
        <select className="alert-select" value={days} onChange={e => setDays(parseInt(e.target.value))}>
          <option value={60}>60天</option>
          <option value={120}>120天</option>
          <option value={250}>250天</option>
        </select>
        <button className="btn-primary" onClick={run} disabled={loading}>
          {loading ? (strategy === 'ai' ? 'AI分析中(较慢)...' : '回测中...') : '开始回测'}
        </button>
      </div>
      {error && <span className="alert-error">{error}</span>}

      {result && !result.error && (
        <>
          <div className="backtest-summary">
            <div className="kpi-card">
              <span className="kpi-label">策略收益</span>
              <span className={`kpi-value ${result.total_return >= 0 ? 'up' : 'down'}`}>
                {result.total_return >= 0 ? '+' : ''}{result.total_return}%
              </span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">基准(持有)</span>
              <span className={`kpi-value ${result.benchmark_return >= 0 ? 'up' : 'down'}`}>
                {result.benchmark_return >= 0 ? '+' : ''}{result.benchmark_return}%
              </span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">超额收益</span>
              <span className={`kpi-value ${result.excess_return >= 0 ? 'up' : 'down'}`}>
                {result.excess_return >= 0 ? '+' : ''}{result.excess_return}%
              </span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">最大回撤</span>
              <span className="kpi-value down">-{result.max_drawdown}%</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">交易次数</span>
              <span className="kpi-value">{result.trades}</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">胜率</span>
              <span className="kpi-value">{result.win_rate}%</span>
            </div>
          </div>

          <div className="backtest-period">
            期间: {result.period} | 初始资金: {result.initial_capital.toLocaleString()} | 终值: {result.final_value.toLocaleString()}
          </div>

          <div className="backtest-equity">
            <h4>权益曲线</h4>
            <EquityChart curve={result.equity_curve} />
          </div>

          {result.trades_log.length > 0 && (
            <div className="backtest-trades">
              <h4>交易记录（最近{result.trades_log.length}笔）</h4>
              <table className="portfolio-table">
                <thead>
                  <tr><th>日期</th><th>操作</th><th>价格</th><th>数量</th>{strategy === 'ai' && <th>AI理由</th>}</tr>
                </thead>
                <tbody>
                  {result.trades_log.map((t, i) => (
                    <tr key={i}>
                      <td>{t.date}</td>
                      <td className={t.action === 'BUY' ? 'up' : 'down'}>{t.action === 'BUY' ? '买入' : '卖出'}</td>
                      <td>{t.price}</td>
                      <td>{t.shares}</td>
                      {strategy === 'ai' && <td className="pf-code">{(t as any).reason || ''}</td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
      </>
      )}
    </div>
  )
}

// 权益曲线 SVG
function EquityChart({ curve }: { curve: { date: string; value: number }[] }) {
  if (!curve || curve.length < 2) return null
  const values = curve.map(p => p.value)
  const minV = Math.min(...values)
  const maxV = Math.max(...values)
  const range = maxV - minV || 1
  const W = 800, H = 160, PAD = { l: 50, r: 20, t: 10, b: 20 }

  const points = curve.map((p, i) => {
    const x = PAD.l + (i / (curve.length - 1)) * (W - PAD.l - PAD.r)
    const y = PAD.t + (1 - (p.value - minV) / range) * (H - PAD.t - PAD.b)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  })

  const baselineY = PAD.t + (1 - (100000 - minV) / range) * (H - PAD.t - PAD.b)

  return (
    <svg className="equity-svg" viewBox={`0 0 ${W} ${H}`} style={{ width: '100%', height: H }}>
      {/* 基准线（10万） */}
      {baselineY > PAD.t && baselineY < H - PAD.b && (
        <line x1={PAD.l} y1={baselineY} x2={W - PAD.r} y2={baselineY}
          stroke="var(--text-3)" strokeWidth="1" strokeDasharray="4 2" />
      )}
      {/* 权益曲线 */}
      <polyline points={points.join(' ')}
        fill="none" stroke="var(--accent)" strokeWidth="1.5" />
      {/* Y轴标签 */}
      <text x={PAD.l - 5} y={PAD.t + 4} textAnchor="end" className="axis-label">{maxV.toLocaleString()}</text>
      <text x={PAD.l - 5} y={H - PAD.b} textAnchor="end" className="axis-label">{minV.toLocaleString()}</text>
    </svg>
  )
}
