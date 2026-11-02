/**
 * Wake-word matching that survives what a browser recogniser actually returns.
 *
 * The old rule was `transcript.includes('hey jarvis')`, and his own transcripts
 * read "he Jarvis", "hi jarvis", "hijabis generate an image" — none of which
 * contain that exact string, so the wake word looked broken while the microphone
 * was fine. This accepts the call name with small spelling drift instead.
 */

// Words people put in front of the name, and words the recogniser substitutes
// for "hey". They are dropped before the name is compared.
const CALL_SIGNS = [
  'hey', 'hi', 'he', 'hai', 'hii', 'hello', 'halo', 'oi', 'yo', 'ok', 'okay',
  'a', 'the', 'are', 'is', 'yes', 'no', 'eh', 'ah', 'oh', 'hu',
];

function normalize(text: string): string {
  return text
    .toLowerCase()
    // "J.A.R.V.I.S." is a spelling a recogniser really produces.
    .replace(/(?<=[a-z0-9])\.(?=[a-z0-9])/g, '')
    .replace(/[^a-z0-9\s]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function editDistance(a: string, b: string): number {
  if (a === b) return 0;
  let previous = Array.from({ length: b.length + 1 }, (_, i) => i);
  for (let i = 1; i <= a.length; i += 1) {
    const current = [i];
    for (let j = 1; j <= b.length; j += 1) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      current[j] = Math.min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + cost);
    }
    previous = current;
  }
  return previous[b.length];
}

/** The name is the last word of the configured phrase; the rest is a call sign. */
function callName(phrase: string): string {
  const words = normalize(phrase).split(' ').filter(Boolean);
  return words.length ? words[words.length - 1] : '';
}

function toleranceFor(name: string): number {
  if (name.length <= 4) return 1;
  if (name.length <= 6) return 2;
  return 3;
}

function tokenIsName(token: string, name: string): boolean {
  if (!name) return false;
  if (token === name) return true;
  // "hijarvis" / "okayjarvis": the call sign got glued to the name.
  if (token.includes(name)) return true;
  if (token.length < 4) return false;
  const tolerance = toleranceFor(name);
  const candidates = [token];
  for (const sign of CALL_SIGNS) {
    // "hijabis" is "hi" + "jabis"; the first letter only lines up once the
    // call sign is off, so every stripped form is a candidate.
    if (token.length > name.length && token.startsWith(sign)) {
      candidates.push(token.slice(sign.length));
    }
  }
  return candidates.some(
    (form) => form.length >= 4 && form[0] === name[0] && editDistance(form, name) <= tolerance,
  );
}

export interface WakeMatch {
  matched: boolean;
  /** What was heard after the wake word, ready to become the command. */
  remainder: string;
}

export function matchWakeWord(transcript: string, phrase: string): WakeMatch {
  const clean = normalize(transcript);
  const spoken = normalize(phrase);
  if (!clean || !spoken) return { matched: false, remainder: '' };

  const whole = clean.indexOf(spoken);
  if (whole !== -1) {
    return { matched: true, remainder: clean.slice(whole + spoken.length).trim() };
  }

  const name = callName(phrase);
  const words = clean.split(' ');
  for (let index = 0; index < words.length; index += 1) {
    if (tokenIsName(words[index], name)) {
      return { matched: true, remainder: words.slice(index + 1).join(' ').trim() };
    }
  }
  return { matched: false, remainder: '' };
}

/** Drop a leading wake word from a collected turn, fuzzy matching included. */
export function stripWakeWord(text: string, phrase: string): string {
  const hit = matchWakeWord(text, phrase);
  return hit.matched ? hit.remainder : text.trim();
}
