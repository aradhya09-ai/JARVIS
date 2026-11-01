import { JarvisHud } from '../components/JarvisHud';
import { HomeMenu, type HomeSection } from '../components/HomeMenu';

import type { Backend } from '../hooks/useBackend';
import type { Voice } from '../hooks/useVoice';
import type { ReactorState, TabId } from '../types';

interface DashboardPageProps {
  backend: Backend;
  voice: Voice;
  reactorState: ReactorState;
  reducedMotion: boolean;
  sections: HomeSection[];
  onNavigate: (id: TabId) => void;
  wakeEnabled: boolean;
  onToggleWake: () => void;
  onRemember: () => void;
}

export function DashboardPage({
  backend,
  voice,
  reactorState,
  reducedMotion,
  sections,
  onNavigate,
  wakeEnabled,
  onToggleWake,
  onRemember,
}: DashboardPageProps) {
  const online = backend.connection === 'online';
  const wakePhrase =
    typeof backend.settings['voice.wake_word'] === 'string'
      ? backend.settings['voice.wake_word']
      : 'hey jarvis';

  return (
    <div className="home-stage">
      <JarvisHud
        state={reactorState}
        level={voice.level}
        reducedMotion={reducedMotion}
        onClick={() => {
          if (!online) return;
          voice.toggle();
        }}
      />
      <HomeMenu
        sections={sections}
        online={online}
        wakeActive={voice.wakeActive}
        wakeEnabled={wakeEnabled}
        wakePhrase={wakePhrase}
        onToggleWake={onToggleWake}
        listening={voice.listening}
        micSupported={voice.supported}
        onToggleMic={voice.toggle}
        onNavigate={onNavigate}
        onRemember={onRemember}
      />
    </div>
  );
}
