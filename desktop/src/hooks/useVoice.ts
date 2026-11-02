import { useCallback, useEffect, useRef, useState } from 'react';

import { matchWakeWord, stripWakeWord } from '../lib/wakeWord';
import { api } from '../services/api';

export interface VoiceOptions {
  language: string;
  wakePhrase: string;
  wakeEnabled: boolean;
  wakeGreeting: string;
  ttsEnabled: boolean;
  voiceName: string;
  sapiVoice: string;
  autoListen: boolean;
  onCommand: (text: string) => void;
  onWake: () => void;
  onInterrupted?: () => void;
}

export type SpeechRoute = 'backend' | 'browser' | 'system';

export interface SpeechStatus {
  route: SpeechRoute;
  message: string;
  at: number;
}

const ERROR_TEXT: Record<string, string> = {
  'not-allowed': 'Microphone permission was denied. Allow the microphone in the browser and try again.',
  'service-not-allowed': 'Speech recognition was blocked by the browser.',
  'no-speech': 'No speech was detected. Try again and speak a little closer to the microphone.',
  network: 'Speech recognition needs a network connection in this browser.',
  'audio-capture': 'No microphone was found.',
  'language-not-supported': 'This browser cannot recognize speech in the selected language.',
};

// Errors the recogniser will not recover from by itself. The wake loop has to
// stop and say why: a listener that is not running must never be reported as
// one that is. Transient ones (no-speech, aborted, a network blip) are retried.
const FATAL_WAKE_ERRORS = new Set([
  'not-allowed',
  'service-not-allowed',
  'audio-capture',
  'language-not-supported',
]);

/**
 * Chrome and Edge refuse to make any sound - AudioContext or speechSynthesis -
 * until the window has taken a real click or keypress. `navigator.userActivation`
 * answers that question directly; older builds fall back to the flag this hook
 * sets on its own listeners.
 */
function tabMayBeBlocked(activatedByListener: boolean): boolean {
  const activation = (navigator as { userActivation?: { hasBeenActive?: boolean } })
    .userActivation;
  if (typeof activation?.hasBeenActive === 'boolean') return !activation.hasBeenActive;
  return !activatedByListener;
}

const INTERRUPTION_RE =
  /(?:^|\b)(?:stop(?:\s+(?:it|talking|now|jarvis))?|that'?s enough|that is enough|be quiet|shut up|cancel(?:\s+that)?|hold on|pause(?:\s+it)?|listen to me|let me speak|ruko(?:\s+jao)?|ruk\s+jao|bas\s+kar\s*do|chup(?:\s+(?:ho\s*jao|rah\s*o))?|meri baat suno)(?:\b|[,.!?])/i;

const DEVANAGARI = /[\u0900-\u097F]/;

// Chrome's speechSynthesis silently stops long utterances after roughly 15
// seconds, so replies are queued in sentence-sized pieces instead of one blob.
const MAX_CHUNK = 200;

// Chrome closes a recognition segment the moment it stops hearing, so a breath
// inside a sentence arrives as a "final" result. Committing at that first
// segment cut users off mid-command; a turn stays open until the room has
// genuinely been quiet.
const TURN_QUIET_MS = 750;
// Interims are the engine still guessing, so they get a longer hold than a real
// final before the turn is considered finished.
const INTERIM_HOLD_MS = 1200;
const TURN_WINDOW_MS = 9000;

// Stray single-word finals from room noise must not become commands.
const NOISE_WORDS = new Set([
  'a', 'an', 'the', 'ok', 'okay', 'oh', 'hmm', 'mm', 'um', 'uh', 'huh', 'so',
  'yeah', 'yes', 'no', 'hi', 'hello', 'hey', 'thanks', 'thank', 'you', 'please',
  'sorry', 'jarvis',
]);

function isFalseEnding(text: string): boolean {
  const words = wordsOf(text);
  if (words.length === 0) return true;
  return words.length === 1 && NOISE_WORDS.has(words[0]);
}

function stripInterruptionPhrase(text: string): string {
  return text.replace(INTERRUPTION_RE, '').replace(/^[\s,.:;!-]+/, '').trim();
}

function wordsOf(text: string): string[] {
  return text.toLowerCase().replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter(Boolean);
}

function chunkForSpeech(text: string): string[] {
  const sentences = text.split(/(?<=[.!?।])\s+|\n+/).map((s) => s.trim()).filter(Boolean);
  const chunks: string[] = [];
  let buffer = '';
  for (const sentence of sentences) {
    if ((buffer + ' ' + sentence).trim().length > MAX_CHUNK && buffer) {
      chunks.push(buffer.trim());
      buffer = sentence;
    } else {
      buffer = (buffer + ' ' + sentence).trim();
    }
  }
  if (buffer) chunks.push(buffer.trim());
  if (chunks.length === 0) return [text];
  // A single very long sentence still needs splitting.
  return chunks.flatMap((chunk) =>
    chunk.length <= MAX_CHUNK
      ? [chunk]
      : (chunk.match(new RegExp(`.{1,${MAX_CHUNK}}(\\s|$)`, 'g')) ?? [chunk]).map((c) => c.trim()),
  );
}

function getAudioContextCtor(): typeof AudioContext | null {
  return window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext ?? null;
}

function bufferIsSilent(buffer: AudioBuffer): boolean {
  if (!buffer.duration || buffer.duration < 0.05) return true;
  const data = buffer.getChannelData(0);
  if (data.length === 0) return true;
  const step = Math.max(1, Math.floor(data.length / 4000));
  for (let i = 0; i < data.length; i += step) {
    if (Math.abs(data[i]) > 0.002) return false;
  }
  return true;
}

/**
 * Choose a browser voice without silently picking an unrelated one.
 * Returns null when the selected language is Hindi and no Hindi voice exists:
 * speaking Devanagari through an English voice with lang="hi-IN" makes Chrome
 * emit nothing at all, so the caller must report the gap instead.
 */
function pickBrowserVoice(
  voices: SpeechSynthesisVoice[],
  wantedName: string,
  language: string,
): { voice: SpeechSynthesisVoice | null; fellBack: boolean; reason?: string } {
  if (voices.length === 0) return { voice: null, fellBack: false, reason: 'no-voices-loaded' };
  if (wantedName) {
    const exact = voices.find((v) => v.name === wantedName);
    if (exact) return { voice: exact, fellBack: false };
  }
  const base = (language || 'en-IN').toLowerCase().split('-')[0];
  const sameLanguage = voices.filter((v) => v.lang.toLowerCase().split('-')[0] === base);
  if (sameLanguage.length > 0) return { voice: sameLanguage[0], fellBack: Boolean(wantedName) };
  if (base === 'hi') {
    return { voice: null, fellBack: false, reason: 'no-hindi-voice' };
  }
  const english = voices.find((v) => v.lang.toLowerCase().startsWith('en'));
  return { voice: english ?? voices[0], fellBack: true };
}

export function useVoice(options: VoiceOptions) {
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const [listening, setListening] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  // The line JARVIS is saying out loud, shown as a subtitle on the home screen.
  const [subtitle, setSubtitle] = useState('');
  const [wakeActive, setWakeActive] = useState(false);
  const [armed, setArmedState] = useState(false);
  const [level, setLevel] = useState(0);
  const [transcript, setTranscript] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);
  const [speechStatus, setSpeechStatus] = useState<SpeechStatus | null>(null);
  // Why the wake listener stopped, when it stopped on its own. The UI shows this
  // instead of "listening", because a dead listener is not a listening one.
  const [wakeFault, setWakeFault] = useState<string | null>(null);
  const [micPermission, setMicPermission] = useState<
    'granted' | 'denied' | 'prompt' | 'unknown'
  >('unknown');
  const [supported] = useState(
    () =>
      typeof window !== 'undefined' &&
      Boolean(window.SpeechRecognition || window.webkitSpeechRecognition),
  );

  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const bargeRef = useRef<SpeechRecognition | null>(null);
  const modeRef = useRef<'command' | 'wake' | null>(null);
  const armedRef = useRef(false);
  const stoppedRef = useRef(false);
  const listeningRef = useRef(false);
  const speakingRef = useRef(false);
  const wakeActiveRef = useRef(false);
  const streamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const micAnalyserRef = useRef<AnalyserNode | null>(null);
  const rafRef = useRef<number | null>(null);
  const smoothedRef = useRef(0);
  const ttsSourceRef = useRef<AudioBufferSourceNode | null>(null);
  const afterSpeechRef = useRef<(() => void) | null>(null);
  const startBargeInRef = useRef<() => void>(() => undefined);
  const stopBargeInRef = useRef<() => void>(() => undefined);
  const startRef = useRef<() => Promise<void>>(async () => undefined);
  const speakRef = useRef<(text: string) => Promise<void>>(async () => undefined);
  // Every speech request takes a number. Late backend replies are dropped when
  // their number is no longer current, which is what stops a cancelled
  // response from starting to talk again.
  const speechEpochRef = useRef(0);
  const spokenTextRef = useRef('');
  // The last wake that was answered, so the recogniser handing the same utterance
  // back twice does not greet twice. Keyed by the words, not by the clock: a flat
  // time window also swallowed a genuine second call that came while JARVIS was
  // still reading the first reply.
  const lastWakeRef = useRef<{ words: string; at: number }>({ words: '', at: 0 });
  const voicesReadyRef = useRef(false);
  // The command being spoken right now, kept open across pauses so one
  // sentence split into three recognition finals still arrives as one command.
  const turnRef = useRef<{
    text: string;
    timer: number | null;
    deadline: number;
    // The live guess the engine keeps revising while he is still talking, and
    // whether a real final has landed yet. Interims only get used to end a turn
    // when no final ever arrives.
    interim: string;
    hasFinal: boolean;
  }>({
    text: '',
    timer: null,
    deadline: 0,
    interim: '',
    hasFinal: false,
  });
  const commandTurnRef = useRef<number | null>(null);
  // The last command actually handed to the backend. Chrome sometimes delivers
  // the same sentence as an interim and then again as a final, so a wake match
  // that replays the command just run is dropped.
  const lastDispatchRef = useRef<{ text: string; at: number }>({ text: '', at: 0 });
  // Set by the first real click or keypress, so playback can be unlocked on
  // builds that do not expose navigator.userActivation.
  const activatedRef = useRef(false);
  // Audio played by the backend is not the tab's to stop: ending it needs an
  // explicit purge plus a timer for the clip length the backend reported.
  const systemStopRef = useRef<(() => void) | null>(null);
  const systemEndRef = useRef<number | null>(null);
  // Consecutive wake-loop failures. One blip is retried; a run of them is a fault.
  const wakeFailuresRef = useRef(0);

  const setListeningState = useCallback((value: boolean) => {
    listeningRef.current = value;
    setListening(value);
  }, []);

  const setWakeState = useCallback((value: boolean) => {
    wakeActiveRef.current = value;
    setWakeActive(value);
  }, []);

  const setArmed = useCallback((value: boolean) => {
    armedRef.current = value;
    setArmedState(value);
  }, []);

  const stopLevelLoop = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    smoothedRef.current = 0;
    setLevel(0);
  }, []);

  const startLevelLoop = useCallback(() => {
    if (rafRef.current !== null) return;
    const data = new Uint8Array(256);
    let lastSample = 0;
    const tick = (time: number) => {
      rafRef.current = requestAnimationFrame(tick);
      if (time - lastSample < 70) return;
      lastSample = time;
      let value = 0;
      const analyser = analyserRef.current ?? micAnalyserRef.current;
      if (analyser) {
        analyser.getByteTimeDomainData(data);
        let sum = 0;
        for (let i = 0; i < data.length; i += 1) {
          const sample = (data[i] - 128) / 128;
          sum += sample * sample;
        }
        value = Math.min(1, Math.sqrt(sum / data.length) * 4);
      } else if (speakingRef.current) {
        // No analyser: the clip is playing through the backend, which the tab
        // cannot measure. A made-up waveform would claim activity with no data
        // behind it, so the meter rests at zero and the status line says where
        // the audio is coming from.
        value = 0;
      }
      if (!analyser && !speakingRef.current && !listeningRef.current) value = 0;
      smoothedRef.current = smoothedRef.current * 0.65 + value * 0.35;
      setLevel(Math.round(Math.min(1, smoothedRef.current) * 100) / 100);
      if (!analyser && !speakingRef.current && !listeningRef.current && !wakeActiveRef.current) {
        stopLevelLoop();
      }
    };
    rafRef.current = requestAnimationFrame(tick);
  }, [stopLevelLoop]);

  const ensureAnalyser = useCallback(async () => {
    if (micAnalyserRef.current || !navigator.mediaDevices?.getUserMedia) return;
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    streamRef.current = stream;
    // A live stream is direct evidence of permission. The permissions query is
    // async, and on some first grants reports 'prompt' for a moment, so do not
    // let it be the only source of truth for the UI wording.
    setMicPermission('granted');
    let ctx = audioCtxRef.current;
    if (!ctx) {
      const Ctor = getAudioContextCtor();
      ctx = Ctor ? new Ctor() : null;
      audioCtxRef.current = ctx;
    }
    if (!ctx) return;
    if (ctx.state === 'suspended') {
      // The meter is cosmetic - speech recognition does not read this graph -
      // and Chrome only releases a suspended context after the window has been
      // clicked. Awaiting it forever would stop the wake listener from ever
      // starting in a tab nobody has touched yet.
      await Promise.race([
        ctx.resume().catch(() => undefined),
        new Promise((resolve) => window.setTimeout(resolve, 1000)),
      ]);
    }
    const source = ctx.createMediaStreamSource(stream);
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    analyser.smoothingTimeConstant = 0.75;
    source.connect(analyser);
    micAnalyserRef.current = analyser;
    startLevelLoop();
  }, [startLevelLoop]);

  const stopMic = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    micAnalyserRef.current = null;
    if (!speakingRef.current) stopLevelLoop();
  }, [stopLevelLoop]);

  /**
   * @param clearFollowUp false when starting a new utterance, true when the user
   * interrupted. The wake flow parks "start listening" in afterSpeechRef and the
   * old code wiped it here before the greeting had even played, so JARVIS never
   * went back to the microphone.
   */
  const stopPlayback = useCallback((clearFollowUp = true) => {
    speechEpochRef.current += 1;
    if (ttsSourceRef.current) {
      try {
        ttsSourceRef.current.stop();
      } catch {
        /* already stopped */
      }
      ttsSourceRef.current = null;
    }
    const synth = window.speechSynthesis;
    if (synth) {
      try {
        synth.cancel();
      } catch {
        /* some browsers throw when the queue is already empty */
      }
    }
    // Interrupting has to silence the laptop speakers too, not just the tab.
    if (systemEndRef.current !== null) {
      window.clearTimeout(systemEndRef.current);
      systemEndRef.current = null;
    }
    if (systemStopRef.current) {
      const purge = systemStopRef.current;
      systemStopRef.current = null;
      purge();
    }
    speakingRef.current = false;
    spokenTextRef.current = '';
    setSpeaking(false);
    analyserRef.current = null;
    if (clearFollowUp) afterSpeechRef.current = null;
    stopBargeInRef.current();
    if (!listeningRef.current && !wakeActiveRef.current && !micAnalyserRef.current) stopLevelLoop();
  }, [stopLevelLoop]);

  const finishSpeaking = useCallback((epoch: number) => {
    if (epoch !== speechEpochRef.current) return;
    speakingRef.current = false;
    spokenTextRef.current = '';
    setSpeaking(false);
    analyserRef.current = null;
    stopBargeInRef.current();
    if (!listeningRef.current && !wakeActiveRef.current && !micAnalyserRef.current) stopLevelLoop();
    const next = afterSpeechRef.current;
    afterSpeechRef.current = null;
    if (next) next();
  }, [stopLevelLoop]);

  const loadVoices = useCallback(() => {
    const synth = window.speechSynthesis;
    if (!synth) return [] as SpeechSynthesisVoice[];
    const list = synth.getVoices();
    if (list.length > 0) {
      voicesReadyRef.current = true;
      setVoices(list);
    }
    return list;
  }, []);

  /**
   * Edge and Chrome populate getVoices() asynchronously; speaking before that
   * resolves silently selects no voice.
   */
  const waitForVoices = useCallback(async (): Promise<SpeechSynthesisVoice[]> => {
    const synth = window.speechSynthesis;
    if (!synth) return [];
    const immediate = synth.getVoices();
    if (immediate.length > 0) return immediate;
    for (let attempt = 0; attempt < 12; attempt += 1) {
      await new Promise((resolve) => window.setTimeout(resolve, 100));
      const list = synth.getVoices();
      if (list.length > 0) return list;
    }
    return [];
  }, []);

  const speakWithBrowser = useCallback(
    async (text: string, epoch: number): Promise<void> => {
      const synth = window.speechSynthesis;
      if (!synth) {
        setSpeechStatus({
          route: 'browser',
          message: 'This browser cannot synthesize speech. Keep backend TTS enabled.',
          at: Date.now(),
        });
        finishSpeaking(epoch);
        return;
      }
      const language = optionsRef.current.language || 'en-IN';
      const list = voicesReadyRef.current ? synth.getVoices() : await waitForVoices();
      const needsHindi = DEVANAGARI.test(text);
      const picked = pickBrowserVoice(list, optionsRef.current.voiceName, language);

      if (!picked.voice) {
        setError(
          picked.reason === 'no-hindi-voice'
            ? `No ${language} voice is installed for the browser. Text still shows in ${language}, and English speech keeps working. Add a Hindi voice in Windows Settings > Time & language > Language & region.`
            : 'No speech synthesizer voice is available in this browser yet. Speak replies are temporarily unavailable.',
        );
        finishSpeaking(epoch);
        return;
      }

      const chunks = chunkForSpeech(text);
      const chosenVoice = picked.voice;
      // cancel() immediately before speak() makes Chrome drop the new utterance,
      // so the queue is given a tick to settle first.
      synth.cancel();
      await new Promise((resolve) => window.setTimeout(resolve, 60));
      if (epoch !== speechEpochRef.current) return;

      let started = false;
      chunks.forEach((chunk, index) => {
        const utterance = new SpeechSynthesisUtterance(chunk);
        utterance.voice = chosenVoice;
        utterance.lang = chosenVoice.lang || language;
        utterance.onstart = () => {
          started = true;
        };
        utterance.onerror = (event) => {
          if (event.error === 'canceled' || event.error === 'interrupted') return;
          if (index === chunks.length - 1 || !started) {
            setSpeechStatus({
              route: 'browser',
              message: `Browser speech failed (${event.error}).`,
              at: Date.now(),
            });
          }
        };
        if (index === chunks.length - 1) {
          utterance.onend = () => {
            if (!started) {
              setSpeechStatus({
                route: 'browser',
                message: 'The browser reported speech but produced no audio.',
                at: Date.now(),
              });
            }
            finishSpeaking(epoch);
          };
        }
        synth.speak(utterance);
      });

      if (!started && chunks.length > 0) {
        // Chrome sometimes never fires onstart; verify shortly after and report
        // rather than leaving the speaking state stuck.
        window.setTimeout(() => {
          if (epoch === speechEpochRef.current && !started && !synth.speaking && !synth.pending) {
            finishSpeaking(epoch);
          }
        }, 1500);
      }

      setSpeechStatus({
        route: 'browser',
        message: `Spoken with ${chosenVoice.name} (${chosenVoice.lang})${
          picked.fellBack && optionsRef.current.voiceName
            ? ` — "${optionsRef.current.voiceName}" is not installed, so a close voice was used`
            : ''
        }${
          needsHindi && !chosenVoice.lang.toLowerCase().startsWith('hi')
            ? ' — an English voice reading Hindi, so the accent is approximate'
            : ''
        }.`,
        at: Date.now(),
      });
      startLevelLoop();
      startBargeInRef.current();
    },
    [finishSpeaking, startLevelLoop, waitForVoices],
  );

  /**
   * Play the reply on the Windows default output device, from the backend. Used
   * whenever the tab itself may not make a sound - which is every reply before
   * the window has been clicked, so the wake greeting and the first answer of a
   * session stop arriving silently.
   */
  const speakThroughSystem = useCallback(
    async (text: string, epoch: number): Promise<void> => {
      const result = await api.ttsSpeak(
        text,
        optionsRef.current.language,
        optionsRef.current.sapiVoice,
      );
      if (epoch !== speechEpochRef.current) {
        // The backend already started playing before this call returned, so a
        // cancelled turn has to purge the speakers rather than just be ignored.
        void api.ttsSpeakStop().catch(() => undefined);
        return;
      }
      const ms = Math.max(400, Math.round(result.duration_ms));
      systemStopRef.current = () => {
        void api.ttsSpeakStop().catch(() => undefined);
      };
      systemEndRef.current = window.setTimeout(() => {
        systemEndRef.current = null;
        finishSpeaking(epoch);
      }, ms + 300);
      setSpeechStatus({
        route: 'system',
        message:
          `Spoken on the laptop speakers, ${((ms - 300) / 1000).toFixed(1)} s. ` +
          'This tab may not play audio until it is clicked.' +
          (result.romanized
            ? ' No Hindi voice is installed, so the text was read as Latin phonetics.'
            : ''),
        at: Date.now(),
      });
      startBargeInRef.current();
    },
    [finishSpeaking],
  );

  const speak = useCallback(
    async (text: string) => {
      const clean = (text || '').trim();
      if (!clean || !optionsRef.current.ttsEnabled) return;
      // stopPlayback() also advances the epoch, so take the token afterwards:
      // claiming it first made every later `epoch !== current` guard bail out and
      // the reply was fetched, never played, and left "speaking" stuck on.
      stopPlayback(false);
      const epoch = ++speechEpochRef.current;
      speakingRef.current = true;
      spokenTextRef.current = clean;
      setSubtitle(clean);
      setSpeaking(true);
      startLevelLoop();
      setError(null);

      // A tab that has never been clicked is not allowed to make a sound, and
      // waiting on its suspended AudioContext only loses the reply. Send it to
      // the speakers instead, and keep the browser voice as the backup.
      if (tabMayBeBlocked(activatedRef.current)) {
        try {
          await speakThroughSystem(clean, epoch);
          return;
        } catch (cause) {
          if (epoch !== speechEpochRef.current) return;
          const reason = cause instanceof Error ? cause.message : String(cause);
          try {
            await speakWithBrowser(clean, epoch);
          } catch {
            setError(`Speech output failed. Speakers: ${reason}. Browser fallback also failed.`);
            finishSpeaking(epoch);
          }
          return;
        }
      }

      try {
        const { blob, info } = await api.tts(
          clean,
          optionsRef.current.language,
          optionsRef.current.sapiVoice,
        );
        if (epoch !== speechEpochRef.current) return;
        let ctx = audioCtxRef.current;
        const Ctor = getAudioContextCtor();
        if (!ctx) {
          if (!Ctor) throw new Error('Audio playback is unavailable in this browser.');
          ctx = new Ctor();
          audioCtxRef.current = ctx;
        }
        if (ctx.state === 'suspended') {
          // Chrome keeps a context suspended until the window gets a real click or
          // keypress, and resume() waits rather than rejecting. Without the race the
          // speaking indicator would stay on forever over audio that never plays.
          const resumed = await Promise.race([
            ctx.resume().then(
              () => true,
              () => false,
            ),
            new Promise<boolean>((resolve) => window.setTimeout(() => resolve(false), 1500)),
          ]);
          if (!resumed) {
            throw new Error(
              'The browser is blocking audio output until this window is clicked.',
            );
          }
        }
        const buffer = await ctx.decodeAudioData(await blob.arrayBuffer());
        if (epoch !== speechEpochRef.current) return;
        // Windows SAPI answers Devanagari with a valid-but-empty WAV and does
        // not raise, so silence has to be detected here or the fallback never runs.
        if (bufferIsSilent(buffer)) {
          throw new Error('Backend speech produced no audible audio.');
        }
        const source = ctx.createBufferSource();
        source.buffer = buffer;
        const outputAnalyser = ctx.createAnalyser();
        outputAnalyser.fftSize = 512;
        outputAnalyser.smoothingTimeConstant = 0.7;
        source.connect(outputAnalyser);
        outputAnalyser.connect(ctx.destination);
        analyserRef.current = outputAnalyser;
        ttsSourceRef.current = source;
        source.onended = () => {
          if (ttsSourceRef.current === source) ttsSourceRef.current = null;
          finishSpeaking(epoch);
        };
        source.start();
        setSpeechStatus({
          route: 'backend',
          message: `Spoken with Windows SAPI${
            // Taken from the response, not inferred from the text: once a real
            // Hindi voice is installed the backend stops transliterating and the
            // status has to change with it.
            info.romanized
              ? '; no Hindi voice is installed, so the text was read as Latin phonetics'
              : info.devanagari
                ? '; spoken by a Hindi voice'
                : ''
          }.`,
          at: Date.now(),
        });
        startBargeInRef.current();
        return;
      } catch (cause) {
        if (epoch !== speechEpochRef.current) return;
        const reason = cause instanceof Error ? cause.message : String(cause);
        // Backend TTS failed or the tab stayed blocked: try the speakers, then
        // the browser voice. Never in parallel.
        try {
          await speakThroughSystem(clean, epoch);
          return;
        } catch {
          /* fall through to the browser voice */
        }
        try {
          await speakWithBrowser(clean, epoch);
        } catch {
          setError(`Speech output failed. Backend: ${reason}. Browser fallback also failed.`);
          finishSpeaking(epoch);
        }
        return;
      }
    },
    [finishSpeaking, speakThroughSystem, speakWithBrowser, startLevelLoop, stopPlayback],
  );

  speakRef.current = speak;

  const buildRecognition = useCallback(() => {
    const Ctor = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!Ctor) return null;
    const recognition = new Ctor();
    recognition.lang = optionsRef.current.language || 'en-IN';
    recognition.interimResults = true;
    // Continuous in both modes: a push-to-talk command has to survive a breath
    // in the middle of the sentence, and JARVIS ends the session once the room
    // goes quiet.
    recognition.continuous = true;
    recognition.maxAlternatives = 1;
    return recognition;
  }, []);

  const stopBargeIn = useCallback(() => {
    const recognition = bargeRef.current;
    bargeRef.current = null;
    if (recognition) {
      try {
        recognition.abort();
      } catch {
        /* ignore */
      }
    }
  }, []);

  const heardOwnSpeech = useCallback((heard: string) => {
    const spoken = spokenTextRef.current;
    if (!spoken) return false;
    const heardWords = wordsOf(heard);
    if (heardWords.length === 0) return false;
    const spokenWords = new Set(wordsOf(spoken));
    const overlap = heardWords.filter((word) => spokenWords.has(word)).length;
    return overlap / heardWords.length > 0.6;
  }, []);

  /**
   * Barge-in while push-to-talk is active. In wake mode the always-on
   * recogniser already hears the user, so interruption is handled there instead
   * of opening a second recognition session, which Chrome does not allow.
   */
  const startBargeIn = useCallback(() => {
    if (!supported || bargeRef.current || !speakingRef.current) return;
    if (listeningRef.current || modeRef.current === 'wake') return;
    const Ctor = window.SpeechRecognition ?? window.webkitSpeechRecognition;
    if (!Ctor) return;
    const recognition = new Ctor();
    recognition.lang = optionsRef.current.language || 'en-IN';
    recognition.interimResults = false;
    recognition.continuous = false;
    recognition.onresult = (event) => {
      let text = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        if (event.results[i].isFinal) text += ` ${event.results[i][0].transcript}`;
      }
      const heard = text.trim();
      if (!heard || !speakingRef.current) return;
      if (heardOwnSpeech(heard)) return;
      if (!INTERRUPTION_RE.test(heard)) return;
      setTranscript(heard);
      const remainder = stripInterruptionPhrase(heard);
      stopPlayback();
      optionsRef.current.onInterrupted?.();
      if (remainder) optionsRef.current.onCommand(remainder);
      else void startRef.current();
    };
    recognition.onerror = () => {
      bargeRef.current = null;
    };
    recognition.onend = () => {
      if (bargeRef.current === recognition) {
        bargeRef.current = null;
        if (speakingRef.current) startBargeIn();
      }
    };
    bargeRef.current = recognition;
    try {
      recognition.start();
    } catch {
      bargeRef.current = null;
    }
  }, [heardOwnSpeech, stopPlayback, supported]);

  startBargeInRef.current = startBargeIn;
  stopBargeInRef.current = stopBargeIn;

  /**
   * A spoken turn runs from the wake word to the first real silence. Finals are
   * appended while it is open and dispatched once, which is what stops JARVIS
   * answering the first half of a sentence.
   */
  const closeTurn = useCallback((dispatch: boolean) => {
    const turn = turnRef.current;
    if (turn.timer !== null) {
      window.clearTimeout(turn.timer);
      turn.timer = null;
    }
    const heard = (turn.text || turn.interim).trim();
    turn.text = '';
    turn.interim = '';
    turn.hasFinal = false;
    turn.deadline = 0;
    setArmed(false);
    if (!dispatch) return;
    const phrase = optionsRef.current.wakePhrase.trim().toLowerCase();
    const command = phrase ? stripWakeWord(heard, phrase) : heard;
    if (!command || isFalseEnding(command)) return;
    lastDispatchRef.current = { text: command.toLowerCase(), at: Date.now() };
    optionsRef.current.onCommand(command);
  }, [setArmed]);

  const openTurn = useCallback(() => {
    const turn = turnRef.current;
    if (turn.timer !== null) window.clearTimeout(turn.timer);
    turn.text = '';
    turn.interim = '';
    turn.hasFinal = false;
    turn.deadline = Date.now() + TURN_WINDOW_MS;
    // Silence after the wake word still has to hand the loop back.
    turn.timer = window.setTimeout(() => closeTurn(false), TURN_WINDOW_MS);
  }, [closeTurn]);

  const addToTurn = useCallback(
    (raw: string) => {
      const turn = turnRef.current;
      if (!turn.deadline) openTurn();
      turn.text = `${turn.text} ${raw}`.trim();
      turn.hasFinal = true;
      // A real final landed, so the interim watchdog is no longer needed.
      if (turn.interim) turn.interim = '';
      if (turn.timer !== null) window.clearTimeout(turn.timer);
      const left = turn.deadline - Date.now();
      turn.timer = window.setTimeout(() => closeTurn(true), left <= 0 ? 0 : TURN_QUIET_MS);
    },
    [closeTurn, openTurn],
  );

  /**
   * Chrome often keeps revising an interim transcript after he has stopped
   * talking and never sends the final, which left the wake loop showing
   * "listening" forever. Once the guess has held still for a full beat, treat it
   * as what he said and run it.
   */
  const handleWakeInterim = useCallback(
    (raw: string) => {
      const turn = turnRef.current;
      const clean = raw.trim();
      if (!turn.deadline || !clean || turn.hasFinal) return;
      if (turn.deadline - Date.now() <= 0) return;
      if (heardOwnSpeech(clean)) return;
      // Same words as the previous guess means the engine is still deciding; the
      // timer already running gets to finish. Only growth restarts the clock.
      if (clean === turn.interim) return;
      // He is talking over the greeting: cut it short so the reply is not queued
      // behind a word he has already stopped waiting for.
      if (speakingRef.current) {
        speechEpochRef.current += 1;
        stopPlayback();
      }
      turn.interim = clean;
      if (turn.timer !== null) window.clearTimeout(turn.timer);
      const left = turn.deadline - Date.now();
      turn.timer = window.setTimeout(() => closeTurn(true), Math.min(INTERIM_HOLD_MS, left));
    },
    [closeTurn, heardOwnSpeech, stopPlayback],
  );

  const start = useCallback(async () => {
    if (!supported) {
      setError('Speech recognition is not available in this browser. Use the text input or Edge/Chrome.');
      return;
    }
    if (listeningRef.current || modeRef.current === 'command') return;
    if (wakeActiveRef.current) {
      // A second recognition session cannot run alongside the wake loop, and
      // Chrome throws if a new one starts before the old one has closed. Let it
      // settle, then hand the turn to the wake loop instead of racing it.
      wakeActiveRef.current = false;
      setWakeState(false);
      stoppedRef.current = true;
      closeTurn(false);
      try {
        recognitionRef.current?.abort();
      } catch {
        /* ignore */
      }
      recognitionRef.current = null;
      modeRef.current = null;
      await new Promise((resolve) => window.setTimeout(resolve, 250));
    }
    setError(null);
    setTranscript('');
    try {
      await ensureAnalyser();
    } catch {
      setError('Microphone access was denied. Listening needs microphone permission.');
      return;
    }
    const recognition = buildRecognition();
    if (!recognition) {
      setError('Speech recognition could not be created.');
      return;
    }
    stoppedRef.current = false;
    modeRef.current = 'command';
    let handled = false;
    let collected = '';
    let lastInterim = '';
    let interimTimer: number | null = null;
    recognition.onstart = () => setListeningState(true);
    const commit = () => {
      if (commandTurnRef.current !== null) {
        window.clearTimeout(commandTurnRef.current);
        commandTurnRef.current = null;
      }
      if (interimTimer !== null) {
        window.clearTimeout(interimTimer);
        interimTimer = null;
      }
      const text = collected.trim();
      collected = '';
      if (!text || handled || stoppedRef.current) return;
      handled = true;
      try {
        recognition.stop();
      } catch {
        /* already ending */
      }
      optionsRef.current.onCommand(text);
    };
    /**
     * The same end-of-speech rule as the wake loop, for push-to-talk: if the
     * engine keeps guessing and never finalises, a still guess means he stopped
     * talking and the command should run.
     */
    const followInterim = (guess: string) => {
      const clean = guess.trim();
      if (!clean || clean === lastInterim) return;
      lastInterim = clean;
      if (interimTimer !== null) window.clearTimeout(interimTimer);
      collected = clean;
      interimTimer = window.setTimeout(() => {
        interimTimer = null;
        commit();
      }, INTERIM_HOLD_MS);
    };
    recognition.onresult = (event) => {
      let interim = '';
      let finals = 0;
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        if (event.results[i].isFinal) {
          collected += ` ${event.results[i][0].transcript}`;
          finals += 1;
        } else {
          interim += ` ${event.results[i][0].transcript}`;
        }
      }
      if (finals && isFalseEnding(collected)) collected = '';
      setTranscript((collected + interim).trim());
      if (!finals || handled) {
        if (!finals && !handled) followInterim(interim);
        return;
      }
      // A real final beats the guess it was built from.
      lastInterim = '';
      if (interimTimer !== null) {
        window.clearTimeout(interimTimer);
        interimTimer = null;
      }
      if (commandTurnRef.current !== null) window.clearTimeout(commandTurnRef.current);
      commandTurnRef.current = window.setTimeout(() => {
        commandTurnRef.current = null;
        commit();
      }, TURN_QUIET_MS);
    };
    recognition.onerror = (event) => {
      if (event.error === 'aborted') return;
      setError(ERROR_TEXT[event.error] ?? `Speech recognition error: ${event.error}`);
    };
    recognition.onend = () => {
      // The engine ended on its own: whatever was collected is the command.
      if (!stoppedRef.current) commit();
      recognitionRef.current = null;
      modeRef.current = null;
      setListeningState(false);
      stopMic();
    };
    recognitionRef.current = recognition;
    try {
      recognition.start();
    } catch {
      setError('Speech recognition is already starting. Try again in a moment.');
      modeRef.current = null;
      stopMic();
    }
  }, [buildRecognition, closeTurn, ensureAnalyser, setListeningState, setWakeState, stopMic, supported]);

  startRef.current = start;
  speakRef.current = speak;

  const stop = useCallback(() => {
    stoppedRef.current = true;
    if (commandTurnRef.current !== null) {
      window.clearTimeout(commandTurnRef.current);
      commandTurnRef.current = null;
    }
    closeTurn(false);
    const recognition = recognitionRef.current;
    recognitionRef.current = null;
    modeRef.current = null;
    afterSpeechRef.current = null;
    setArmed(false);
    stopBargeInRef.current();
    if (recognition) {
      try {
        recognition.abort();
      } catch {
        /* ignore */
      }
    }
    setListeningState(false);
    setWakeState(false);
    stopMic();
  }, [closeTurn, setArmed, setListeningState, setWakeState, stopMic]);

  /**
   * One continuous recogniser owns wake detection, the greeting, the command
   * that follows it, and spoken interruptions. Switching between a wake session
   * and a command session was where the old implementation lost the handoff.
   */
  const handleWakeResult = useCallback(
    (raw: string) => {
      const clean = raw.trim();
      if (!clean) return;

      // A turn opened by the wake word is still collecting. Everything heard
      // now belongs to that command — even across a pause — so the greeting is
      // cut short rather than the sentence being chopped in half.
      if (turnRef.current.deadline) {
        if (heardOwnSpeech(clean)) return;
        setTranscript(clean);
        if (speakingRef.current) {
          speechEpochRef.current += 1;
          stopPlayback();
        }
        addToTurn(clean);
        return;
      }

      setTranscript(clean);

      const phrase = optionsRef.current.wakePhrase.trim().toLowerCase();
      if (!phrase) return;

      // The wake word is checked before the "am I talking" branch, because the
      // old order swallowed it: saying "hey jarvis" while a reply was still being
      // read out was dropped unless it also matched an interruption phrase, so
      // the assistant looked deaf exactly when it had just said something.
      if (!armedRef.current && !heardOwnSpeech(clean)) {
        const hit = matchWakeWord(clean, phrase);
        if (hit.matched) {
          // The interim watchdog may have already run this exact sentence; do not
          // greet and execute it a second time.
          const spoken = (hit.remainder || '').trim().toLowerCase();
          if (spoken && spoken === lastDispatchRef.current.text
            && Date.now() - lastDispatchRef.current.at < 3000) return;
          // The same utterance can arrive as two finals; a different sentence is a
          // new call and must be answered even seconds after the last one.
          const words = clean.toLowerCase();
          if (lastWakeRef.current.words === words && Date.now() - lastWakeRef.current.at < 4000) return;
          lastWakeRef.current = { words, at: Date.now() };
          if (speakingRef.current) {
            speechEpochRef.current += 1;
            stopPlayback();
            optionsRef.current.onInterrupted?.();
          }
          setArmed(true);
          optionsRef.current.onWake();
          openTurn();
          if (hit.remainder) addToTurn(hit.remainder);
          const greeting = optionsRef.current.wakeGreeting.trim();
          if (greeting && optionsRef.current.ttsEnabled) void speakRef.current(greeting);
          return;
        }
      }

      if (speakingRef.current) {
        if (heardOwnSpeech(clean)) return;
        const interrupted = INTERRUPTION_RE.test(clean);
        // Armed means the greeting is still playing: anything the user says
        // over it is their command, so cut the greeting short rather than
        // dropping the utterance and making them repeat themselves.
        if (!interrupted && !armedRef.current) return;
        speechEpochRef.current += 1;
        stopPlayback();
        const wasArmed = armedRef.current;
        setArmed(false);
        if (!wasArmed) optionsRef.current.onInterrupted?.();
        const remainder = stripInterruptionPhrase(clean);
        if (remainder) {
          openTurn();
          addToTurn(remainder);
        }
        return;
      }

      if (armedRef.current) addToTurn(clean);
    },
    [addToTurn, heardOwnSpeech, openTurn, setArmed, stopPlayback],
  );

  /**
   * Take the wake listener down and say why. Leaving `wakeActive` on after the
   * recogniser dies makes the HUD claim it is hearing the wake word when it is
   * deaf, which is worse than admitting the failure.
   */
  const stopWakeLoop = useCallback(
    (fault: string | null) => {
      setWakeState(false);
      modeRef.current = null;
      setWakeFault(fault);
      const recognition = recognitionRef.current;
      recognitionRef.current = null;
      if (recognition) {
        recognition.onresult = null;
        recognition.onerror = null;
        recognition.onend = null;
        try {
          recognition.abort();
        } catch {
          /* already finished */
        }
      }
      if (fault) setError(fault);
    },
    [setWakeState],
  );

  const startWakeLoop = useCallback(async () => {
    const recognition = buildRecognition();
    if (!recognition) return;
    stoppedRef.current = false;
    wakeActiveRef.current = true;
    setWakeState(true);
    setWakeFault(null);
    modeRef.current = 'wake';
    let startedAt = Date.now();
    recognition.onresult = (event) => {
      let text = '';
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        if (event.results[i].isFinal) text += ` ${event.results[i][0].transcript}`;
        else interim += ` ${event.results[i][0].transcript}`;
      }
      if (text.trim()) {
        handleWakeResult(text.trim());
        return;
      }
      // No final in this callback, but he may have finished speaking. Let the
      // still-growing guess end the turn instead of waiting for a final that
      // Chrome sometimes never sends.
      if (interim.trim()) handleWakeInterim(interim.trim());
    };
    recognition.onerror = (event) => {
      if (event.error === 'aborted' || event.error === 'no-speech') return;
      const detail = ERROR_TEXT[event.error] ?? `Wake word recognition error: ${event.error}`;
      // Permission, device and language problems never fix themselves, and a
      // recogniser that keeps erroring is not listening no matter how often it
      // is restarted.
      if (FATAL_WAKE_ERRORS.has(event.error)) {
        wakeFailuresRef.current = 0;
        stopWakeLoop(detail);
        return;
      }
      wakeFailuresRef.current += 1;
      if (wakeFailuresRef.current >= 3) {
        stopWakeLoop(`${detail} JARVIS stopped listening after repeated failures.`);
        return;
      }
      setError(detail);
    };
    recognition.onend = () => {
      if (!wakeActiveRef.current || stoppedRef.current) return;
      const aliveFor = Date.now() - startedAt;
      if (aliveFor > 4000) wakeFailuresRef.current = 0;
      // The engine closed the session while a turn was still open, so he has
      // stopped talking: run what he said rather than letting the window expire
      // and discard it.
      if (turnRef.current.deadline && (turnRef.current.text || turnRef.current.interim).trim()) {
        closeTurn(true);
      }
      // Chrome ends continuous recognition by itself; restart it so the wake
      // word keeps working without the user re-arming anything. A session that
      // closes again the instant it opens is flapping, not listening.
      window.setTimeout(() => {
        if (!wakeActiveRef.current || stoppedRef.current) return;
        if (aliveFor < 1200) wakeFailuresRef.current += 1;
        if (wakeFailuresRef.current >= 3) {
          stopWakeLoop(
            'The microphone kept closing the speech session. Wake-word listening is off. ' +
              'Press the microphone to talk instead.',
          );
          return;
        }
        try {
          startedAt = Date.now();
          recognition.start();
        } catch {
          stopWakeLoop(
            'Wake word listening stopped unexpectedly. Press Start listening to retry.',
          );
        }
      }, 400);
    };
    recognitionRef.current = recognition;
    try {
      recognition.start();
    } catch {
      stopWakeLoop(
        'Wake word recognition could not start. Close other microphone users and retry.',
      );
      stopMic();
    }
  }, [buildRecognition, closeTurn, handleWakeInterim, handleWakeResult, setWakeState, stopMic, stopWakeLoop]);

  const toggleWake = useCallback(async () => {
    if (wakeActiveRef.current) {
      stoppedRef.current = true;
      wakeActiveRef.current = false;
      closeTurn(false);
      setArmed(false);
      setWakeState(false);
      setWakeFault(null);
      wakeFailuresRef.current = 0;
      const recognition = recognitionRef.current;
      recognitionRef.current = null;
      modeRef.current = null;
      try {
        recognition?.abort();
      } catch {
        /* ignore */
      }
      stopMic();
      return;
    }
    if (!supported) {
      setError('Wake word needs browser speech recognition (Chrome or Edge).');
      setWakeFault('This browser has no speech recognition engine. Use Chrome or Edge.');
      return;
    }
    if (micPermission === 'denied') {
      setWakeFault(
        'Microphone access is blocked for this address. Click the lock or microphone icon ' +
          'in the address bar, allow it, then reload.',
      );
      setError('Microphone access is blocked for this address. Allow it in the address bar.');
      return;
    }
    if (listeningRef.current) stop();
    setError(null);
    try {
      await ensureAnalyser();
    } catch {
      setWakeFault(
        'The microphone could not be opened. Windows blocks microphone access for this ' +
          'browser under Settings > Privacy & security > Microphone.',
      );
      setError('Wake word needs microphone permission. Click the mic button to grant access.');
      return;
    }
    wakeFailuresRef.current = 0;
    await startWakeLoop();
  }, [
    closeTurn,
    ensureAnalyser,
    micPermission,
    setArmed,
    setWakeState,
    stop,
    startWakeLoop,
    stopMic,
    supported,
  ]);

  const toggle = useCallback(() => {
    if (listeningRef.current) stop();
    else void start();
  }, [start, stop]);

  const testVoice = useCallback(
    async (sample: string) => {
      setError(null);
      await speak(sample);
    },
    [speak],
  );

  const clearError = useCallback(() => setError(null), []);

  /**
   * Chrome will not let a freshly created AudioContext make a sound until the
   * page itself gets a click or keypress, and `resume()` hangs instead of
   * rejecting when that has not happened. This assistant is driven by the wake
   * word, so a reply can easily be the first thing anyone tries to play. Unlock
   * on the first interaction of any kind and keep that context for every reply
   * afterwards.
   */
  useEffect(() => {
    if (!supported) return;
    const unlock = () => {
      activatedRef.current = true;
      const Ctor = getAudioContextCtor();
      if (!Ctor) return;
      const ctx = audioCtxRef.current ?? new Ctor();
      audioCtxRef.current = ctx;
      if (ctx.state === 'suspended') void ctx.resume();
    };
    window.addEventListener('pointerdown', unlock);
    window.addEventListener('keydown', unlock);
    return () => {
      window.removeEventListener('pointerdown', unlock);
      window.removeEventListener('keydown', unlock);
    };
  }, [supported]);

  /**
   * A denied microphone is not a transient error - Chrome remembers the denial
   * per address - so the panel needs to name it before the user has spoken a
   * single word at a deaf assistant.
   */
  useEffect(() => {
    const permissions = navigator.permissions;
    if (!permissions?.query) return;
    let tracked: PermissionStatus | null = null;
    let cancelled = false;
    const read = () => {
      if (tracked) setMicPermission(tracked.state as 'granted' | 'denied' | 'prompt');
    };
    permissions
      .query({ name: 'microphone' as PermissionName })
      .then((status) => {
        if (cancelled) return;
        tracked = status;
        read();
        status.addEventListener('change', read);
      })
      .catch(() => {
        /* browsers that do not expose microphone permissions stay 'unknown' */
      });
    return () => {
      cancelled = true;
      tracked?.removeEventListener('change', read);
    };
  }, []);

  useEffect(() => {
    const synth = window.speechSynthesis;
    if (!synth) return;
    loadVoices();
    const onChange = () => loadVoices();
    synth.addEventListener('voiceschanged', onChange);
    // Some engines never fire voiceschanged on a fresh profile.
    const retry = window.setTimeout(() => {
      if (!voicesReadyRef.current) loadVoices();
    }, 800);
    return () => {
      synth.removeEventListener('voiceschanged', onChange);
      window.clearTimeout(retry);
    };
  }, [loadVoices]);

  useEffect(() => {
    return () => {
      stoppedRef.current = true;
      wakeActiveRef.current = false;
      speechEpochRef.current += 1;
      try {
        recognitionRef.current?.abort();
      } catch {
        /* ignore */
      }
      try {
        bargeRef.current?.abort();
      } catch {
        /* ignore */
      }
      streamRef.current?.getTracks().forEach((track) => track.stop());
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
      try {
        ttsSourceRef.current?.stop();
      } catch {
        /* ignore */
      }
      window.speechSynthesis?.cancel();
      void audioCtxRef.current?.close();
    };
  }, []);

  return {
    supported,
    listening,
    speaking,
    subtitle,
    wakeActive,
    wakeFault,
    micPermission,
    armed,
    level,
    transcript,
    error,
    voices,
    speechStatus,
    start,
    stop,
    toggle,
    speak,
    toggleWake,
    testVoice,
    stopSpeaking: () => stopPlayback(),
    clearError,
  };
}

export type Voice = ReturnType<typeof useVoice>;
