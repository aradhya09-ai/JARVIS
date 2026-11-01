import { useMemo } from 'react';

import type { CSSProperties } from 'react';

import type { ReactorState } from '../types';

type SpinStyle = CSSProperties & Record<'--hud-period', string>;

interface JarvisHudProps {
  state: ReactorState;
  level?: number;
  progress?: number;
  reducedMotion?: boolean;
  onClick?: () => void;
}

const CX = 800;
const CY = 450;

const GOLD = '#ffc46b';
const CYAN = '#4fd2ff';

const STATE_LABEL: Record<ReactorState, string> = {
  idle: 'STANDBY',
  listening: 'LISTENING',
  thinking: 'THINKING',
  speaking: 'SPEAKING',
  executing: 'EXECUTING',
  completed: 'COMPLETE',
  error: 'ERROR',
  offline: 'OFFLINE',
  permission: 'CONFIRM',
  interrupted: 'INTERRUPT',
};

const STATE_ACCENT: Record<ReactorState, string> = {
  idle: GOLD,
  listening: '#7ce8ff',
  thinking: '#9db8ff',
  speaking: '#63f5d0',
  executing: GOLD,
  completed: '#69f0a0',
  error: '#ff5c5c',
  offline: '#3d5468',
  permission: GOLD,
  interrupted: '#ff9a6a',
};

// Seconds for one revolution. Motion tracks a real assistant state: offline
// freezes the dials instead of pretending something is running.
const STATE_PERIOD: Record<ReactorState, number> = {
  idle: 120,
  listening: 52,
  thinking: 24,
  speaking: 40,
  executing: 30,
  completed: 70,
  error: 90,
  offline: 0,
  permission: 60,
  interrupted: 100,
};

/** 5x7 LED matrix, one string per row. */
const GLYPHS: Record<string, string[]> = {
  ' ': ['00000', '00000', '00000', '00000', '00000', '00000', '00000'],
  '.': ['00000', '00000', '00000', '00000', '00000', '00110', '00110'],
  ':': ['00000', '00110', '00110', '00000', '00110', '00110', '00000'],
  '-': ['00000', '00000', '00000', '11111', '00000', '00000', '00000'],
  '/': ['00001', '00010', '00010', '00100', '01000', '01000', '10000'],
  A: ['01110', '10001', '10001', '11111', '10001', '10001', '10001'],
  B: ['11110', '10001', '10001', '11110', '10001', '10001', '11110'],
  C: ['01111', '10000', '10000', '10000', '10000', '10000', '01111'],
  D: ['11110', '10001', '10001', '10001', '10001', '10001', '11110'],
  E: ['11111', '10000', '10000', '11110', '10000', '10000', '11111'],
  F: ['11111', '10000', '10000', '11110', '10000', '10000', '10000'],
  G: ['01111', '10000', '10000', '10111', '10001', '10001', '01111'],
  H: ['10001', '10001', '10001', '11111', '10001', '10001', '10001'],
  I: ['11111', '00100', '00100', '00100', '00100', '00100', '11111'],
  J: ['00111', '00010', '00010', '00010', '00010', '10010', '01100'],
  K: ['10001', '10010', '10100', '11000', '10100', '10010', '10001'],
  L: ['10000', '10000', '10000', '10000', '10000', '10000', '11111'],
  M: ['10001', '11011', '10101', '10101', '10001', '10001', '10001'],
  N: ['10001', '11001', '10101', '10011', '10001', '10001', '10001'],
  O: ['01110', '10001', '10001', '10001', '10001', '10001', '01110'],
  P: ['11110', '10001', '10001', '11110', '10000', '10000', '10000'],
  Q: ['01110', '10001', '10001', '10001', '10101', '10010', '01101'],
  R: ['11110', '10001', '10001', '11110', '10100', '10010', '10001'],
  S: ['01111', '10000', '10000', '01110', '00001', '00001', '11110'],
  T: ['11111', '00100', '00100', '00100', '00100', '00100', '00100'],
  U: ['10001', '10001', '10001', '10001', '10001', '10001', '01110'],
  V: ['10001', '10001', '10001', '10001', '10001', '01010', '00100'],
  W: ['10001', '10001', '10001', '10101', '10101', '01010', '01010'],
  X: ['10001', '10001', '01010', '00100', '01010', '10001', '10001'],
  Y: ['10001', '10001', '01010', '00100', '00100', '00100', '00100'],
  Z: ['11111', '00001', '00010', '00100', '01000', '10000', '11111'],
  '0': ['01110', '10001', '10011', '10101', '11001', '10001', '01110'],
  '1': ['00100', '01100', '00100', '00100', '00100', '00100', '01110'],
  '2': ['01110', '10001', '00001', '00010', '00100', '01000', '11111'],
  '3': ['11110', '00001', '00001', '01110', '00001', '10001', '11110'],
  '4': ['00010', '00110', '01010', '10010', '11111', '00010', '00010'],
  '5': ['11111', '10000', '11110', '00001', '00001', '10001', '01110'],
  '6': ['00110', '01000', '10000', '11110', '10001', '10001', '01110'],
  '7': ['11111', '00001', '00010', '00100', '01000', '01000', '01000'],
  '8': ['01110', '10001', '10001', '01110', '10001', '10001', '01110'],
  '9': ['01110', '10001', '10001', '01111', '00001', '00010', '01100'],
};

const CELL_STEP = 6; // 5 lit columns plus one blank gutter.
const ROWS = 7;

interface Pixel {
  x: number;
  y: number;
  sep: boolean;
}

function dotTextWidth(text: string, cell: number): number {
  if (!text.length) return 0;
  return (text.length - 1) * CELL_STEP * cell + 5 * cell;
}

/** Lays a string out on the LED grid; `left`/`top` are the block's top corner. */
function dotText(text: string, cell: number, left: number, top: number): Pixel[] {
  const pixels: Pixel[] = [];
  for (let index = 0; index < text.length; index += 1) {
    const glyph = GLYPHS[text[index].toUpperCase()] ?? GLYPHS['-'];
    for (let row = 0; row < ROWS; row += 1) {
      for (let column = 0; column < 5; column += 1) {
        if (glyph[row][column] !== '1') continue;
        pixels.push({
          x: left + (index * CELL_STEP + column) * cell,
          y: top + row * cell,
          sep: text[index] === '.',
        });
      }
    }
  }
  return pixels;
}

function centeredDotText(text: string, cell: number, centerY: number): Pixel[] {
  const width = dotTextWidth(text, cell);
  return dotText(text, cell, CX - width / 2, centerY - (ROWS * cell) / 2);
}

function polar(radius: number, degrees: number): [number, number] {
  const radians = ((degrees - 90) * Math.PI) / 180;
  return [CX + Math.cos(radians) * radius, CY + Math.sin(radians) * radius];
}

function arcPath(radius: number, from: number, to: number): string {
  const [x0, y0] = polar(radius, from);
  const [x1, y1] = polar(radius, to);
  const large = Math.abs(to - from) > 180 ? 1 : 0;
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${radius} ${radius} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

interface Tick {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  major: boolean;
}

function tickRing(radius: number, count: number, length: number, majorEvery: number): Tick[] {
  const ticks: Tick[] = [];
  for (let i = 0; i < count; i += 1) {
    const major = i % majorEvery === 0;
    const angle = (i / count) * 360;
    const reach = major ? length * 2.1 : length;
    const [x1, y1] = polar(radius, angle);
    const [x2, y2] = polar(radius + reach, angle);
    ticks.push({ x1, y1, x2, y2, major });
  }
  return ticks;
}

function segments(radius: number, count: number, sweep: number): string[] {
  const step = 360 / count;
  return Array.from({ length: count }, (_, i) => arcPath(radius, i * step, i * step + sweep));
}

function RingTicks({
  radius,
  count,
  length,
  majorEvery,
  color,
  width = 1.6,
}: {
  radius: number;
  count: number;
  length: number;
  majorEvery: number;
  color: string;
  width?: number;
}) {
  return (
    <>
      {tickRing(radius, count, length, majorEvery).map((tick, i) => (
        <line
          key={i}
          x1={tick.x1}
          y1={tick.y1}
          x2={tick.x2}
          y2={tick.y2}
          stroke={color}
          strokeWidth={tick.major ? width * 1.8 : width}
          strokeLinecap="round"
        />
      ))}
    </>
  );
}

function SegmentedArcs({
  radius,
  count,
  sweep,
  color,
  width,
}: {
  radius: number;
  count: number;
  sweep: number;
  color: string;
  width: number;
}) {
  return (
    <>
      {segments(radius, count, sweep).map((d, i) => (
        <path key={i} d={d} fill="none" stroke={color} strokeWidth={width} strokeLinecap="round" />
      ))}
    </>
  );
}

function DotRow({
  pixels,
  cell,
  letter,
  separator,
}: {
  pixels: Pixel[];
  cell: number;
  letter: string;
  separator: string;
}) {
  return (
    <>
      {pixels.map((p, i) => (
        <circle
          key={i}
          cx={p.x}
          cy={p.y}
          r={p.sep ? cell * 0.44 : cell * 0.33}
          fill={p.sep ? separator : letter}
        />
      ))}
    </>
  );
}

// Fixed spectral weights so the bar clusters read as a real amplitude meter
// rather than random movement.
const BAR_WEIGHTS = [0.35, 0.62, 0.9, 1, 0.78, 0.52, 0.7, 0.95, 0.6, 0.42, 0.68, 0.3];

function BarCluster({ x, y, level, color }: { x: number; y: number; level: number; color: string }) {
  return (
    <>
      {BAR_WEIGHTS.map((weight, i) => {
        const height = 2 + weight * 55 * level;
        const barX = x + i * 13;
        return (
          <g key={i}>
            <rect x={barX} y={y - 57} width={9} height={57} rx={1} fill={color} opacity={0.1} />
            <rect
              x={barX}
              y={y - height}
              width={9}
              height={height}
              rx={1}
              fill={color}
              opacity={0.35 + weight * 0.45}
            />
          </g>
        );
      })}
    </>
  );
}

export function JarvisHud({
  state,
  level = 0,
  progress = 0,
  reducedMotion = false,
  onClick,
}: JarvisHudProps) {
  const label = STATE_LABEL[state];
  const accent = STATE_ACCENT[state];
  const offline = state === 'offline';
  const amplitude = Math.max(0, Math.min(1, level));
  const completion = Math.max(0, Math.min(1, progress));

  const wordmark = useMemo(() => centeredDotText('J.A.R.V.I.S.', 3.5, CY), []);
  const readout = useMemo(() => centeredDotText(label, 2.1, 62), [label]);

  const period = STATE_PERIOD[state];
  const spinning = !reducedMotion && period > 0;
  const spinStyle = {
    '--hud-period': `${period || 120}s`,
    animationPlayState: spinning ? 'running' : 'paused',
  } as SpinStyle;

  const gaugeCircumference = 2 * Math.PI * 286;

  return (
    <div
      className={`hud hud--${state}${onClick ? ' hud--interactive' : ''}`}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={(event) => {
        if (!onClick) return;
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          onClick();
        }
      }}
      aria-label={`J.A.R.V.I.S. heads-up display — ${label}${onClick ? ', activate microphone' : ''}`}
      style={spinStyle}
    >
      <svg className="hud__svg" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid meet">
        <defs>
          <radialGradient id="hud-core" cx="50%" cy="50%" r="50%" fx="38%" fy="32%">
            <stop offset="0%" stopColor="#3d8ecb" />
            <stop offset="38%" stopColor="#17548f" />
            <stop offset="74%" stopColor="#0a2540" />
            <stop offset="100%" stopColor="#040e1a" />
          </radialGradient>
          <radialGradient id="hud-halo" cx="50%" cy="50%" r="50%">
            <stop offset="45%" stopColor="rgba(40,120,200,0.30)" />
            <stop offset="72%" stopColor="rgba(28,86,150,0.14)" />
            <stop offset="100%" stopColor="rgba(0,0,0,0)" />
          </radialGradient>
          <linearGradient id="hud-sweep" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor={CYAN} stopOpacity="0" />
            <stop offset="100%" stopColor={CYAN} stopOpacity="0.55" />
          </linearGradient>
          <filter id="hud-bloom" x="-40%" y="-40%" width="180%" height="180%">
            <feGaussianBlur stdDeviation="3.4" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
          {/* Lighter halo so the LED dots stay crisp instead of smearing together. */}
          <filter id="hud-crystal" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur stdDeviation="1.5" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        <g className="hud__dim">
          <circle cx={CX} cy={CY} r={360} fill="url(#hud-halo)" />

          {/* Corner brackets frame the stage without implying any control. */}
          {[
            `M 60 130 L 60 60 L 130 60`,
            `M 1470 60 L 1540 60 L 1540 130`,
            `M 1540 770 L 1540 840 L 1470 840`,
            `M 130 840 L 60 840 L 60 770`,
          ].map((d, i) => (
            <path key={i} d={d} fill="none" stroke={GOLD} strokeWidth={2} opacity={0.5} />
          ))}

          <circle cx={CX} cy={CY} r={372} fill="none" stroke={CYAN} strokeWidth={1} opacity={0.16} />
          <g className="hud__spin">
            <RingTicks radius={352} count={120} length={7} majorEvery={10} color={CYAN} />
          </g>
          <g className="hud__spin hud__spin--reverse">
            <SegmentedArcs radius={326} count={4} sweep={70} color={GOLD} width={3} />
          </g>

          <circle
            cx={CX}
            cy={CY}
            r={306}
            fill="none"
            stroke={CYAN}
            strokeWidth={2}
            strokeDasharray="2 12"
            opacity={0.55}
          />

          {/* Data gauge: a real value — task progress while executing, otherwise
              live microphone amplitude. The track stays visible when both are 0. */}
          <circle cx={CX} cy={CY} r={286} fill="none" stroke="#123049" strokeWidth={6} />
          <circle
            cx={CX}
            cy={CY}
            r={286}
            fill="none"
            stroke={accent}
            strokeWidth={6}
            strokeLinecap="round"
            strokeDasharray={`${gaugeCircumference * (state === 'executing' ? completion : amplitude)} ${gaugeCircumference}`}
            transform={`rotate(-90 ${CX} ${CY})`}
            filter="url(#hud-bloom)"
          />
          <g className="hud__spin">
            <RingTicks radius={262} count={48} length={5} majorEvery={6} color={GOLD} width={1.3} />
          </g>
          <g className="hud__spin hud__spin--reverse">
            <SegmentedArcs radius={240} count={8} sweep={26} color={CYAN} width={2.5} />
          </g>

          {/* Radar dial. The sweep only turns while the assistant is actually working. */}
          <circle cx={CX} cy={CY} r={216} fill="none" stroke={CYAN} strokeWidth={1} opacity={0.3} />
          <g className="hud__spin hud__spin--sweep">
            {Array.from({ length: 14 }, (_, i) => (
              <path
                key={i}
                d={arcPath(216, -i * 3.2, -i * 3.2 - 3.2)}
                fill="none"
                stroke={CYAN}
                strokeWidth={i === 0 ? 2.4 : 1.4}
                opacity={0.5 * (1 - i / 16)}
              />
            ))}
            <line
              x1={CX}
              y1={CY}
              x2={polar(216, 0)[0]}
              y2={polar(216, 0)[1]}
              stroke={CYAN}
              strokeWidth={1.4}
              opacity={0.5}
            />
          </g>
          <RingTicks radius={192} count={36} length={4} majorEvery={9} color="#8fb6d6" width={1.2} />
        </g>

        {/* Centre: the sphere and its dot-matrix name. */}
        <circle cx={CX} cy={CY} r={184} fill="none" stroke={accent} strokeWidth={1} opacity={0.3} />
        <circle cx={CX} cy={CY} r={160} fill="url(#hud-core)" />
        <path
          d={arcPath(170, -60, 120)}
          fill="none"
          stroke={GOLD}
          strokeWidth={2.5}
          opacity={offline ? 0.15 : 0.75}
        />
        <path
          d={arcPath(170, 130, 250)}
          fill="none"
          stroke={CYAN}
          strokeWidth={2}
          opacity={0.5}
        />
        <g filter="url(#hud-crystal)" opacity={offline ? 0.3 : 1}>
          <DotRow pixels={wordmark} cell={3.5} letter="#f2fbff" separator={GOLD} />
        </g>

        {/* Top dotted data readout — the assistant's actual state. */}
        <g className="hud__readout">
          <line x1={CX - 300} y1={62} x2={CX - 150} y2={62} stroke={CYAN} strokeWidth={1} strokeDasharray="3 7" opacity={0.5} />
          <line x1={CX + 150} y1={62} x2={CX + 300} y2={62} stroke={CYAN} strokeWidth={1} strokeDasharray="3 7" opacity={0.5} />
          <g filter="url(#hud-crystal)">
            <DotRow pixels={readout} cell={2.1} letter={offline ? '#4a5f74' : accent} separator={GOLD} />
          </g>
        </g>

        {/* Side amplitude meters, driven by the live microphone level. */}
        <g opacity={offline ? 0.2 : 1}>
          <BarCluster x={CX - 560} y={CY + 40} level={amplitude} color={CYAN} />
          <BarCluster x={CX + 405} y={CY + 40} level={amplitude} color={GOLD} />
        </g>

        {/* Bottom readout rail. */}
        <g opacity={offline ? 0.2 : 0.75}>
          <path d={`M ${CX - 260} 812 L ${CX - 200} 812`} stroke={GOLD} strokeWidth={2} />
          <path d={`M ${CX + 200} 812 L ${CX + 260} 812`} stroke={GOLD} strokeWidth={2} />
          {Array.from({ length: 21 }, (_, i) => (
            <circle
              key={i}
              cx={CX - 180 + i * 18}
              cy={812}
              r={i % 5 === 0 ? 3 : 1.6}
              fill={i % 5 === 0 ? GOLD : CYAN}
              opacity={0.35 + (i % 5 === 0 ? 0.45 : 0.2)}
            />
          ))}
        </g>
      </svg>
    </div>
  );
}
