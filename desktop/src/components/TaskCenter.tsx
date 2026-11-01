import { useMemo, useState } from 'react';

import type { Task, TaskStatus } from '../types';
import { IconStop } from './Icons';

interface TaskCenterProps {
  tasks: Task[];
  onCancel: (id: string) => void;
  compact?: boolean;
  limit?: number;
}

const STATUS_LABEL: Record<TaskStatus, string> = {
  pending: 'Queued',
  running: 'Running',
  awaiting_confirmation: 'Awaiting confirmation',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

const ACTIVE: TaskStatus[] = ['pending', 'running', 'awaiting_confirmation'];

function relativeTime(raw: string): string {
  const then = new Date(raw).getTime();
  if (Number.isNaN(then)) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Date(raw).toLocaleDateString();
}

export function TaskCenter({ tasks, onCancel, compact = false, limit }: TaskCenterProps) {
  const [filter, setFilter] = useState<'all' | 'active' | 'done'>('all');
  const filtered = useMemo(() => {
    const list =
      filter === 'active'
        ? tasks.filter((task) => ACTIVE.includes(task.status))
        : filter === 'done'
          ? tasks.filter((task) => !ACTIVE.includes(task.status))
          : tasks;
    return limit ? list.slice(0, limit) : list;
  }, [tasks, filter, limit]);

  return (
    <section className={`panel tasks${compact ? ' tasks--compact' : ''}`} aria-label="Task center">
      <header className="panel__header">
        <div>
          <h2 className="panel__title">Task center</h2>
          <p className="panel__hint">
            {tasks.filter((task) => ACTIVE.includes(task.status)).length} active · {tasks.length}{' '}
            tracked
          </p>
        </div>
        {!compact && (
          <div className="segmented" role="tablist" aria-label="Filter tasks">
            {(['all', 'active', 'done'] as const).map((value) => (
              <button
                key={value}
                type="button"
                role="tab"
                aria-selected={filter === value}
                className={filter === value ? 'is-active' : ''}
                onClick={() => setFilter(value)}
              >
                {value}
              </button>
            ))}
          </div>
        )}
      </header>

      <div className="task-list">
        {filtered.length === 0 && (
          <p className="empty-note">
            {filter === 'active' ? 'No tasks are running.' : 'No tasks yet — issue a command.'}
          </p>
        )}
        {filtered.map((task) => (
          <article key={task.id} className={`task-item task-item--${task.status}`}>
            <div className="task-item__head">
              <span className={`chip chip--${task.status}`}>{STATUS_LABEL[task.status]}</span>
              <span className="task-item__tool">{task.tool ?? 'reply'}</span>
              <time className="task-item__time" dateTime={task.created_at}>
                {relativeTime(task.created_at)}
              </time>
              {ACTIVE.includes(task.status) && (
                <button
                  type="button"
                  className="icon-button icon-button--danger"
                  onClick={() => onCancel(task.id)}
                  title="Cancel task"
                  aria-label="Cancel task"
                >
                  <IconStop size={13} />
                </button>
              )}
            </div>
            <p className="task-item__text">{task.text}</p>
            {task.error && <p className="task-item__error">{task.error}</p>}
            {!compact && task.status === 'completed' && task.result && (
              <details className="task-item__details">
                <summary>View result payload</summary>
                <pre>{JSON.stringify(task.result, null, 2)}</pre>
              </details>
            )}
            {!compact && (
              <div className="task-item__meta">
                <span>{task.planner ?? 'rules'}</span>
                {task.risk && <span className={`risk risk--${task.risk}`}>{task.risk} risk</span>}
                {task.args && Object.keys(task.args).length > 0 && (
                  <span className="task-item__args">
                    {Object.entries(task.args)
                      .slice(0, 3)
                      .map(([key, value]) => `${key}=${String(value).slice(0, 40)}`)
                      .join(' · ')}
                  </span>
                )}
              </div>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
