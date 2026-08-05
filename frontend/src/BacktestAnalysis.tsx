import { useState } from 'react'
import { MonteCarloHistogram, SensitivityHeatmap } from './BacktestCharts'

// 回测深度分析页面：PF/RF/评分 + 蒙特卡洛 + 分层测试 + 参数敏感度 + Walk-Forward

type AnalysisType = 'score' | 'monte_carlo' | 'layered' | 'sensitivity'

interface AnyRecord { [key: string]: any }

export default function BacktestAnalysis() {
  const [symbol, setSymbol] = useState('600519')
  const [strategy, setStrategy] = useState('ma_cross')
  const [days, setDays] = useState(250)
  const [analysisType, setAnalysisType] = useState<AnalysisType>('score')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [data, setData] = useState<AnyRecord | null>(null)

  const run = async () => {
    if (!symbol.trim()) { setError('请输入股票代码'); return }
    setLoading(true); setError(''); setData(null)
    try {
      const r = await fetch(`/api/backtest/analysis/${symbol.trim()}?strategy=${strategy}&days=${days}&analysis_type=${analysisType}`)
      const json = await r.json()
      if (json.error) { setError(json.error); return }
      setData(json as AnyRecord)
    } catch (e) {
      setError(e instanceof Error ? e.message : '分析失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="bt-analysis">
      <div className="backtest-controls">
        <input className="alert-input" placeholder="股票代码" value={symbol} onChange={e => setSymbol(e.target.value)} />
        <select className="alert-select" value={strategy} onChange={e => setStrategy(e.target.value)}>
          <option value="ma_cross">MA均线交叉</option>
          <option value="hold">买入持有</option>
        </select>
        <select className="alert-select" value={days} onChange={e => setDays(Number(e.target.value))}>
          <option value={120}>120天</option>
          <option value={250}>250天</option>
          <option value={500}>500天</option>
        </select>
        <select className="alert-select" value={analysisType} onChange={e => setAnalysisType(e.target.value as AnalysisType)}>
          <option value="score">PF/RF/综合评分</option>
          <option value="monte_carlo">蒙特卡洛压力测试</option>
          <option value="layered">分层过滤测试</option>
          <option value="sensitivity">参数敏感度</option>
        </select>
        <button className="btn-primary" onClick={run} disabled={loading}>
          {loading ? '分析中...' : '运行分析'}
        </button>
      </div>

      {error && <div className="alert-error">{error}</div>}

      {loading && <div className="bt-loading">分析中，蒙特卡洛需要几秒...</div>}

      {data && !loading && (
        <div className="bt-result">
          {analysisType === 'score' && <ScoreView data={data} />}
          {analysisType === 'monte_carlo' && <MonteCarloView data={data} />}
          {analysisType === 'layered' && <LayeredView data={data} />}
          {analysisType === 'sensitivity' && <SensitivityView data={data} />}
        </div>
      )}
    </div>
  )
}

function ScoreView({ data }: { data: AnyRecord }) {
  const s = data.score || data
  return (
    <div>
      <div className="bt-score-card bt-grade-{s.grade?.toLowerCase()}">
        <div className="bt-score-main">
          <span className="bt-score-value">{s.score ?? 'N/A'}</span>
          <span className="bt-score-grade">{s.grade ?? 'N/A'}</span>
        </div>
        <div className="bt-score-detail">
          <div>Profit Factor: <strong>{data.profit_factor ?? 'N/A'}</strong></div>
          <div>Recovery Factor: <strong>{data.recovery_factor ?? 'N/A'}</strong></div>
        </div>
      </div>
      {s.warnings?.length > 0 && (
        <div className="bt-warnings">
          <h4>风险提示</h4>
          {s.warnings.map((w: string, i: number) => <div key={i} className="alert-error">{w}</div>)}
        </div>
      )}
      <table className="portfolio-table">
        <thead><tr><th>指标</th><th>得分</th></tr></thead>
        <tbody>
          <tr><td>年化收益</td><td>{s.annual_return_score}</td></tr>
          <tr><td>Profit Factor</td><td>{s.pf_score}</td></tr>
          <tr><td>Recovery Factor</td><td>{s.rf_score}</td></tr>
          <tr><td>稳定性</td><td>{s.stability_score}</td></tr>
          <tr><td>回撤惩罚</td><td className="down">-{s.drawdown_penalty}</td></tr>
        </tbody>
      </table>
    </div>
  )
}

function MonteCarloView({ data }: { data: AnyRecord }) {
  if (data.error) return <div className="alert-error">{data.error}</div>
  return (
    <div>
      <h4>蒙特卡洛压力测试 ({data.simulations || 0}次模拟)</h4>
      <table className="portfolio-table">
        <thead><tr><th>指标</th><th>原始回测</th><th>95%分位</th><th>最差情况</th></tr></thead>
        <tbody>
          <tr><td>收益率</td><td>{data.original_return}%</td><td>{data.final_return_p5}%</td><td>{data.final_return_p95}%</td></tr>
          <tr><td>最大回撤</td><td>{data.original_drawdown}%</td><td className="down">{data.p95_max_drawdown}%</td><td className="down">{data.worst_max_drawdown}%</td></tr>
        </tbody>
      </table>
      <div className="bt-mc-extras">
        <div>爆仓概率: <strong className={data.blowup_probability > 0 ? 'down' : ''}>{data.blowup_probability}%</strong></div>
        <div>最差连续亏损: <strong>{data.worst_consecutive_losses}次</strong></div>
        <div>中位数收益: <strong>{data.final_return_p50}%</strong></div>
        {data.suggested_position_ratio && (
          <div>建议仓位: <strong>{(data.suggested_position_ratio * 100).toFixed(0)}%</strong></div>
        )}
      </div>
      {data.histogram && data.histogram.length > 0 && (
        <div className="backtest-equity">
          <h4>收益分布直方图</h4>
          <MonteCarloHistogram histogram={data.histogram} originalReturn={data.original_return} />
        </div>
      )}
      {data.drawdown_histogram && data.drawdown_histogram.length > 0 && (
        <div className="backtest-equity">
          <h4>回撤分布直方图</h4>
          <MonteCarloHistogram histogram={data.drawdown_histogram} />
        </div>
      )}
    </div>
  )
}

function LayeredView({ data }: { data: AnyRecord }) {
  if (data.error) return <div className="alert-error">{data.error}</div>
  const layers = data.layers || []
  return (
    <div>
      <h4>分层过滤测试</h4>
      <table className="portfolio-table">
        <thead><tr><th>层级</th><th>交易数</th><th>收益%</th><th>回撤%</th><th>胜率%</th><th>PF</th><th>贡献(收益)</th></tr></thead>
        <tbody>
          {layers.map((l: AnyRecord, i: number) => (
            <tr key={i}>
              <td>{l.name}</td>
              <td>{l.trades}</td>
              <td className={l.total_return >= 0 ? 'up' : 'down'}>{l.total_return}</td>
              <td className="down">{l.max_drawdown}</td>
              <td>{l.win_rate}</td>
              <td>{l.profit_factor}</td>
              <td>{l.contribution ? `${l.contribution.return_delta > 0 ? '+' : ''}${l.contribution.return_delta}%` : '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function SensitivityView({ data }: { data: AnyRecord }) {
  if (data.error) return <div className="alert-error">{data.error}</div>
  return (
    <div>
      <h4>参数敏感度 ({data.combos_tested || 0}种组合)</h4>
      <div className="bt-sensitivity-verdict">{data.stability_verdict}</div>
      <div className="bt-sensitivity-stats">
        <span>中位数收益: {data.median_return}%</span>
        <span>盈利比例: {data.profitable_ratio}%</span>
      </div>
      {/* 二维热力图 */}
      {data.results && data.results.length > 1 && (
        <div className="backtest-equity">
          <h4>参数热力图</h4>
          <SensitivityHeatmap results={data.results} />
        </div>
      )}
      <table className="portfolio-table">
        <thead><tr><th>快线</th><th>慢线</th><th>收益%</th><th>回撤%</th><th>交易数</th><th>PF</th></tr></thead>
        <tbody>
          {(data.results || []).map((r: AnyRecord, i: number) => (
            <tr key={i}>
              <td>{r.fast}</td>
              <td>{r.slow}</td>
              <td className={r.total_return >= 0 ? 'up' : 'down'}>{r.total_return}</td>
              <td className="down">{r.max_drawdown}</td>
              <td>{r.trades}</td>
              <td>{r.pf}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
