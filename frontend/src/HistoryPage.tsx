import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { AnalysisResult, HistoryItem } from './types'
import { ReportView } from './AnalyzePage'

/* ---------------- 历史记录 ---------------- */

function HistoryPane({ onPick }: { onPick: () => void }) {
  const [items, setItems] = useState<HistoryItem[]>([])
  const [error, setError] = useState('')
  const [detail, setDetail] = useState<AnalysisResult | null>(null)
  const [loadingId, setLoadingId] = useState<number | null>(null)

  const load = useCallback(async () => {
    try {
      setItems(await api.getHistory())
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    }
  }, [])

  useEffect(() => { load() }, [load])

  const viewDetail = async (id: number) => {
    setLoadingId(id)
    try {
      const r = await api.getAnalysis(id)
      if (r.result) setDetail(r.result)
    } catch { /* skip */ }
    finally { setLoadingId(null) }
  }

  // 详情视图
  if (detail) {
    return (
      <div className="pane">
        <button className="ghost back-to-list" onClick={() => setDetail(null)} style={{ marginBottom: 12 }}>返回列表</button>
        <ReportView result={detail} />
      </div>
    )
  }

  return (
    <div className="pane">
      {error && <div className="error-box">{error}</div>}
      {items.length === 0 ? (
        <div className="empty">暂无分析记录，去"投研分析"页跑一次</div>
      ) : (
        <table className="history-table">
          <thead>
            <tr><th>ID</th><th>代码</th><th>时间</th><th>状态</th><th></th></tr>
          </thead>
          <tbody>
            {items.map((it) => (
              <tr key={it.id}>
                <td>{it.id}</td>
                <td>{it.ticker}</td>
                <td>{it.created_at}</td>
                <td>{it.status}</td>
                <td>
                  <button className="ghost" onClick={() => viewDetail(it.id)} disabled={loadingId === it.id}>
                    {loadingId === it.id ? '加载...' : '查看'}
                  </button>
                  <button onClick={onPick}>再分析</button>
                  <button className="ghost hist-del-btn" onClick={async () => {
                    if (!confirm(`确定删除记录 #${it.id}？`)) return
                    try { await api.deleteHistory(it.id); setItems(prev => prev.filter(x => x.id !== it.id)) } catch {}
                  }}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
    )}
    </div>
  )
}

export default HistoryPane
