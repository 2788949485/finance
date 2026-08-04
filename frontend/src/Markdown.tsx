// Markdown 渲染组件：聊天气泡与报告文本的美化渲染
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

export default function Markdown({ text }: { text: string }) {
  // 压缩多余空行（3+连续换行 -> 2换行），避免 react-markdown 渲染出过大段落间距
  const cleaned = text.replace(/\n{3,}/g, '\n\n')
  return (
    <div className="md-body">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{cleaned}</ReactMarkdown>
    </div>
  )
}
