import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

// 全局弹窗系统：替代原生 alert/confirm
// 用法：
//   const { toast, confirm } = useModal()
//   toast('保存成功')                    // 轻提示，3秒自动消失
//   toast('出错了', 'error')             // 错误提示，红色
//   toast('请注意', 'warning')           // 警告提示，黄色
//   const ok = await confirm('确定删除？')  // 确认框，返回 true/false

type ToastType = 'info' | 'success' | 'error' | 'warning'
interface Toast { id: number; message: string; type: ToastType }

interface ConfirmOptions {
  title?: string
  confirmText?: string
  cancelText?: string
  danger?: boolean
}

interface ConfirmState {
  visible: boolean
  options: ConfirmOptions
  resolve: ((ok: boolean) => void) | null
}

interface ModalCtx {
  toast: (message: string, type?: ToastType) => void
  confirm: (message: string, options?: ConfirmOptions) => Promise<boolean>
}

const Ctx = createContext<ModalCtx | null>(null)

export function useModal(): ModalCtx {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error('useModal must be inside ModalProvider')
  return ctx
}

export function ModalProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const [confirmState, setConfirmState] = useState<ConfirmState>({ visible: false, options: {}, resolve: null })

  const toast = useCallback((message: string, type: ToastType = 'info') => {
    const id = Date.now() + Math.random()
    setToasts(prev => [...prev, { id, message, type }])
    setTimeout(() => setToasts(prev => prev.filter(t => t.id !== id)), type === 'info' || type === 'success' ? 3000 : 5000)
  }, [])

  const confirm = useCallback((message: string, options: ConfirmOptions = {}) => {
    return new Promise<boolean>(resolve => {
      setConfirmState({ visible: true, options: { ...options, title: options.title || message }, resolve })
    })
  }, [])

  const resolveConfirm = (ok: boolean) => {
    confirmState.resolve?.(ok)
    setConfirmState({ visible: false, options: {}, resolve: null })
  }

  return (
    <Ctx.Provider value={{ toast, confirm }}>
      {children}
      {/* Toasts */}
      <div className="toast-container">
        {toasts.map(t => (
          <div key={t.id} className={`toast toast-${t.type}`}>
            {t.type === 'success' ? '\u2713 ' : t.type === 'error' ? '\u2717 ' : t.type === 'warning' ? '\u26A0 ' : ''}
            {t.message}
          </div>
        ))}
      </div>
      {/* Confirm dialog */}
      {confirmState.visible && (
        <div className="modal-overlay" onClick={() => resolveConfirm(false)}>
          <div className="modal-dialog" onClick={e => e.stopPropagation()}>
            <p className="modal-message">{confirmState.options.title}</p>
            <div className="modal-actions">
              <button className="modal-btn cancel" onClick={() => resolveConfirm(false)}>
                {confirmState.options.cancelText || '取消'}
              </button>
              <button
                className={`modal-btn ${confirmState.options.danger ? 'danger' : 'primary'}`}
                onClick={() => resolveConfirm(true)}
              >
                {confirmState.options.confirmText || '确定'}
              </button>
            </div>
          </div>
        </div>
      )}
    </Ctx.Provider>
  )
}
