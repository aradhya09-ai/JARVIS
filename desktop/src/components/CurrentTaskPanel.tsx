import type { Health, Task } from '../types';
import { IconCpu, IconPhone, IconSpark, IconStop, IconTasks } from './Icons';

interface CurrentTaskPanelProps {
  activeTask: Task | null;
  lastTask: Task | null;
  health: Health | null;
  online: boolean;
  onCancel: (id: string) => void;
}

const STATUS_LABEL: Record<string, string> = {
  pending: 'Queued',
  running: 'Running',
  awaiting_confirmation: 'Awaiting confirmation',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
};

export function CurrentTaskPanel({ activeTask, lastTask, health, online, onCancel }: CurrentTaskPanelProps) {
  const subsystem = [
    { icon: <IconSpark size={13} />, label: 'AI model', ok: Boolean(health?.active_provider), value: health?.active_provider ?? 'Rules only' },
    { icon: <IconTasks size={13} />, label: 'Desktop tools', ok: (health?.tools ?? 0) > 0, value: `${health?.tools ?? 0} registered` },
    { icon: <IconCpu size={13} />, label: 'Backend', ok: online, value: online ? 'Online' : 'Offline' },
    { icon: <IconPhone size={13} />, label: 'Phone link', ok: Boolean(health?.phone.connected), value: health?.phone.connected ? (health.phone.name ?? 'Connected') : 'Not paired' },
  ];

  return (
    <section className="panel current-task" aria-label="Current task">
      <header className="panel__header">
        <div>
          <h2 className="panel__title">Current task</h2>
          <p className="panel__hint">
            {activeTask ? STATUS_LABEL[activeTask.status] : 'Ready for your command…'}
          </p>
        </div>
        {activeTask && ['pending', 'running', 'awaiting_confirmation'].includes(activeTask.status) && (
          <button
            type="button"
            className="button button--tiny button--danger"
            onClick={() => onCancel(activeTask.id)}
          >
            <IconStop size={12} /> Cancel
          </button>
        )}
      </header>

      {activeTask ? (
        <div className="current-task__body">
          <span className={`chip chip--${activeTask.status}`}>{STATUS_LABEL[activeTask.status]}</span>
          <p className="current-task__text">{activeTask.text}</p>
          {activeTask.status === 'running' && <div className="meter meter--accent"><span style={{ width: '100%' }} /></div>}
        </div>
      ) : lastTask ? (
        <div className="current-task__body">
          <span className={`chip chip--${lastTask.status}`}>{STATUS_LABEL[lastTask.status]}</span>
          <p className="current-task__text">{lastTask.text}</p>
        </div>
      ) : (
        <p className="empty-note">Nothing has run yet — issue a command to get started.</p>
      )}

      <ul className="subsystem-list">
        {subsystem.map((item) => (
          <li key={item.label}>
            <span className={`status-dot status-dot--${item.ok ? 'good' : 'idle'}`} />
            {item.icon}
            <span className="subsystem-list__label">{item.label}</span>
            <strong>{item.value}</strong>
          </li>
        ))}
      </ul>
    </section>
  );
}
