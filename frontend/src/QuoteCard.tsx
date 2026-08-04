// 行情卡片：K线图 + 实时指标，跟随对话消息内嵌展示
import { useEffect, useState } from 'react'
import { api } from './api'
import type { QuoteResponse } from './types'
import KLineChart from './KLineChart'

export function extractCodes(text: string): string[] {
  // A股 6 位数字 / 港股 hk+5位 / 美股 us+代码 / 纯字母美股代码（排除常见英文停用词）
  const codes = text.match(/\b(hk\d{5}|us[A-Z]{2,5}|[036]\d{5}|[A-Z]{2,5})\b/g)
  if (!codes) return []
  const STOP = new Set(['THE', 'AND', 'ARE', 'FOR', 'NOT', 'YOU', 'OUR', 'HOW', 'WHY',
    'WAS', 'HAD', 'HAS', 'ITS', 'YOUR', 'USD', 'HKD', 'CNY', 'PE', 'PB', 'ROE', 'RSI',
    'MA5', 'MA10', 'MA20', 'MA60', 'KPI', 'AI', 'OK', 'NO', 'IN', 'ON', 'AT', 'TO', 'OF',
    'IS', 'IT', 'AS', 'BY', 'OR', 'AN', 'IF', 'BE', 'SO', 'UP', 'DOWN', 'HIGH', 'LOW'])
  return [...new Set(codes.filter((c) => !STOP.has(c)))]
}

export default function QuoteCard({ code }: { code: string }) {
  const [data, setData] = useState<QuoteResponse | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let alive = true
    api.getQuote(code, 60)
      .then((q) => { if (alive) setData(q) })
      .catch(() => { if (alive) setFailed(true) })
    return () => { alive = false }
  }, [code])

  if (failed) return null
  if (!data) return <div className="quote-loading">加载 {code} 行情...</div>

  const b = data.brief as {
    name?: string; price?: number; change_pct?: number
    pe?: number; pb?: number; turnover?: number; market_cap?: number
  }
  const name = String(b.name ?? code)
  const price = b.price
  const change = b.change_pct
  const up = (change ?? 0) >= 0

  return (
    <div className="quote-card">
      <div className="quote-meta">
        <div className="quote-title">{name} <span className="ticker-code">{code}</span></div>
        <div className="quote-price-row">
          <span className={`kline-price ${up ? 'up' : 'down'}`}>{price ?? '--'}</span>
          <span className={`kpi-value ${up ? 'up' : 'down'}`} style={{ fontSize: 13 }}>
            {change != null ? `${change > 0 ? '+' : ''}${change}%` : ''}
          </span>
        </div>
        <div className="quote-indicators">
          <span>PE {b.pe ?? '--'}</span>
          <span>PB {b.pb ?? '--'}</span>
          <span>换手 {b.turnover ?? '--'}%</span>
          <span>市值 {b.market_cap != null ? `${b.market_cap}亿` : '--'}</span>
        </div>
      </div>
      <KLineChart bars={data.kline} symbol={`${name} ${code}`} />
    </div>
  )
}
