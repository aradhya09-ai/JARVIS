import { useEffect, useState } from 'react';

import type { Backend } from '../hooks/useBackend';
import type { Voice } from '../hooks/useVoice';
import { PermissionBanner } from '../components/PermissionBanner';
import { SettingsPanel } from '../components/SettingsPanel';
import { SiteRegistryPanel } from '../components/SiteRegistryPanel';

interface SettingsPageProps {
  backend: Backend;
  voice: Voice;
}

export function SettingsPage({ backend, voice }: SettingsPageProps) {
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  return (
    <div className="settings-page">
      {notice && (
        <PermissionBanner tone="info" title="Settings" onDismiss={() => setNotice(null)}>
          {notice}
        </PermissionBanner>
      )}
      <SettingsPanel
        settings={backend.settings}
        health={backend.health}
        online={backend.connection === 'online'}
        voices={voice.voices}
        testVoice={(sample) => void voice.testVoice(sample)}
        update={backend.updateSettings}
        onClearConversation={() => void backend.clearConversation()}
        onNotice={setNotice}
      />
      <SiteRegistryPanel onNotice={setNotice} />
    </div>
  );
}
