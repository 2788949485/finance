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
  get_news: '读取个股新闻',
  get_stock_news: '读取个股新闻',
  get_market_news: '获取实时快讯',
  run_research: '运行多智能体投研',
}

interface FlowStep { name: string; args: Record<string, unknown>; status: 'running' | 'done' }

// 智能体工作流面板：实时步骤 + 默认折叠
function FlowPanel({ steps, collapsed, onToggle }: {
  steps: FlowStep[]; collapsed: boolean; onToggle: () => void
}) {
  const doneCount = steps.filter((s) => s.status === 'done').length
  return (
    <div className="flow-panel">
      <button className="flow-head" onClick={onToggle}>
        <span className="flow-title">智能体工作流</span>
        {steps.length > 0 && (
          <span className="flow-status">
            {doneCount === steps.length && steps.length > 0
              ? `完成 ${steps.length} 步工具调用`
              : `正在执行 ${doneCount}/${steps.length} 步`}
          </span>
        )}
        <span className={`flow-arrow ${collapsed ? '' : 'open'}`}>▾</span>
      </button>
      {!collapsed && (
        <div className="flow-body">
          {steps.length === 0 && <div className="flow-empty">正在规划执行步骤...</div>}
          {steps.map((s, i) => (
            <div key={i} className={`flow-step ${s.status}`}>
              <span className="flow-step-icon">{s.status === 'done' ? '✓' : '◌'}</span>
              <span className="flow-step-name">{TOOL_LABEL[s.name] || s.name}</span>
              {s.status === 'running' && <span className="flow-step-spin" />}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// 单条消息：行情卡片只跟随用户消息展示（助手回复不重复显示）
function MessageItem({ m }: { m: ChatMessage }) {
  const codes = m.role === 'user' ? extractCodes(m.content) : []
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
  const [flowSteps, setFlowSteps] = useState<FlowStep[]>([])
  const [flowCollapsed, setFlowCollapsed] = useState(false)
  const [pendingReply, setPendingReply] = useState('')
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
    setFlowSteps([])
    setFlowCollapsed(false)  // 执行中展开显示实时步骤
    setPendingReply('')
    setMessages((prev) => [...prev, { role: 'user', content: text, created_at: new Date().toISOString() }])
    try {
      let sid = sessionId
      const reply = await api.streamChat(text, sid ?? undefined, (ev) => {
        if (ev.type === 'tool_start') {
          setFlowSteps((prev) => [...prev, { name: ev.name, args: ev.args, status: 'running' }])
        } else if (ev.type === 'tool_end') {
          // 标记最近一个 running 步骤为 done
          setFlowSteps((prev) => {
            const idx = [...prev].reverse().findIndex((s) => s.status === 'running')
            if (idx === -1) return prev
            const i = prev.length - 1 - idx
            return prev.map((s, j) => (j === i ? { ...s, status: 'done' } : s))
          })
        } else if (ev.type === 'chunk') {
          // 流式输出：逐块追加（打字机效果）
          setPendingReply((prev) => prev + ev.content)
        } else if (ev.type === 'msg') {
          setPendingReply(ev.content)  // 完整回复兜底
        } else if (ev.type === 'done') {
          sid = ev.session_id
        }
      })
      setSessionId(sid)
      setMessages((prev) => [...prev, {
        role: 'assistant', content: reply || pendingReply, created_at: new Date().toISOString(),
      }])
      setPendingReply('')
      await loadSessions()
      // 回复完成：工作流默认折叠
      setFlowCollapsed(true)
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
                  else { setConfirmDel(s.id); setTimeout(() => setConfirmDel((c) => (c === s.id ? null : c)), 8000) }
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
              <p>可以问我任何股票问题，例如：</p>
              <ul>
                <li>"分析一下 600519 的基本面"</li>
                <li>"600519 最近为什么涨？"</li>
                <li>"跑一份 000001 的完整投研报告"</li>
                <li>"对比 300750 和 002594 的估值"</li>
              </ul>
              <p className="chat-hint">我会自动查询实时行情、财务、龙虎榜、新闻等真实数据来回答</p>
              <div className="chat-hot">
                <div className="chat-hot-head">热门行情</div>
                <div className="chat-hot-grid">
                  {['600519', 'hk00700', 'usAAPL', '300750'].map((code) => (
                    <QuoteCard key={code} code={code} />
                  ))}
                </div>
              </div>
            </div>
          )}
          {messages.map((m, i) => <MessageItem key={i} m={m} />)}
          {busy && pendingReply && (
            <div className="msg assistant">
              <div className="msg-bubble"><div className="msg-text"><Markdown text={pendingReply} /></div></div>
            </div>
          )}
          {busy && !pendingReply && <div className="msg assistant"><div className="msg-bubble typing">智能体思考中...</div></div>}
          {error && <div className="error-box">{error}</div>}
          <div ref={bottomRef} />
        </div>

        {/* 工作流面板：回答时才显示（有步骤即出现），完成后折叠成摘要 */}
        {(busy || flowSteps.length > 0) && (
          <div className="chat-flow">
            <FlowPanel
              steps={flowSteps}
              collapsed={flowCollapsed}
              onToggle={() => setFlowCollapsed((v) => !v)}
            />
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
