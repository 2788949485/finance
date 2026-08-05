import { useState } from 'react'
import { api } from './api'
import type { BacktestResult } from './types'
import BacktestAnalysis from './BacktestAnalysis'
import { EquityChart, DrawdownChart, MonthlyHeatmap } from './BacktestCharts'

const STRATEGIES = [
  { key: 'ma_cross', label: 'MA均线交叉', params: [
    { key: 'fast_period', label: '快线', default: 5 },
    { key: 'slow_period', label: '慢线', default: 20 },
  ]},
  { key: 'dual_ma', label: '双均线(可调)', params: [
    { key: 'fast_period', label: '快线', default: 10 },
    { key: 'slow_period', label: '慢线', default: 30 },
  ]},
  { key: 'macd', label: 'MACD交叉', params: [] },
  { key: 'kdj', label: 'KDJ金叉', params: [] },
  { key: 'boll', label: 'BOLL带突破', params: [
    { key: 'boll_period', label: '周期', default: 20 },
  ]},
  { key: 'rsi', label: 'RSI超买超卖', params: [
    { key: 'rsi_period', label: '周期', default: 14 },
    { key: 'rsi_oversold', label: '超卖线', default: 30 },
    { key: 'rsi_overbought', label: '超买线', default: 70 },
  ]},
  { key: 'grid', label: '网格交易', params: [
    { key: 'grid_pct', label: '间距%', default: 5 },
  ]},
  { key: 'hold', label: '买入持有(基准)', params: [] },
  { key: 'ai', label: 'AI增强策略', params: [] },
]

type PageTab = 'basic' | 'analysis'

export default function BacktestPage() {
  const [pageTab, setPageTab] = useState<PageTab>('basic')
  const [symbol, setSymbol] = useState('')
  const [strategy, setStrategy] = useState('ma_cross')
  const [days, setDays] = useState(120)
  const [enableCost, setEnableCost] = useState(true)
  const [result, setResult] = useState<BacktestResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [params, setParams] = useState<Record<string, string>>({})

  const currentStrategy = STRATEGIES.find(s => s.key === strategy)

  const run = async () => {
    if (!symbol.trim()) { setError('请输入股票代码'); return }
    setLoading(true); setError('')
    try {
      // 转换参数
      const numParams: Record<string, any> = {}
      if (currentStrategy) {
        for (const p of currentStrategy.params) {
          const val = params[`${strategy}_${p.key}`] ?? String(p.default)
          numParams[p.key] = val
        }
      }
      const r = await api.getBacktest(symbol.trim(), strategy, days, enableCost ? 1 : 0, numParams)
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
        <select className="alert-select" value={strategy} onChange={e => { setStrategy(e.target.value); setParams({}) }}>
          {STRATEGIES.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
        <select className="alert-select" value={days} onChange={e => setDays(parseInt(e.target.value))}>
          <option value={60}>60天</option>
          <option value={120}>120天</option>
          <option value={250}>250天</option>
          <option value={500}>500天</option>
        </select>
        <label className="bt-cost-toggle">
          <input type="checkbox" checked={enableCost} onChange={e => setEnableCost(e.target.checked)} />
          <span>含手续费</span>
        </label>
        <button className="btn-primary" onClick={run} disabled={loading}>
          {loading ? (strategy === 'ai' ? 'AI分析中(较慢)...' : '回测中...') : '开始回测'}
        </button>
      </div>

      {/* 策略参数面板 */}
      {currentStrategy && currentStrategy.params.length > 0 && (
        <div className="bt-params">
          <span className="bt-params-label">{currentStrategy.label} 参数：</span>
          {currentStrategy.params.map(p => (
            <label key={p.key} className="bt-param-item">
              <span>{p.label}</span>
              <input type="number" className="bt-param-input"
                value={params[`${strategy}_${p.key}`] ?? p.default}
                onChange={e => setParams(prev => ({ ...prev, [`${strategy}_${p.key}`]: e.target.value }))} />
            </label>
          ))}
        </div>
      )}

      {error && <span className="alert-error">{error}</span>}

      {result && !result.error && (
        <>
          {/* KPI 卡片 - 扩展指标 */}
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
              <span className="kpi-label">年化收益</span>
              <span className={`kpi-value ${(result.annual_return ?? 0) >= 0 ? 'up' : 'down'}`}>
                {(result.annual_return ?? 0) >= 0 ? '+' : ''}{(result.annual_return ?? 0).toFixed(1)}%
              </span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">最大回撤</span>
              <span className="kpi-value down">-{result.max_drawdown}%</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">夏普比率</span>
              <span className="kpi-value">{(result.sharpe_ratio ?? 0).toFixed(2)}</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">Sortino</span>
              <span className="kpi-value">{(result.sortino_ratio ?? 0).toFixed(2)}</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">Calmar</span>
              <span className="kpi-value">{(result.calmar_ratio ?? 0).toFixed(2)}</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">波动率</span>
              <span className="kpi-value">{(result.volatility ?? 0).toFixed(1)}%</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">交易次数</span>
              <span className="kpi-value">{result.trades}</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">胜率</span>
              <span className="kpi-value">{result.win_rate}%</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">最大连亏</span>
              <span className="kpi-value">{result.max_consecutive_losses ?? '-'}次</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">EWMA夏普</span>
              <span className="kpi-value">{(result.ewm_sharpe ?? 0).toFixed(2)}</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">CVaR(95%)</span>
              <span className="kpi-value down">{(result.cvar_95 ?? 0).toFixed(2)}%</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">偏度/峰度</span>
              <span className="kpi-value">{(result.skewness ?? 0).toFixed(2)}/{(result.kurtosis ?? 0).toFixed(2)}</span>
            </div>
            <div className="kpi-card">
              <span className="kpi-label">回撤恢复</span>
              <span className="kpi-value">{result.max_dd_duration ?? '-'}天</span>
            </div>
          </div>

          <div className="backtest-period">
            期间: {result.period} | 初始资金: {result.initial_capital.toLocaleString()} | 终值: {result.final_value.toLocaleString()}
          </div>

          <div className="backtest-equity">
            <h4>权益曲线</h4>
            <EquityChart curve={result.equity_curve} initialCapital={result.initial_capital} />
          </div>

          <div className="backtest-equity">
            <h4>回撤水下图 (Underwater)</h4>
            <DrawdownChart curve={result.equity_curve} />
          </div>

          <div className="backtest-equity">
            <h4>月度收益热力图</h4>
            <MonthlyHeatmap curve={result.equity_curve} />
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
                      {strategy === 'ai' && <td className="pf-code">{t.reason || ''}</td>}
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
