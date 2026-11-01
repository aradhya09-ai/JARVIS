import type { Backend } from '../hooks/useBackend';
import { PhoneStatus } from '../components/PhoneStatus';

interface PhonePageProps {
  backend: Backend;
  onOpenSettings: () => void;
}

export function PhonePage({ backend, onOpenSettings }: PhonePageProps) {
  const phone = backend.phone;

  return (
    <div className="phone-page">
      <PhoneStatus phone={phone} onPair={onOpenSettings} />

      <section className="panel">
        <header className="panel__header">
          <div>
            <h2 className="panel__title">Capabilities</h2>
            <p className="panel__hint">What the bridge can and cannot do in this build.</p>
          </div>
        </header>
        <div className="phone-page__grid">
          <div className="kv">
            <span>Remote commands</span>
            <strong>{phone?.connected ? 'Available' : 'Needs a paired device'}</strong>
          </div>
          <div className="kv">
            <span>Phone battery</span>
            <strong>{phone?.battery !== null && phone?.battery !== undefined ? `${phone.battery}%` : 'Unavailable'}</strong>
          </div>
          <div className="kv">
            <span>Notification sync</span>
            <strong>NOT IMPLEMENTED</strong>
          </div>
          <div className="kv">
            <span>WhatsApp / SMS / calls</span>
            <strong>NOT IMPLEMENTED</strong>
          </div>
        </div>
        <p className="note">
          Pairing, authentication and remote commands work through the JSON WebSocket bridge with
          hashed tokens and per-device rate limits. Notification mirroring and messaging actions
          require Android-side permissions that are not part of this build, so they are shown as
          NOT IMPLEMENTED rather than faked.
        </p>
      </section>
    </div>
  );
}
