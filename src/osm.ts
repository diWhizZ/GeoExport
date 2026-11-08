import osmtogeojson from 'osmtogeojson';
import { bboxClip, booleanPointInPolygon, bboxPolygon } from '@turf/turf';
import type { Feature, FeatureCollection, Geometry } from 'geojson';

type Bounds = [number, number, number, number];
const filters: Record<string, string[]> = {
  buildings: ['["building"]'],
  roads: ['["highway"]'],
  water: ['["natural"="water"]', '["waterway"]', '["landuse"="reservoir"]'],
  green: ['["leisure"~"^(park|garden|nature_reserve)$"]', '["landuse"~"^(forest|grass|meadow|recreation_ground)$"]', '["natural"~"^(wood|grassland|scrub|tree)$"]'],
  rail: ['["railway"~"^(rail|tram|light_rail|subway|narrow_gauge)$"]'],
};

/** Public Overpass instance: convenience for the prototype, not a production SLA. */
export async function fetchOsm(bounds: Bounds, layerIds: string[], signal?: AbortSignal) {
  const available = layerIds.filter(id => filters[id]);
  if (!available.length) {
    if (!layerIds.includes('contours')) throw new Error('Wybierz co najmniej jedną warstwę.');
    return {type: 'FeatureCollection' as const, features: [] as Feature<Geometry>[], metadata: {
      source: 'No OSM data requested', attribution: '© OpenStreetMap contributors',
      license: 'ODbL 1.0', license_url: 'https://www.openstreetmap.org/copyright',
      retrieved_at: new Date().toISOString(), data_timestamp: null as string | null,
      bounds_wgs84: bounds, layers: [] as string[], omitted_layers: ['contours'], warnings: [] as string[],
      disclaimer: 'Dane poglądowe; nie zastępują pomiarów geodezyjnych.',
    }};
  }
  const bbox = `${bounds[1]},${bounds[0]},${bounds[3]},${bounds[2]}`;
  const queries = available.flatMap(id => filters[id].map(filter => `nwr${filter}(${bbox});`)).join('');
  const query = `[out:json][timeout:50];(${queries});out body;>;out skel qt;`;
  const response = await fetch('https://overpass-api.de/api/interpreter', {
    method: 'POST', body: new URLSearchParams({data: query}), signal,
  });
  if (!response.ok) throw new Error(`Serwer Overpass zwrócił błąd ${response.status}. Spróbuj ponownie później.`);
  const osm = await response.json();
  if (osm.remark) throw new Error(`Serwer Overpass nie ukończył zapytania: ${osm.remark}`);
  const converted = osmtogeojson(osm) as FeatureCollection;
  const polygon = bboxPolygon(bounds);
  const features: Feature<Geometry>[] = [];
  const warnings: string[] = [];
  for (const feature of converted.features) {
    try {
      if (!feature.geometry) throw new Error('brak geometrii');
      const geometry = feature.geometry;
      let clipped: Feature<Geometry> | null = feature as Feature<Geometry>;
      if (geometry.type === 'Point') {
        if (!booleanPointInPolygon(feature as Feature<GeoJSON.Point>, polygon)) clipped = null;
      } else if (['LineString', 'MultiLineString', 'Polygon', 'MultiPolygon'].includes(geometry.type)) {
        clipped = bboxClip(feature as any, bounds) as Feature<Geometry>;
        if (!clipped.geometry || !(clipped.geometry as any).coordinates?.length) clipped = null;
      } else { throw new Error(`nieobsługiwany typ geometrii ${geometry.type}`); }
      if (clipped) features.push(clipped);
    } catch (e) {
      warnings.push(`${feature.id ?? 'obiekt'}: ${e instanceof Error ? e.message : 'błąd geometrii'}`);
    }
  }
  return {
    type: 'FeatureCollection' as const,
    features,
    // RFC 7946 GeoJSON coordinates are always WGS 84 longitude/latitude.
    metadata: {
      source: 'OpenStreetMap via Overpass API', attribution: '© OpenStreetMap contributors',
      license: 'ODbL 1.0', license_url: 'https://www.openstreetmap.org/copyright',
      retrieved_at: new Date().toISOString(), data_timestamp: osm.osm3s?.timestamp_osm_base ?? null,
      bounds_wgs84: bounds, layers: available, omitted_layers: layerIds.filter(id => !filters[id]), warnings,
      disclaimer: 'Dane poglądowe; nie zastępują pomiarów geodezyjnych.',
    },
  };
}
