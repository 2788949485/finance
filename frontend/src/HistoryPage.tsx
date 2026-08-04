import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import type { HistoryItem } from './types'

/* ---------------- 历史记录 ---------------- */

function HistoryPane({ onPick }: { onPick: () => void }) {
  const [items, setItems] = useState<HistoryItem[]>([])
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      setItems(await api.getHistory())
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    }
  }, [])

  useEffect(() => { load() }, [load])

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
                  <button onClick={onPick}>再分析</button>
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
