import type { PhoneStatus as PhoneStatusType } from '../types';
import { IconBattery, IconPhone, IconPlug } from './Icons';

interface PhoneStatusProps {
  phone: PhoneStatusType | null;
  onPair: () => void;
  compact?: boolean;
}

export function PhoneStatus({ phone, onPair, compact = false }: PhoneStatusProps) {
  const connected = Boolean(phone?.connected);
  return (
    <section className={`panel phone${compact ? ' phone--compact' : ''}`} aria-label="Phone status">
      <header className="panel__header">
        <div>
          <h2 className="panel__title">
            <IconPhone size={15} /> Phone bridge
          </h2>
          <p className="panel__hint">
            {connected ? `${phone?.name ?? 'Android device'} linked` : 'No Android device connected'}
          </p>
        </div>
        <span className={`status-dot status-dot--${connected ? 'good' : 'idle'}`} />
      </header>

      <div className="phone__body">
        <div className="kv">
          <span>Status</span>
          <strong>{connected ? 'Online' : 'Offline'}</strong>
        </div>
        <div className="kv">
          <span>Battery</span>
          <strong>
            {phone?.battery !== null && phone?.battery !== undefined ? (
              <>
                <IconBattery size={13} /> {phone.battery}%
                {phone.charging ? (
                  <>
                    {' '}
                    <IconPlug size={12} /> charging
                  </>
                ) : null}
              </>
            ) : (
              'Unavailable'
            )}
          </strong>
        </div>
        {!compact && (
          <>
            <div className="kv">
              <span>Desktop ID</span>
              <code title={phone?.desktop_id}>{phone?.desktop_id?.slice(0, 8) ?? '—'}…</code>
            </div>
            <div className="kv">
              <span>Listening</span>
              <code>
                {phone?.bound_host}:{phone?.port}
                {phone?.bound_host === '127.0.0.1' ? ' (loopback)' : ''}
              </code>
            </div>
          </>
        )}
      </div>

      <p className="phone__note">
        {connected
          ? 'Phone automation requests are authenticated per device and rate limited.'
          : 'Start the Android companion, then pair it with a one-time code. Remote control needs the phone app in the foreground.'}
      </p>

      <button type="button" className="button button--ghost button--block" onClick={onPair}>
        {connected ? 'Manage phone pairing' : 'Pair a phone'}
      </button>
    </section>
  );
}
