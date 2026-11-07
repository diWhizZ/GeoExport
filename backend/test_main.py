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
    monkeypatch.setattr('main.read_window', lambda *args: (np.ma.array([[0., 2.],[0., 2.]]), from_origin(21, 52, .001, .001)))
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
    monkeypatch.setattr('main.read_window', lambda *args: (np.ma.array([[100.,101.,102.],[100.,101.,102.],[100.,101.,102.]]), from_origin(21,52.002,.001,.001)))
    collection={'type':'FeatureCollection','metadata':{},'features':[{'type':'Feature','geometry':{'type':'Polygon','coordinates':[[[21.0002,52.0002],[21.0008,52.0002],[21.0008,52.0008],[21.0002,52.0002]]]},'properties':{'building':'yes','height':'12'}}]}
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

def test_geotiff_contains_crs_and_license(monkeypatch):
    from rasterio.io import MemoryFile
    monkeypatch.setattr('main.read_window', lambda *args: (np.ma.array([[100.,101.],[102.,103.]]), from_origin(21,52.002,.001,.001)))
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

def test_model_rejects_area_crossing_dem_tiles(monkeypatch):
    r=client.post('/api/export/model',json={'collection':{'type':'FeatureCollection','features':[]},'bounds':[20.999,52.0,21.001,52.001], 'crs':'EPSG:2180','format':'OBJ'})
    assert r.status_code==422
    assert 'kafla' in r.json()['detail']
