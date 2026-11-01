import {
  IconBolt,
  IconCamera,
  IconFolder,
  IconLock,
  IconMonitor,
  IconPlay,
  IconPresentation,
  IconSpark,
} from './Icons';

interface QuickActionsProps {
  onRun: (text: string) => void;
  disabled?: boolean;
}

// Every command here was checked against the planner's rule table, so the card
// runs the tool it names instead of falling through to a freeform model reply.
const ACTIONS = [
  { label: 'Open VS Code', command: 'Open VS Code', icon: <IconSpark size={15} /> },
  { label: 'Open PowerPoint', command: 'Open PowerPoint', icon: <IconPresentation size={15} /> },
  { label: 'File Explorer', command: 'Open File Explorer', icon: <IconFolder size={15} /> },
  { label: 'Google Chrome', command: 'Open Chrome', icon: <IconMonitor size={15} /> },
  { label: 'Take screenshot', command: 'Take a screenshot', icon: <IconCamera size={15} /> },
  { label: 'Lock PC', command: 'Lock my computer', icon: <IconLock size={15} /> },
  { label: 'Open Downloads', command: 'Open my Downloads folder', icon: <IconFolder size={15} /> },
  { label: 'Open Documents', command: 'Open my Documents folder', icon: <IconFolder size={15} /> },
  { label: 'System status', command: 'What is my CPU and memory usage', icon: <IconBolt size={15} /> },
];

export function QuickActions({ onRun, disabled }: QuickActionsProps) {
  return (
    <section className="panel quick" aria-label="Quick actions">
      <header className="panel__header">
        <div>
          <h2 className="panel__title">Quick actions</h2>
          <p className="panel__hint">Each card sends a real command to the backend.</p>
        </div>
      </header>
      <div className="quick__grid">
        {ACTIONS.map((action) => (
          <button
            key={action.label}
            type="button"
            className="quick__button"
            onClick={() => onRun(action.command)}
            disabled={disabled}
            title={action.command}
          >
            <span className="quick__icon">{action.icon}</span>
            {action.label}
            <IconPlay size={11} />
          </button>
        ))}
      </div>
      <p className="quick__note">
        Anything with moderate or high risk pauses for confirmation before running. Launching an
        app that is not installed on this machine reports that instead of pretending it opened.
      </p>
    </section>
  );
}
