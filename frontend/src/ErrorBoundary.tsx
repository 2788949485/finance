import { Component, type ReactNode, type ErrorInfo } from 'react'

// 全局错误边界：任何子组件渲染时抛异常不会白屏，而是显示友好错误信息 + 重试按钮
interface Props {
  children: ReactNode
}
interface State {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ErrorBoundary caught:', error, info.componentStack)
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null })
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="error-boundary">
          <div className="error-boundary-card">
            <h3>页面出了点问题</h3>
            <p className="error-boundary-msg">{this.state.error?.message || '未知错误'}</p>
            <button className="btn-primary" onClick={this.handleReset}>重试</button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}
