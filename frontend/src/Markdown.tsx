// Markdown 渲染组件：聊天气泡与报告文本的美化渲染
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'

// 判断是否是 Markdown 特殊行（标题/列表/引用/表格/代码块/分隔线）
function isSpecial(line: string): boolean {
  const s = line.trim()
  return (
    s.startsWith('#') || s.startsWith('-') || s.startsWith('*') ||
    s.startsWith('+') || s.startsWith('>') || s.startsWith('|') ||
    s.startsWith('```') || s.startsWith('---') ||
    /^\d+\.\s/.test(s) || /^\s*-\s/.test(line) || /^\s*\d+\.\s/.test(line)
  )
}

export default function Markdown({ text }: { text: string }) {
  // 1. 压缩 3+ 连续换行
  let cleaned = text.replace(/\n{3,}/g, '\n\n')
  // 2. 连续的普通文本段落（非列表/标题/引用/表格）合并成一个段落（\n\n -> \n），
  //    配合 breaks:true 让单换行渲染成 <br>，消除段落间距过大问题
  const blocks = cleaned.split(/\n\n+/)
  const merged: string[] = []
  let buf: string[] = []
  const flush = () => { if (buf.length) { merged.push(buf.join('\n')); buf = [] } }
  for (const block of blocks) {
    // 块内含多行且任意一行是特殊语法 -> 不合并
    const lines = block.split('\n')
    const hasSpecial = lines.some(l => isSpecial(l))
    if (hasSpecial) { flush(); merged.push(block) }
    else { buf.push(block) }
  }
  flush()
  cleaned = merged.join('\n\n')

  return (
    <div className="md-body">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{cleaned}</ReactMarkdown>
    </div>
  )
}
