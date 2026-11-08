/** Local-only export history. Entries are configurations, not stored files or accounts. */
export type Bounds = [number, number, number, number];
export type HistoryEntry = {
  id: string;
  createdAt: string;
  bounds: Bounds;
  location: string;
  layerIds: string[];
  format: 'GeoJSON' | 'DXF' | 'SVG' | 'GeoTIFF' | 'OBJ' | 'glTF';
  crs: string;
  interval: string;
};
const key = 'geoexport.export-history.v1';
const formats = new Set(['GeoJSON', 'DXF', 'SVG', 'GeoTIFF', 'OBJ', 'glTF']);
const layers = new Set(['buildings', 'roads', 'water', 'green', 'rail', 'contours']);
const projected = new Set(['PL-1992 · EPSG:2180', 'UTM 34N · EPSG:32634']);
const intervals = new Set(['0,5 m', '1 m', '2 m', '5 m']);

function validEntry(v: unknown): v is HistoryEntry {
  if (!v || typeof v !== 'object') return false;
  const x = v as Record<string, unknown>;
  if (typeof x.id !== 'string' || x.id.length > 100 || typeof x.createdAt !== 'string' || !Number.isFinite(Date.parse(x.createdAt)) || typeof x.location !== 'string' || x.location.length > 500 || typeof x.format !== 'string' || !formats.has(x.format) || typeof x.crs !== 'string' || typeof x.interval !== 'string' || !intervals.has(x.interval)) return false;
  if ((x.format === 'GeoJSON' || x.format === 'GeoTIFF') ? x.crs !== 'WGS 84 · EPSG:4326' : !projected.has(x.crs)) return false;
  if (!Array.isArray(x.layerIds) || x.layerIds.length > layers.size || !x.layerIds.every(id => typeof id === 'string' && layers.has(id))) return false;
  if (!Array.isArray(x.bounds) || x.bounds.length !== 4 || !x.bounds.every(n => typeof n === 'number' && Number.isFinite(n))) return false;
  const [w,s,e,n] = x.bounds;
  return w >= -180 && e <= 180 && s >= -85 && n <= 85 && w < e && s < n;
}

export function loadHistory(storage: Pick<Storage, 'getItem'>): HistoryEntry[] {
  try {
    const entries: unknown = JSON.parse(storage.getItem(key) || '[]');
    return Array.isArray(entries) ? entries.filter(validEntry).slice(0, 50) : [];
  } catch { return []; }
}

export function saveHistory(storage: Pick<Storage, 'setItem'>, entries: HistoryEntry[]): boolean {
  try { storage.setItem(key, JSON.stringify(entries.slice(0, 50))); return true; }
  catch { return false; }
}

/** Input order is latitude, longitude. Only explicit decimal-degree pairs are accepted. */
export function parseCoordinates(input: string): [number, number] | null {
  const match = input.trim().match(/^([+-]?(?:\d+(?:\.\d*)?|\.\d+))\s*[,;]\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+))$/);
  if (!match) return null;
  const lat = Number(match[1]), lng = Number(match[2]);
  if (!Number.isFinite(lat) || !Number.isFinite(lng) || lat < -84.99725 || lat > 84.99725 || lng < -179.995 || lng > 179.995) return null;
  return [lat, lng];
}
