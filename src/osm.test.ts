import {afterEach, describe, expect, it, vi} from 'vitest';
import {fetchOsm} from './osm';

afterEach(() => vi.unstubAllGlobals());
describe('adapter Overpass', () => {
  it('exports OSM features with attribution, data version and clipped geometry', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ok:true, json: async () => ({
      osm3s:{timestamp_osm_base:'2025-01-01T00:00:00Z'}, elements:[
        {type:'node',id:1,lat:52.23,lon:21.001},
        {type:'node',id:2,lat:52.23,lon:21.02},
        {type:'way',id:3,nodes:[1,2],tags:{highway:'residential',name:'Test'}}
      ]
    })});
    vi.stubGlobal('fetch', fetchMock);
    const data = await fetchOsm([21,52.22,21.01,52.24], ['roads','contours']);
    expect(data.features).toHaveLength(1);
    expect(data.features[0].geometry.type).toBe('LineString');
    expect(JSON.stringify(data.features[0].geometry)).toContain('21.01');
    expect(data.metadata.attribution).toBe('© OpenStreetMap contributors');
    expect(data.metadata.data_timestamp).toBe('2025-01-01T00:00:00Z');
    expect(data.metadata.omitted_layers).toEqual(['contours']);
    expect(fetchMock.mock.calls[0][1].body.get('data')).toContain('["highway"]');
  });
  it('rejects an incomplete Overpass response rather than exporting it', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ok:true, json:async()=>({remark:'runtime error'})}));
    await expect(fetchOsm([21,52,21.01,52.01], ['buildings'])).rejects.toThrow('runtime error');
  });
  it('supports contours alone without issuing an OSM request', async () => {
    const mock=vi.fn();vi.stubGlobal('fetch',mock);
    const result=await fetchOsm([21,52,21.01,52.01], ['contours']);
    expect(result.features).toEqual([]);expect(result.metadata.omitted_layers).toEqual(['contours']);expect(mock).not.toHaveBeenCalled();
  });
  it('requires at least one layer', async () => {
    await expect(fetchOsm([21,52,21.01,52.01], [])).rejects.toThrow('co najmniej jedną warstwę');
  });
});
