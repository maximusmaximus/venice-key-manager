export const TIERS = [
  { value: 'xs', label: 'XS — ultra fast / lowest cost' },
  { value: 's', label: 'S — fast & efficient' },
  { value: 'm', label: 'M — balanced' },
  { value: 'l', label: 'L — deep reasoning' },
  { value: 'xl', label: 'XL — flagship' },
];
export const PERIODS = [
  { value: 'DAY', label: 'Per day' },
  { value: 'WEEK', label: 'Per week' },
  { value: 'MONTH', label: 'Per month' },
];
export const KEY_TYPES = [
  { value: 'INFERENCE', label: 'Inference' },
  { value: 'ADMIN', label: 'Admin' },
];

/** Convert a <input type="datetime-local"> value to an ISO UTC string (or ''). */
export function localToIso(v) {
  if (!v) return '';
  const d = new Date(v);
  return isNaN(d.getTime()) ? '' : d.toISOString();
}
