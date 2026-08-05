// Markdown 渲染组件：聊天气泡与报告文本的美化渲染
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkBreaks from 'remark-breaks'

export default function Markdown({ text }: { text: string }) {
  // 压缩所有空行：连续换行统一为单换行，消除段落间距
  let cleaned = text
    .replace(/\r\n/g, '\n')        // 统一换行符
    .replace(/\n{2,}/g, '\n')      // 多个换行压成1个
    .replace(/\n(#{1,4}\s)/g, '\n\n$1')  // 标题前保留空行(需要单独成块)
    .replace(/\n(>\s|---\n)/g, '\n\n$1')  // 引用/分隔线前保留
    .replace(/\n(\|)/g, '\n\n$1')   // 表格前保留

  return (
    <div className="md-body">
      <ReactMarkdown remarkPlugins={[remarkGfm, remarkBreaks]}>{cleaned}</ReactMarkdown>
    </div>
  )
}
