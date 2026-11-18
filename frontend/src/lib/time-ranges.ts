// Time ranges for the create form: reading clock times the way people type them,
// and cutting a long stream into back-to-back batches. The server validates
// everything again; this only keeps the form honest and plans the batches.

export const MIN_BATCH_RANGE_SECONDS = 30;
export const MIN_BATCH_MINUTES = 5;
export const MAX_BATCH_MINUTES = 600;
// "From here to the end" is sent as a very late end; the server clamps it to the video.
export const OPEN_END_SECONDS = 24 * 3600;

const NUMBER = /^\d+(?:\.\d+)?$/;
const UNITS = /^(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?$/i;

/** Seconds from "754", "12:34", "1:02:03" or "1h2m3s"; null when it is not a time. */
export function parseClockTime(value: string): number | null {
  const text = value.trim();
  if (!text) return null;
  if (text.includes(":")) {
    const parts = text.split(":");
    if (parts.length > 3 || parts.some((part) => !NUMBER.test(part))) return null;
    const numbers = parts.map(Number);
    if (numbers[numbers.length - 1] >= 60) return null;
    if (numbers.length === 3 && numbers[1] >= 60) return null;
    return numbers.reduce((total, part) => total * 60 + part, 0);
  }
  if (NUMBER.test(text)) return Number(text);
  const match = UNITS.exec(text.replace(/\s+/g, ""));
  if (!match || !match.slice(1).some(Boolean)) return null;
  const [hours, minutes, seconds] = match.slice(1).map((part) => Number(part ?? 0));
  return hours * 3600 + minutes * 60 + seconds;
}

/** 4210 -> "1:10:10". */
export function formatClock(totalSeconds: number): string {
  const total = Math.max(0, Math.round(totalSeconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

export type SeriesRange = { start: number; end: number };

/**
 * Back-to-back batches of at most `chunkSeconds` covering [start, end].
 * A leftover shorter than the server's minimum is dropped rather than sent.
 */
export function planBatches(start: number, end: number, chunkSeconds: number): SeriesRange[] {
  if (!(chunkSeconds > 0) || !(end > start)) return [];
  const batches: SeriesRange[] = [];
  for (let cursor = start; cursor < end; cursor += chunkSeconds) {
    const batchEnd = Math.min(cursor + chunkSeconds, end);
    if (batchEnd - cursor >= MIN_BATCH_RANGE_SECONDS) batches.push({ start: cursor, end: batchEnd });
  }
  return batches;
}

/** "1:10:00-2:20:00", the form the API reads. */
export function rangeText(range: SeriesRange): string {
  return `${formatClock(range.start)}-${formatClock(range.end)}`;
}
