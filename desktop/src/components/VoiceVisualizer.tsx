import { useEffect, useRef } from 'react';

interface VoiceVisualizerProps {
  level: number;
  mode: 'idle' | 'listening' | 'speaking' | 'muted';
  accent?: string;
  height?: number;
  bars?: number;
}

const MODE_COLOR: Record<VoiceVisualizerProps['mode'], string> = {
  idle: '#ff8a1e',
  listening: '#5fd4ff',
  speaking: '#ffb347',
  muted: '#5c6474',
};

function parseColor(hex: string): [number, number, number] {
  const value = Number.parseInt(hex.replace('#', ''), 16);
  if (Number.isNaN(value)) return [255, 138, 30];
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

export function VoiceVisualizer({
  level,
  mode,
  accent = '#ff8a1e',
  height = 56,
  bars = 56,
}: VoiceVisualizerProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const levelRef = useRef(level);
  const modeRef = useRef(mode);
  const accentRef = useRef(accent);
  levelRef.current = level;
  modeRef.current = mode;
  accentRef.current = accent;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const context = canvas.getContext('2d');
    if (!context) return;
    let width = 0;
    let dpr = 1;

    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      dpr = Math.min(window.devicePixelRatio || 1, 2);
      width = Math.max(80, Math.floor(rect.width));
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      context.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);

    let frame = 0;
    const draw = (now: number) => {
      frame = requestAnimationFrame(draw);
      if (document.hidden) return;
      const t = now / 1000;
      const currentMode = modeRef.current;
      const currentLevel = Math.max(0, Math.min(1, levelRef.current));
      const color = currentMode === 'idle' ? accentRef.current : MODE_COLOR[currentMode];
      const [r, g, b] = parseColor(color);
      const base = currentMode === 'muted' ? 0.03 : 0.08;
      const energy =
        currentMode === 'listening'
          ? Math.max(0.05, currentLevel)
          : currentMode === 'speaking'
            ? 0.25 + currentLevel * 0.75
            : 0.08;
      const gap = 3;
      const barWidth = Math.max(2, (width - gap * (bars - 1)) / bars);
      context.clearRect(0, 0, width, height);
      for (let i = 0; i < bars; i += 1) {
        const phase = i * 0.42;
        const wave =
          Math.sin(t * 3.1 + phase) * 0.35 +
          Math.sin(t * 1.27 - phase * 0.6) * 0.25 +
          Math.sin(t * 5.3 + phase * 1.7) * 0.18;
        const shaped = Math.abs(wave) * energy + base;
        const barHeight = Math.max(2, Math.min(height - 2, shaped * height * 1.7));
        const x = i * (barWidth + gap);
        const y = (height - barHeight) / 2;
        const alpha = currentMode === 'muted' ? 0.35 : 0.45 + shaped * 1.2;
        const gradient = context.createLinearGradient(0, y, 0, y + barHeight);
        gradient.addColorStop(0, `rgba(${r}, ${g}, ${b}, ${Math.min(0.95, alpha)})`);
        gradient.addColorStop(0.5, `rgba(${r}, ${g}, ${b}, ${Math.min(0.75, alpha + 0.1)})`);
        gradient.addColorStop(1, `rgba(${r}, ${g}, ${b}, ${Math.min(0.95, alpha)})`);
        context.fillStyle = gradient;
        context.beginPath();
        context.roundRect(x, y, barWidth, barHeight, Math.min(barWidth / 2, 2));
        context.fill();
      }
    };
    frame = requestAnimationFrame(draw);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [bars, height]);

  return (
    <div className={`visualizer visualizer--${mode}`} style={{ height }}>
      <canvas ref={canvasRef} style={{ width: '100%', height }} />
      <span className="visualizer__label">
        {mode === 'listening'
          ? 'Mic live'
          : mode === 'speaking'
            ? 'Speaking'
            : mode === 'muted'
              ? 'Mic muted'
              : 'Audio idle'}
      </span>
    </div>
  );
}
