import type { Task } from '../types';
import { IconChevron } from './Icons';

interface RecentCommandsProps {
  tasks: Task[];
  onViewAll: () => void;
}

const STATUS_LABEL: Record<string, string> = {
  pending: 'Queued',
  running: 'Running',
  awaiting_confirmation: 'Confirm',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

function relativeTime(raw: string): string {
  const then = new Date(raw).getTime();
  if (Number.isNaN(then)) return '';
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return new Date(raw).toLocaleDateString();
}

export function RecentCommands({ tasks, onViewAll }: RecentCommandsProps) {
  const recent = tasks.slice(0, 5);

  return (
    <section className="panel recent" aria-label="Recent commands">
      <header className="panel__header">
        <div>
          <h2 className="panel__title">Recent commands</h2>
          <p className="panel__hint">Only real executed commands appear here.</p>
        </div>
        <button type="button" className="button button--tiny button--ghost" onClick={onViewAll}>
          View all <IconChevron size={11} />
        </button>
      </header>
      <div className="recent__list">
        {recent.length === 0 && <p className="empty-note">No commands yet.</p>}
        {recent.map((task) => (
          <div key={task.id} className="recent__item">
            <div className="recent__text">
              <p>{task.text}</p>
              <span>{relativeTime(task.created_at)}</span>
            </div>
            <span className={`chip chip--${task.status}`}>{STATUS_LABEL[task.status]}</span>
          </div>
        ))}
      </div>
    </section>
  );
}
