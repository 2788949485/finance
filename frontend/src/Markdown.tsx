// Markdown 渲染组件：聊天气泡与报告文本的美化渲染
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'

export default function Markdown({ text }: { text: string }) {
  // 1. 压缩所有连续空行为单换行（消除段落间距过大问题）
  let cleaned = text.replace(/\n{2,}/g, '\n')
  // 2. 保留列表/标题前的换行（这些需要单独成块）
  cleaned = cleaned.replace(/([^\n])\n(#{1,4}\s|>\s|---|\|)/g, '$1\n\n$2')
  cleaned = cleaned.replace(/([^\n])\n(- \D|^\* \D|\d+\. )/gm, '$1\n\n$2')

  return (
    <div className="md-body">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{cleaned}</ReactMarkdown>
    </div>
  )
}
