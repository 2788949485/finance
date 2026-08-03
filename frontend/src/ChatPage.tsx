// 智能对话页：ReAct 智能体聊天 + 行情卡片 + K线图
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { ChatMessage, ChatSession, QuoteResponse } from './types'
import KLineChart from './KLineChart'

const TOOL_LABEL: Record<string, string> = {
  get_quote: '查询实时行情',
  get_kline: '拉取K线数据',
  get_financials: '查询财务数据',
  get_lhb: '查询龙虎榜',
  get_news: '读取新闻',
  run_research: '运行多智能体投研',
}

export default function ChatPage() {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [quotes, setQuotes] = useState<Record<string, QuoteResponse>>({})
  const bottomRef = useRef<HTMLDivElement>(null)

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await api.listChats())
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { loadSessions() }, [loadSessions])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  // 识别消息中的股票代码并拉行情
  const ensureQuote = useCallback(async (text: string) => {
    const codes = text.match(/\b([036]\d{5})\b/g)
    if (!codes) return
    for (const code of [...new Set(codes)]) {
      if (!quotes[code]) {
        try {
          const q = await api.getQuote(code, 60)
          setQuotes((prev) => ({ ...prev, [code]: q }))
        } catch { /* ignore */ }
      }
    }
  }, [quotes])

  useEffect(() => {
    const last = messages[messages.length - 1]
    if (last && (last.role === 'assistant' || last.role === 'user')) {
      ensureQuote(last.content)
    }
  }, [messages, ensureQuote])

  const openSession = async (id: number) => {
    setSessionId(id)
    setMessages(await api.chatMessages(id))
    setError('')
  }

  const newSession = async () => {
    const { session_id } = await api.newChat()
    setSessionId(session_id)
    setMessages([])
    await loadSessions()
    setError('')
  }

  const send = async () => {
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    setBusy(true)
    setError('')
    // 本地先显示用户消息
    setMessages((prev) => [...prev, { role: 'user', content: text, created_at: new Date().toISOString() }])
    try {
      const r = await api.sendChat(text, sessionId ?? undefined)
      setSessionId(r.session_id)
      setMessages((prev) => [...prev, {
        role: 'assistant', content: r.reply, created_at: new Date().toISOString(), tool_calls: r.tool_calls,
      }])
      await loadSessions()
    } catch (e) {
      setError(e instanceof Error ? e.message : '发送失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="chat-layout">
      <aside className="chat-side">
        <button className="new-chat-btn" onClick={newSession}>新建对话</button>
        <div className="chat-list">
          {sessions.map((s) => (
            <button
              key={s.id}
              className={`chat-item ${s.id === sessionId ? 'active' : ''}`}
              onClick={() => openSession(s.id)}
            >
              <span className="chat-item-title">{s.title}</span>
              <span className="chat-item-meta">{s.msg_count} 条</span>
            </button>
          ))}
        </div>
      </aside>

      <div className="chat-main">
        <div className="chat-messages">
          {messages.length === 0 && (
            <div className="chat-welcome">
              <h3>我是 FinanceCrew 投研助理</h3>
              <p>可以问我任何 A 股问题，例如：</p>
              <ul>
                <li>“分析一下 600519 的基本面”</li>
                <li>“600519 最近为什么涨？”</li>
                <li>“跑一份 000001 的完整投研报告”</li>
                <li>“对比 300750 和 002594 的估值”</li>
              </ul>
              <p className="chat-hint">我会自动查询实时行情、财务、龙虎榜、新闻等真实数据来回答</p>
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`msg ${m.role}`}>
              <div className="msg-bubble">
                <div className="msg-text">{m.content}</div>
                {m.tool_calls && m.tool_calls.length > 0 && (
                  <div className="msg-tools">
                    {m.tool_calls.map((t, j) => (
                      <span key={j}>{TOOL_LABEL[t.name] || t.name}</span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}
          {busy && <div className="msg assistant"><div className="msg-bubble typing">智能体思考中...</div></div>}
          {error && <div className="error-box">{error}</div>}
          <div ref={bottomRef} />
        </div>

        {/* 行情卡片 + K线 */}
        {Object.keys(quotes).length > 0 && (
          <div className="quote-area">
            {Object.entries(quotes).map(([code, q]) => (
              <div className="quote-card" key={code}>
                <KLineChart bars={q.kline} symbol={`${String(q.brief.name ?? code)} ${code}`} />
              </div>
            ))}
          </div>
        )}

        <div className="chat-input">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send()}
            placeholder="输入问题，回车发送（支持直接输入股票代码）"
          />
          <button onClick={send} disabled={busy || !input.trim()}>发送</button>
        </div>
      </div>
    </div>
  )
}
