// Markdown 渲染组件：聊天气泡与报告文本的美化渲染
import React from 'react'

// 简单内联格式化：**加粗** -> <strong>
function formatInline(text: string): React.ReactNode {
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**')) {
      return <strong key={i}>{p.slice(2, -2)}</strong>
    }
    if (p.startsWith('`') && p.endsWith('`')) {
      return <code key={i} style={{ fontFamily: 'var(--mono)', fontSize: '12px', background: 'var(--muted)', padding: '1px 4px', borderRadius: '2px' }}>{p.slice(1, -1)}</code>
    }
    return p
  })
}

export default function Markdown({ text }: { text: string }) {
  // 压缩所有连续空行为单换行
  const cleaned = text
    .replace(/\r\n/g, '\n')
    .replace(/\n{2,}/g, '\n')
    .trim()

  // 按换行分割，每行用<div>渲染（无margin无间距）
  const lines = cleaned.split('\n')

  return (
    <div className="md-body">
      {lines.map((line, i) => {
        const t = line.trim()
        if (!t) return null  // 跳过空行
        // 标题
        if (/^#{1,4}\s/.test(t)) {
          return <div key={i} style={{ fontWeight: 700, marginTop: i > 0 ? 8 : 0, marginBottom: 2 }}>{formatInline(t.replace(/^#{1,4}\s/, ''))}</div>
        }
        // 无序列表
        if (/^[-*]\s/.test(t)) {
          return <div key={i} style={{ paddingLeft: 16, textIndent: -10 }}>{'• '}{formatInline(t.replace(/^[-*]\s/, ''))}</div>
        }
        // 有序列表
        if (/^\d+\.\s/.test(t)) {
          return <div key={i} style={{ paddingLeft: 16, textIndent: -16 }}>{formatInline(t)}</div>
        }
        // 引用
        if (t.startsWith('> ')) {
          return <div key={i} style={{ borderLeft: '2px solid var(--border)', paddingLeft: 8, color: 'var(--text-2)' }}>{formatInline(t.slice(2))}</div>
        }
        // 分隔线
        if (t === '---') {
          return <hr key={i} style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '8px 0' }} />
        }
        // 普通行
        return <div key={i}>{formatInline(t)}</div>
      })}
    </div>
  )
}
