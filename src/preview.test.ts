import {describe,it,expect} from 'vitest';
import {classifyFeature,previewCollection} from './preview';
describe('preview layers',()=>{
  it('classifies using active layers rather than a non-selected tag',()=>{
    expect(classifyFeature({highway:'footway',railway:'tram'},['rail'])).toBe('rail');
    expect(classifyFeature({layer:'contours'},['contours'])).toBe('contours');
    expect(classifyFeature({natural:'water'},['water'])).toBe('water');
  });
  it('does not modify exported properties while labelling preview features',()=>{
    const feature={type:'Feature' as const, geometry:{type:'Point' as const,coordinates:[21,52]},properties:{building:'yes'}};
    const result=previewCollection([feature],['buildings']);
    expect(result.features[0].properties?.geoexport_category).toBe('buildings');
    expect(feature.properties).toEqual({building:'yes'});
  });
});
