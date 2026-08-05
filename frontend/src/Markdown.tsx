// Markdown 渲染组件：聊天气泡与报告文本的美化渲染
import React from 'react'
import katex from 'katex'

// 渲染LaTeX公式
function renderMath(tex: string, displayMode: boolean): string {
  try {
    return katex.renderToString(tex, { displayMode, throwOnError: false, output: 'html' })
  } catch {
    return tex
  }
}

// 简单内联格式化：**加粗** -> <strong>，$公式$ -> katex
function formatInline(text: string): React.ReactNode {
  // 先提取$...$行内公式
  const parts = text.split(/(\*\*[^*]+\*\*|`[^`]+`|\$[^$]+\$)/g)
  return parts.map((p, i) => {
    if (p.startsWith('**') && p.endsWith('**')) {
      return <strong key={i}>{p.slice(2, -2)}</strong>
    }
    if (p.startsWith('`') && p.endsWith('`')) {
      return <code key={i} style={{ fontFamily: 'var(--mono)', fontSize: '12px', background: 'var(--muted)', padding: '1px 4px', borderRadius: '2px' }}>{p.slice(1, -1)}</code>
    }
    if (p.startsWith('$') && p.endsWith('$') && p.length > 2) {
      return <span key={i} dangerouslySetInnerHTML={{ __html: renderMath(p.slice(1, -1), false) }} />
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

  // 先提取表格块和块级公式
  const lines = cleaned.split('\n')
  const blocks: { type: 'text' | 'table' | 'math'; lines: string[]; tex?: string }[] = []
  let i = 0
  while (i < lines.length) {
    const t = lines[i].trim()
    // 块级公式 $$...$$
    if (t.startsWith('$$') && t.endsWith('$$') && t.length > 4) {
      blocks.push({ type: 'math', lines: [], tex: t.slice(2, -2) })
      i++
    } else if (t.startsWith('$$')) {
      // 多行公式 $$...\n...\n$$
      const mathLines: string[] = [t.slice(2)]
      i++
      while (i < lines.length && !lines[i].trim().endsWith('$$')) {
        mathLines.push(lines[i])
        i++
      }
      if (i < lines.length) {
        mathLines.push(lines[i].trim().slice(0, -2))
        i++
      }
      blocks.push({ type: 'math', lines: [], tex: mathLines.join('\n') })
    } else if (t.startsWith('|') && t.endsWith('|')) {
      // 表格开始，收集连续的|行
      const tableLines: string[] = []
      while (i < lines.length && lines[i].trim().startsWith('|')) {
        tableLines.push(lines[i].trim())
        i++
      }
      blocks.push({ type: 'table', lines: tableLines })
    } else {
      blocks.push({ type: 'text', lines: [lines[i]] })
      i++
    }
  }

  // 渲染表格
  function renderTable(tableLines: string[]) {
    // 过滤分隔行(|---|---|)
    const dataLines = tableLines.filter(l => !/^\|[\s-:|]+\|$/.test(l))
    if (dataLines.length === 0) return null
    const rows = dataLines.map(l => l.split('|').slice(1, -1).map(c => c.trim()))
    const [header, ...body] = rows
    return (
      <table className="md-table">
        <thead>
          <tr>{header.map((c, i) => <th key={i}>{formatInline(c)}</th>)}</tr>
        </thead>
        <tbody>
          {body.map((row, ri) => (
            <tr key={ri}>{row.map((c, ci) => <td key={ci}>{formatInline(c)}</td>)}</tr>
          ))}
        </tbody>
      </table>
    )
  }

  return (
    <div className="md-body">
      {blocks.map((block, bi) => {
        if (block.type === 'math') {
          return <div key={bi} className="md-math" dangerouslySetInnerHTML={{ __html: renderMath(block.tex || '', true) }} />
        }
        if (block.type === 'table') return <React.Fragment key={bi}>{renderTable(block.lines)}</React.Fragment>
        return block.lines.map((line, li) => {
          const t = line.trim()
          if (!t) return null
          // 标题
          if (/^#{1,4}\s/.test(t)) {
            return <div key={`${bi}-${li}`} style={{ fontWeight: 700, marginTop: bi + li > 0 ? 8 : 0, marginBottom: 2 }}>{formatInline(t.replace(/^#{1,4}\s/, ''))}</div>
          }
          // 无序列表
          if (/^[-*]\s/.test(t)) {
            return <div key={`${bi}-${li}`} style={{ paddingLeft: 16, textIndent: -10 }}>{'• '}{formatInline(t.replace(/^[-*]\s/, ''))}</div>
          }
          // 有序列表
          if (/^\d+\.\s/.test(t)) {
            return <div key={`${bi}-${li}`} style={{ paddingLeft: 16, textIndent: -16 }}>{formatInline(t)}</div>
          }
          // 引用
          if (t.startsWith('> ')) {
            return <div key={`${bi}-${li}`} style={{ borderLeft: '2px solid var(--border)', paddingLeft: 8, color: 'var(--text-2)' }}>{formatInline(t.slice(2))}</div>
          }
          // 分隔线
          if (t === '---') {
            return <hr key={`${bi}-${li}`} style={{ border: 'none', borderTop: '1px solid var(--border)', margin: '8px 0' }} />
          }
          // 普通行
          return <div key={`${bi}-${li}`}>{formatInline(t)}</div>
        })
      })}
    </div>
  )
}
