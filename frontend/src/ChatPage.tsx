// 智能对话页：ReAct 智能体聊天，行情卡片（K线图）跟随消息内嵌
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api'
import type { ChatMessage, ChatSession } from './types'
import QuoteCard, { extractCodes } from './QuoteCard'
import Markdown from './Markdown'

const TOOL_LABEL: Record<string, string> = {
  get_quote: '查询实时行情',
  get_kline: '拉取K线数据',
  get_financials: '查询财务数据',
  get_lhb: '查询龙虎榜',
  get_news: '读取新闻',
  run_research: '运行多智能体投研',
}

// 单条消息 + 内嵌行情卡片
function MessageItem({ m }: { m: ChatMessage }) {
  const codes = extractCodes(m.content)
  return (
    <div className={`msg ${m.role}`}>
      <div className="msg-bubble">
        <div className="msg-text"><Markdown text={m.content} /></div>
        {m.tool_calls && m.tool_calls.length > 0 && (
          <div className="msg-tools">
            {m.tool_calls.map((t, j) => (
              <span key={j}>{TOOL_LABEL[t.name] || t.name}</span>
            ))}
          </div>
        )}
      </div>
      {codes.length > 0 && (
        <div className="msg-quotes">
          {codes.map((code) => <QuoteCard key={code} code={code} />)}
        </div>
      )}
    </div>
  )
}

export default function ChatPage() {
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [sessionId, setSessionId] = useState<number | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [confirmDel, setConfirmDel] = useState<number | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)

  const loadSessions = useCallback(async () => {
    try {
      setSessions(await api.listChats())
    } catch { /* ignore */ }
  }, [])

  useEffect(() => { loadSessions() }, [loadSessions])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, busy])

  const openSession = async (id: number) => {
    setSessionId(id)
    setMessages(await api.chatMessages(id))
    setError('')
  }

  const removeSession = async (id: number) => {
    try {
      await api.deleteChat(id)
      setSessions((prev) => prev.filter((s) => s.id !== id))
      if (sessionId === id) {
        setSessionId(null)
        setMessages([])
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除失败')
    } finally {
      setConfirmDel(null)
    }
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
            <div key={s.id} className={`chat-item ${s.id === sessionId ? 'active' : ''}`}>
              <button className="chat-item-main" onClick={() => openSession(s.id)}>
                <span className="chat-item-title">{s.title}</span>
                <span className="chat-item-meta">{s.msg_count} 条</span>
              </button>
              <button
                className={`chat-item-del ${confirmDel === s.id ? 'confirming' : ''}`}
                title="删除对话"
                onClick={(e) => {
                  e.stopPropagation()
                  if (confirmDel === s.id) removeSession(s.id)
                  else { setConfirmDel(s.id); setTimeout(() => setConfirmDel((c) => (c === s.id ? null : c)), 3000) }
                }}
              >{confirmDel === s.id ? '确认？' : '删除'}</button>
            </div>
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
                <li>"分析一下 600519 的基本面"</li>
                <li>"600519 最近为什么涨？"</li>
                <li>"跑一份 000001 的完整投研报告"</li>
                <li>"对比 300750 和 002594 的估值"</li>
              </ul>
              <p className="chat-hint">我会自动查询实时行情、财务、龙虎榜、新闻等真实数据来回答，涉及股票的消息下方会直接展示 K 线图</p>
            </div>
          )}
          {messages.map((m, i) => <MessageItem key={i} m={m} />)}
          {busy && <div className="msg assistant"><div className="msg-bubble typing">智能体思考中...</div></div>}
          {error && <div className="error-box">{error}</div>}
          <div ref={bottomRef} />
        </div>

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
