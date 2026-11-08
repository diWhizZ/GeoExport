import type { Feature, FeatureCollection, Geometry } from 'geojson';

/** Styling labels reflect the chosen layers, not merely tag priority. */
export function classifyFeature(properties: Record<string, unknown>, selected: string[]): string {
  if (properties.layer === 'contours' && selected.includes('contours')) return 'contours';
  if ('building' in properties && selected.includes('buildings')) return 'buildings';
  if ('highway' in properties && selected.includes('roads')) return 'roads';
  if ('railway' in properties && selected.includes('rail')) return 'rail';
  if (('waterway' in properties || properties.natural === 'water' || properties.landuse === 'reservoir') && selected.includes('water')) return 'water';
  if (selected.includes('green') && ('natural' in properties || 'landuse' in properties || 'leisure' in properties)) return 'green';
  return 'other';
}
export function previewCollection(features: Feature<Geometry>[], selected: string[]): FeatureCollection<Geometry> {
  return {type:'FeatureCollection', features: features.map(feature => ({...feature, properties:{...feature.properties, geoexport_category:classifyFeature(feature.properties || {}, selected)}}))};
}
