import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  fallback?: ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  render() {
    if (this.state.error) {
      return (
        this.props.fallback ?? (
          <div className="p-6 text-center">
            <h2 className="mb-2 text-error">组件出错</h2>
            <p className="mb-4 text-[13px] text-text-muted">{this.state.error.message}</p>
            <button
              className="btn btn-primary btn-sm"
              onClick={() => this.setState({ error: null })}
            >
              重试
            </button>
          </div>
        )
      );
    }
    return this.props.children;
  }
}
