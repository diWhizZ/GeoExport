import numpy as np
from fastapi.testclient import TestClient
from rasterio.transform import from_origin
from main import app, contours_from_grid, tile_url

client = TestClient(app)

def test_tile_names():
    assert 'N52_00_E021_00_DEM' in tile_url(52, 21)
    assert 'S01_00_W002_00_DEM' in tile_url(-1, -2)

def test_contours_and_mask():
    grid = np.ma.array([[0., 1., 2.], [0., 1., 2.], [0., 1., 2.]], mask=[[0,0,0],[0,0,0],[0,0,1]])
    result = contours_from_grid(grid, from_origin(21, 52, .001, .001), 1)
    assert any(f['properties']['elevation_m'] == 1 for f in result)
    assert all(f['geometry']['type'] == 'LineString' for f in result)

def test_area_limit_and_invalid_bounds():
    assert client.post('/api/elevation/contours', json={'bounds':[21,52,22,53]}).status_code == 422
    assert client.post('/api/elevation/contours', json={'bounds':[21,53,22,52]}).status_code == 422

def test_endpoint_with_mock_grid(monkeypatch):
    monkeypatch.setattr('main.read_dem_mosaic', lambda *args: (np.ma.array([[0., 2.],[0., 2.]]), from_origin(21, 52, .001, .001), ['test-tile']))
    response = client.post('/api/elevation/contours', json={'bounds':[21,52,21.001,52.001], 'interval':1})
    assert response.status_code == 200
    assert response.json()['metadata']['model_type'] == 'DSM'
    assert len(response.json()['features']) > 0

def test_dxf_export_layers_and_bad_features():
    from io import StringIO
    import ezdxf
    collection = {'type':'FeatureCollection','features':[
        {'type':'Feature','geometry':{'type':'Polygon','coordinates':[[[21,52],[21.001,52],[21.001,52.001],[21,52]]]},'properties':{'building':'yes'}},
        {'type':'Feature','geometry':{'type':'LineString','coordinates':[[21,52],[21.001,52.001]]},'properties':{'layer':'contours','elevation_m':100}},
        {'type':'Feature','geometry':None,'properties':{}}],
        'metadata':{'retrieved_at':'2025-01-01'}}
    response = client.post('/api/export/dxf', json={'collection':collection,'crs':'EPSG:2180'})
    assert response.status_code == 200
    assert response.headers['x-geoexport-skipped'] == '1'
    doc = ezdxf.read(StringIO(response.content.decode('utf-8')))
    entities = list(doc.modelspace())
    assert any(e.dxf.layer == 'BUDYNKI' and e.dxftype() == 'LWPOLYLINE' for e in entities)
    assert any(e.dxf.layer == 'POZIOMICE' and e.dxf.elevation == 100 for e in entities)
    assert any(e.dxf.layer == 'METADANE' and 'OpenStreetMap' in e.dxf.text for e in entities if e.dxftype() == 'TEXT')

def test_svg_export_has_editable_layers_and_metadata():
    from xml.etree import ElementTree as ET
    collection = {'type':'FeatureCollection', 'metadata':{'attribution':'© OpenStreetMap contributors'}, 'features':[
        {'type':'Feature','geometry':{'type':'Polygon','coordinates':[[[21,52],[21.001,52],[21.001,52.001],[21,52]]]},'properties':{'building':'yes'}},
        {'type':'Feature','geometry':None,'properties':{}}]}
    response = client.post('/api/export/svg', json={'collection':collection, 'crs':'EPSG:2180'})
    assert response.status_code == 200
    assert response.headers['x-geoexport-skipped'] == '1'
    root = ET.fromstring(response.content)
    ns = {'s':'http://www.w3.org/2000/svg'}
    assert root.find("s:g[@id='BUDYNKI']/s:path", ns) is not None
    assert 'OpenStreetMap contributors' in root.find('s:metadata', ns).text
    assert any('OpenStreetMap contributors' in (t.text or '') for t in root.findall('s:text', ns))

def test_3d_models_have_terrain_buildings_and_attribution(monkeypatch):
    from io import BytesIO
    import json, zipfile, trimesh
    monkeypatch.setattr('main.read_dem_mosaic', lambda *args: (np.ma.array([[100.,101.,102.],[100.,101.,102.],[100.,101.,102.]]), from_origin(21,52.002,.001,.001), ['test-tile']))
    collection={'type':'FeatureCollection','metadata':{},'features':[{'type':'Feature','geometry':{'type':'Polygon','coordinates':[[[21.0002,52.0002],[21.0008,52.0002],[21.0008,52.0008],[21.0002,52.0002]]]},'properties':{'building':'yes','height':'12'}}]}
    bounds_by_format = {}
    for fmt in ['OBJ','glTF']:
        response=client.post('/api/export/model',json={'collection':collection,'bounds':[21,52,21.001,52.001], 'crs':'EPSG:2180','format':fmt})
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            meta=json.loads(archive.read('metadata.json'))
            assert meta['buildings']==1 and meta['estimated_building_heights']==0
            assert 'OpenStreetMap' in meta['attribution']
            name='model.obj' if fmt=='OBJ' else 'model.glb'
            model=trimesh.load(BytesIO(archive.read(name)), file_type=name.split('.')[-1], force='scene')
            assert len(model.geometry)>=1
            bounds_by_format[fmt] = model.bounds
            assert meta['axes'] == ('X=east, Y=up, Z=south' if fmt == 'glTF' else 'X=east, Y=north, Z=up')
    obj, glb = bounds_by_format['OBJ'], bounds_by_format['glTF']
    assert np.allclose(glb[:,0], obj[:,0], atol=0.01)
    assert np.allclose(glb[:,1], obj[:,2], atol=0.01)
    assert np.allclose(glb[:,2], -obj[::-1,1], atol=0.01)

def test_geotiff_contains_crs_and_license(monkeypatch):
    from rasterio.io import MemoryFile
    monkeypatch.setattr('main.read_dem_mosaic', lambda *args: (np.ma.array([[100.,101.],[102.,103.]]), from_origin(21,52.002,.001,.001), ['test-tile']))
    r=client.post('/api/export/geotiff',json={'bounds':[21,52,21.001,52.001]})
    assert r.status_code==200
    with MemoryFile(r.content) as mem:
        with mem.open() as raster:
            assert raster.crs.to_epsg()==4326
            assert raster.read(1)[0,0]==100
            assert 'Copernicus DEM' in raster.tags()['attribution']

def test_model_rejects_area_over_limit():
    r=client.post('/api/export/model',json={'collection':{'type':'FeatureCollection','features':[]},'bounds':[21,52,22,53], 'crs':'EPSG:2180','format':'OBJ'})
    assert r.status_code==422

def test_mosaic_crossing_dem_tiles():
    import main
    from unittest.mock import patch
    def fake_window(url, bounds):
        if 'E020' in url:
            return np.ma.array(np.full((4,4), 10.)), from_origin(20.998,52.002,.0005,.0005)
        return np.ma.array(np.full((4,4), 20.)), from_origin(21.,52.002,.0005,.0005)
    bounds=[20.999,52.0005,21.001,52.0015]
    with patch('main.read_window', side_effect=fake_window):
        data, transform, tiles = main.read_dem_mosaic(bounds)
    assert len(tiles) == 2
    assert data.shape == (2,4)
    assert np.all(data[:, :2] == 10) and np.all(data[:, 2:] == 20)
    assert transform.c == bounds[0] and transform.f == bounds[3]

def test_missing_tile_fails_instead_of_fabricating_data(monkeypatch):
    import main
    monkeypatch.setattr('main.read_window', lambda *args: None)
    with __import__('pytest').raises(ValueError, match='Brak danych'):
        main.read_dem_mosaic([20.999,52.0005,21.001,52.0015])

def test_rejects_very_thin_extreme_bounds_before_reading():
    # Area can be below 1 km² while spanning an enormous number of 1° tiles.
    r=client.post('/api/export/geotiff', json={'bounds':[-100,52,100,52.000000001]})
    assert r.status_code==422

def test_cross_tile_export_reads_actual_geotiff_windows(tmp_path, monkeypatch):
    import rasterio
    from rasterio.io import MemoryFile
    from pathlib import Path
    import main
    paths={}
    for longitude, value in [(20,10),(21,20)]:
        path=tmp_path / f'{longitude}.tif'
        with rasterio.open(path, 'w', driver='GTiff', height=4, width=4, count=1, dtype='float32', crs='EPSG:4326', transform=from_origin(20.998 if longitude==20 else 21,52.002,.0005,.0005), nodata=-9999) as dst:
            dst.write(np.full((4,4),value,dtype='float32'),1)
        paths[longitude]=str(path)
    monkeypatch.setattr('main.tile_url',lambda lat,lon: paths[lon])
    bounds=[20.999,52.0005,21.001,52.0015]
    result=client.post('/api/export/geotiff',json={'bounds':bounds})
    assert result.status_code==200, result.text
    with MemoryFile(result.content) as mem:
        with mem.open() as raster:
            data=raster.read(1,masked=True)
            assert data.shape==(2,4)
            assert np.all(data[:,:2]==10) and np.all(data[:,2:]==20)
            assert len(__import__('json').loads(raster.tags()['source_tiles']))==2
    model=client.post('/api/export/model',json={'collection':{'type':'FeatureCollection','features':[]}, 'bounds':bounds, 'crs':'EPSG:2180','format':'glTF'})
    assert model.status_code==200, model.text

def test_nodata_not_filled_in_mosaic(monkeypatch):
    import main
    left=np.ma.array(np.full((4,4),10.), mask=False)
    right=np.ma.array(np.full((4,4),20.), mask=False)
    right.mask[1,1]=True
    def window(url, bounds):
        if 'E020' in url: return left, from_origin(20.998,52.002,.0005,.0005)
        return right, from_origin(21.,52.002,.0005,.0005)
    monkeypatch.setattr('main.read_window',window)
    data,_,_=main.read_dem_mosaic([20.999,52.0005,21.001,52.0015])
    assert np.ma.count_masked(data)==1
    assert data.mask[0,3]

def test_tiny_grid_contours_returns_explicit_validation_error(monkeypatch):
    monkeypatch.setattr('main.read_dem_mosaic', lambda *args: (np.ma.array([[5.]]), from_origin(21,52,.001,.001), ['tile']))
    response=client.post('/api/elevation/contours',json={'bounds':[21,52,21.00001,52.00001],'interval':1})
    assert response.status_code == 422
    assert 'Zbyt mały obszar' in response.json()['detail']
