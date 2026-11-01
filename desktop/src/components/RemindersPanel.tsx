import { useCallback, useEffect, useState } from 'react';

import { api } from '../services/api';
import type { ReminderRecord } from '../types';
import { IconBell, IconRefresh } from '../components/Icons';

interface RemindersPanelProps {
  online: boolean;
  reloadToken?: number;
}

const EMPTY = { text: '', inMinutes: '', at: '', speak: false };

function when(reminder: ReminderRecord, now: number): string {
  const target = new Date(reminder.due_at).getTime();
  if (Number.isNaN(target)) return reminder.due_at;
  const delta = Math.round((target - now) / 60000);
  if (reminder.fired) return 'delivered';
  if (reminder.cancelled) return 'cancelled';
  if (delta <= 0) return 'overdue';
  if (delta < 60) return `in ${delta} min`;
  if (delta < 60 * 24) return `in ${Math.round(delta / 60)} h`;
  return new Date(reminder.due_at).toLocaleString();
}

export function RemindersPanel({ online, reloadToken = 0 }: RemindersPanelProps) {
  const [reminders, setReminders] = useState<ReminderRecord[]>([]);
  const [note, setNote] = useState('');
  const [showFinished, setShowFinished] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await api.reminders(showFinished);
      setReminders(data.reminders);
      setNote(data.note);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [showFinished]);

  useEffect(() => {
    if (online) void load();
  }, [online, load, reloadToken]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000);
    return () => window.clearInterval(timer);
  }, []);

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      const minutes = form.inMinutes.trim() === '' ? undefined : Number(form.inMinutes);
      await api.addReminder({
        text: form.text.trim(),
        minutes,
        due_at: form.at ? new Date(form.at).toISOString() : undefined,
        speak: form.speak,
      });
      setForm(EMPTY);
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async (id: number) => {
    await api.cancelReminder(id).catch(() => setError('That reminder could not be cancelled.'));
    void load();
  };

  const test = async (id: number) => {
    await api
      .testReminder(id)
      .catch(() => setError('The test delivery failed — the reminder itself is unchanged.'));
  };

  const scheduled = form.at.trim() !== '' || Number(form.inMinutes) > 0;
  const canSave = form.text.trim().length > 0 && scheduled && !busy;

  return (
    <section className="panel" aria-label="Reminders">
      <header className="panel__header">
        <div>
          <h2 className="panel__title">
            <IconBell size={15} /> Reminders
          </h2>
          <p className="panel__hint">{note || 'Reminders you create here or by voice.'}</p>
        </div>
        <div className="field__row">
          <button
            type="button"
            className={`chip chip--button${showFinished ? ' is-on' : ''}`}
            onClick={() => setShowFinished((value) => !value)}
            title="Include delivered and cancelled reminders"
          >
            {showFinished ? 'Hiding none' : 'Show finished'}
          </button>
          <button
            type="button"
            className="icon-button"
            onClick={() => void load()}
            title="Refresh reminders"
            disabled={!online}
          >
            <IconRefresh size={15} />
          </button>
        </div>
      </header>

      {error && <p className="inline-alert">{error}</p>}

      <div className="form-grid">
        <label className="field">
          <span>What to remember</span>
          <input
            type="text"
            value={form.text}
            maxLength={200}
            placeholder="Revise normalization"
            onChange={(event) => setForm({ ...form, text: event.target.value })}
            disabled={!online}
          />
        </label>
        <label className="field">
          <span>In N minutes</span>
          <input
            type="number"
            min={1}
            max={60 * 24 * 365}
            value={form.inMinutes}
            placeholder="20"
            onChange={(event) => setForm({ ...form, inMinutes: event.target.value, at: '' })}
            disabled={!online || form.at !== ''}
          />
        </label>
        <label className="field">
          <span>Or at an exact time</span>
          <input
            type="datetime-local"
            value={form.at}
            onChange={(event) => setForm({ ...form, at: event.target.value, inMinutes: '' })}
            disabled={!online || form.inMinutes !== ''}
          />
          <small>A past time is delivered as overdue, not silently dropped.</small>
        </label>
        <label className="field field--check">
          <input
            type="checkbox"
            checked={form.speak}
            onChange={(event) => setForm({ ...form, speak: event.target.checked })}
            disabled={!online}
          />
          <span>Speak it when it fires</span>
        </label>
        <div className="field__row">
          <button
            type="button"
            className="button button--accent"
            onClick={() => void create()}
            disabled={!online || !canSave}
          >
            Set reminder
          </button>
          {!online && <small className="empty-note">Backend offline — nothing can be scheduled.</small>}
        </div>
      </div>

      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>Reminder</th>
              <th>Due</th>
              <th>State</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {reminders.map((reminder) => (
              <tr key={reminder.id}>
                <td>{reminder.text}</td>
                <td className="mono">{when(reminder, now)}</td>
                <td>
                  <span
                    className={`chip chip--${
                      reminder.cancelled
                        ? 'cancelled'
                        : reminder.fired
                          ? 'completed'
                          : 'pending'
                    }`}
                  >
                    {reminder.cancelled ? 'cancelled' : reminder.fired ? 'delivered' : 'waiting'}
                  </span>
                  {reminder.speak && <span className="chip">spoken</span>}
                </td>
                <td>
                  <div className="field__row">
                    <button
                      type="button"
                      className="button button--tiny button--ghost"
                      onClick={() => void test(reminder.id)}
                      disabled={!online || reminder.cancelled}
                      title="Deliver it right now to check the alert and speech path"
                    >
                      Test now
                    </button>
                    <button
                      type="button"
                      className="button button--tiny button--danger"
                      onClick={() => void cancel(reminder.id)}
                      disabled={!online || reminder.cancelled || reminder.fired}
                    >
                      Cancel
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {reminders.length === 0 && (
              <tr>
                <td colSpan={4} className="empty-note">
                  {online
                    ? 'No reminders stored. Voice requests like “remind me to stretch in 20 minutes” land here too.'
                    : 'Backend offline.'}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}
