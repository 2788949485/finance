// 行情卡片：K线/分时切换 + 15秒实时轮询 + 新闻，跟随对话消息内嵌展示
import { useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { KlineBar, MinutePoint, NewsItem, QuoteResponse } from './types'
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
  const [news, setNews] = useState<NewsItem[]>([])
  const [allBars, setAllBars] = useState<KlineBar[]>([])
  const [mode, setMode] = useState<'day' | 'minute'>('day')
  const [live, setLive] = useState(false)
  const [err, setErr] = useState('')
  const timerRef = useRef<number | null>(null)

  const load = async (m: 'day' | 'minute', fresh: number) => {
    try {
      const q = await api.getQuote(code, 60, m, fresh)
      setData(q)
      setErr('')
    } catch {
      setErr('行情加载失败')
    }
  }

  // 预加载全量K线（"全部"选项用；只加载一次）
  useEffect(() => {
    let cancelled = false
    api.getQuote(code, 60, 'day', 0, 1).then((q) => {
      if (!cancelled && q.kline.length > 60) setAllBars(q.kline as KlineBar[])
    }).catch(() => {})
    return () => { cancelled = true }
  }, [code])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      await load(mode, 0)
      if (!cancelled) {
        const n = await api.getNews(code).catch(() => null)
        if (n) setNews(n.news)
      }
    })()
    return () => { cancelled = true }
  }, [code])

  // 15 秒实时轮询（fresh=1 绕过缓存）；分时模式轮询分时数据
  useEffect(() => {
    if (!live) return
    timerRef.current = window.setInterval(() => {
      load(mode, 1)
    }, 15000)
    return () => {
      if (timerRef.current) window.clearInterval(timerRef.current)
    }
  }, [live, mode])

  const switchMode = (m: 'day' | 'minute') => {
    setMode(m)
    load(m, 0)
  }

  if (!data) {
    return <div className="quote-loading">{err || `加载行情 ${code}...`}</div>
  }

  const b = data.brief as {
    name?: string; price?: number; change_pct?: number
    pe?: number; pb?: number; turnover?: number; market_cap?: number
  }
  const name = String(b.name ?? code)
  const price = b.price
  const change = b.change_pct
  const bars = (data.kline as KlineBar[]).filter((k) => k.date && typeof k.close === 'number')
  const minute = (data.kline as MinutePoint[]).filter((k) => k.time && typeof k.price === 'number')

  return (
    <div className="quote-card">
      <div className="quote-head">
        <span className="quote-name">{name}</span>
        <span className="quote-code">{code}</span>
        <span className={`quote-change ${(change ?? 0) >= 0 ? 'up' : 'down'}`}>
          {price ?? '--'} {change != null ? `${change >= 0 ? '+' : ''}${change}%` : ''}
        </span>
        <button
          className={`live-btn ${live ? 'on' : ''}`}
          onClick={() => setLive((v) => !v)}
          title="实时刷新（15秒）"
        >
          {live ? '● 实时' : '○ 实时'}
        </button>
      </div>
      <div className="quote-meta">
        {b.pe != null && <span>PE {b.pe}</span>}
        {b.pb != null && <span>PB {b.pb}</span>}
        {b.turnover != null && <span>换手 {b.turnover}%</span>}
        {b.market_cap != null && <span>市值 {b.market_cap}亿</span>}
      </div>
      <KLineChart
        bars={allBars.length > 60 ? allBars : bars}
        minute={minute}
        lastClose={data.last_close ?? null}
        symbol={name}
        mode={mode}
        onMode={switchMode}
      />
      {news.length > 0 && (
        <div className="quote-news">
          <div className="quote-news-head">最新新闻</div>
          {news.slice(0, 4).map((n, i) => (
            <div className="quote-news-item" key={i}>
              <span className="quote-news-time">{n.time.slice(5, 16)}</span>
              <span className="quote-news-title">{n.title.length > 70 ? n.title.slice(0, 70) + '…' : n.title}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
