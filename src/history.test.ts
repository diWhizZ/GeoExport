import {describe, expect, it} from 'vitest';
import {loadHistory, saveHistory, parseCoordinates, type HistoryEntry} from './history';

const entry: HistoryEntry = {id:'1',createdAt:'2026-01-01T12:00:00Z',bounds:[21,52,21.001,52.001],location:'Warszawa',layerIds:['buildings'],format:'DXF',crs:'PL-1992 · EPSG:2180',interval:'1 m'};
describe('local history', () => {
  it('round trips valid configurations and caps history', () => {
    let data=''; const storage={getItem:()=>data,setItem:(_k:string,v:string)=>{data=v}};
    expect(saveHistory(storage,Array(60).fill(entry))).toBe(true);
    expect(loadHistory(storage)).toHaveLength(50);
    expect(loadHistory(storage)[0]).toEqual(entry);
  });
  it('ignores corrupted data and configurations with invalid CRS or bounds', () => {
    expect(loadHistory({getItem:()=>'{oops'})).toEqual([]);
    expect(loadHistory({getItem:()=>JSON.stringify([entry,{...entry,crs:'EPSG:9999'},{...entry,bounds:[21,52,20,53]}])})).toEqual([entry]);
  });
  it('handles disabled storage without failing export', () => {
    expect(saveHistory({setItem:()=>{throw Error('quota')}},[entry])).toBe(false);
  });
});
describe('coordinate parsing', () => {
  it('parses latitude and longitude in explicit decimal notation', () => {
    expect(parseCoordinates('52.2297, 21.0122')).toEqual([52.2297,21.0122]);
    expect(parseCoordinates('-12.5; -45.1')).toEqual([-12.5,-45.1]);
  });
  it('rejects malformed, out of bounds and ambiguous inputs', () => {
    expect(parseCoordinates('52,23, 21,01')).toBeNull();
    expect(parseCoordinates('91, 21')).toBeNull();
    expect(parseCoordinates('Warszawa')).toBeNull();
    expect(parseCoordinates('52, 180')).toBeNull();
  });
});
