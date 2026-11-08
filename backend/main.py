"""Small elevation adapter for Copernicus DEM GLO-30 (DSM, not bare-earth DTM)."""
import math
import threading
from datetime import datetime, timezone

import numpy as np
import rasterio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator
from pyproj import Geod
from rasterio.windows import Window, from_bounds
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling
from skimage.measure import find_contours

app = FastAPI(title='GeoExport elevation adapter', docs_url=None, redoc_url=None)
_slots = threading.BoundedSemaphore(2)
_geod = Geod(ellps='WGS84')

class ElevationRequest(BaseModel):
    bounds: tuple[float, float, float, float]
    interval: float = Field(default=5, ge=0.5, le=100)

    @model_validator(mode='after')
    def validate_bounds(self):
        west, south, east, north = self.bounds
        if not all(math.isfinite(x) for x in self.bounds) or not (-180 <= west < east <= 180 and -85 <= south < north <= 85):
            raise ValueError('Niepoprawny prostokąt WGS 84')
        area, _ = _geod.polygon_area_perimeter([west,east,east,west], [south,south,north,north])
        if abs(area) > 1_000_000:
            raise ValueError('Obszar przekracza limit 1 km²')
        tile_count = (math.ceil(east) - math.floor(west)) * (math.ceil(north) - math.floor(south))
        if tile_count > 16:
            raise ValueError('Obszar przecina zbyt wiele kafli DEM (maksymalnie 16)')
        return self

def tile_url(lat: int, lon: int) -> str:
    lat_name = f'{"N" if lat >= 0 else "S"}{abs(lat):02d}'
    lon_name = f'{"E" if lon >= 0 else "W"}{abs(lon):03d}'
    name = f'Copernicus_DSM_COG_10_{lat_name}_00_{lon_name}_00_DEM'
    return f'https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif'

def read_window(url, bounds):
    # Read a small padded window so resampling near tile and selection edges has data.
    with rasterio.Env(GDAL_HTTP_TIMEOUT='30', GDAL_HTTP_CONNECTTIMEOUT='10', GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', CPL_VSIL_CURL_ALLOWED_EXTENSIONS='.tif'):
        with rasterio.open(url) as ds:
            if ds.crs is None or ds.crs.to_epsg() != 4326:
                raise ValueError('Nieoczekiwany układ współrzędnych kafla DEM')
            raw = from_bounds(*bounds, transform=ds.transform)
            col0 = max(0, math.floor(raw.col_off) - 1)
            row0 = max(0, math.floor(raw.row_off) - 1)
            col1 = min(ds.width, math.ceil(raw.col_off + raw.width) + 1)
            row1 = min(ds.height, math.ceil(raw.row_off + raw.height) + 1)
            if col1 <= col0 or row1 <= row0:
                return None
            window = Window(col0, row0, col1-col0, row1-row0)
            if window.width * window.height > 150000:
                raise ValueError('Siatka terenu jest zbyt duża')
            return ds.read(1, window=window, masked=True), ds.window_transform(window)

def read_dem_mosaic(bounds):
    """Combine intersecting COG windows onto a single WGS84 grid without filling nodata."""
    west, south, east, north = bounds
    grids, tiles = [], []
    for lat in range(math.floor(south), math.ceil(north)):
        for lon in range(math.floor(west), math.ceil(east)):
            url = tile_url(lat, lon)
            part = (max(west, lon), max(south, lat), min(east, lon+1), min(north, lat+1))
            grid = read_window(url, part)
            if grid is None:
                raise ValueError('Brak danych w jednym z kafli DEM')
            grids.append(grid)
            tiles.append(url)
    if not grids:
        raise ValueError('Brak kafli DEM dla wybranego obszaru')
    # At the equator and near latitude-band transitions COG pixel widths may differ.
    # Use the finest resolution among the source tiles, nearest-neighbour sampling.
    res_x = min(abs(transform.a) for _, transform in grids)
    res_y = min(abs(transform.e) for _, transform in grids)
    if not (math.isfinite(res_x) and math.isfinite(res_y) and res_x > 0 and res_y > 0):
        raise ValueError('Niepoprawna rozdzielczość DEM')
    width, height = max(1, math.ceil((east-west)/res_x - 1e-9)), max(1, math.ceil((north-south)/res_y - 1e-9))
    if width * height > 100000 or max(width, height) > 100000:
        raise ValueError('Siatka terenu jest zbyt duża')
    target_transform = from_origin(west, north, res_x, res_y)
    mosaic = np.full((height, width), np.nan, dtype=np.float32)
    for data, transform in grids:
        values = np.ma.asarray(data, dtype=np.float32).filled(np.nan)
        temp = np.full_like(mosaic, np.nan)
        reproject(values, temp, src_transform=transform, src_crs='EPSG:4326', src_nodata=np.nan,
                  dst_transform=target_transform, dst_crs='EPSG:4326', dst_nodata=np.nan,
                  resampling=Resampling.nearest)
        valid = np.isfinite(temp)
        mosaic[valid] = temp[valid]
    if not np.isfinite(mosaic).any():
        raise ValueError('Brak poprawnych danych DEM w obszarze')
    return np.ma.masked_invalid(mosaic), target_transform, tiles

def contours_from_grid(data, transform, interval):
    values = np.ma.asarray(data, dtype=float).filled(np.nan)
    if not np.isfinite(values).any():
        return []
    if min(values.shape) < 2:
        raise ValueError('Zbyt mały obszar do wyznaczenia poziomic z tej siatki DEM')
    low, high = float(np.nanmin(values)), float(np.nanmax(values))
    first = math.ceil(low / interval)
    last = math.floor(high / interval)
    if last - first > 1000:
        raise ValueError('Zbyt wiele poziomic dla wybranego interwału')
    features = []
    for i in range(first, last + 1):
        level = i * interval
        for line in find_contours(values, level, mask=np.isfinite(values)):
            if len(line) < 2:
                continue
            # find_contours returns row/column in pixel-center coordinates.
            coordinates = [list(transform @ (float(col) + 0.5, float(row) + 0.5)) for row, col in line]
            features.append({'type': 'Feature', 'geometry': {'type': 'LineString', 'coordinates': coordinates},
                             'properties': {'layer': 'contours', 'elevation_m': round(level, 4), 'source': 'Copernicus DEM GLO-30'}})
    return features

@app.post('/api/elevation/contours')
def elevation_contours(request: ElevationRequest):
    if not _slots.acquire(blocking=False):
        raise HTTPException(429, 'Serwer jest zajęty. Spróbuj ponownie później.')
    try:
        data, transform, tiles = read_dem_mosaic(request.bounds)
        features = contours_from_grid(data, transform, request.interval)
        return {'type': 'FeatureCollection', 'features': features, 'metadata': {
            'source': 'Copernicus DEM GLO-30', 'model_type': 'DSM', 'resolution': 'approximately 30 m',
            'attribution': f'Contains modified Copernicus DEM data {datetime.now(timezone.utc).year}',
            'license_url': 'https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM',
            'retrieved_at': datetime.now(timezone.utc).isoformat(), 'tiles': tiles,
            'interval_m': request.interval, 'nodata_pixels': int(np.ma.count_masked(data)),
            'warning': 'Model powierzchni (DSM), nie NMT. Rozdzielczość około 30 m; poziomice są poglądowe i nie zastępują pomiarów terenowych.'}}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        # Fail the elevation layer explicitly rather than fabricate data.
        raise HTTPException(502, 'Nie można pobrać lub przetworzyć kafla Copernicus DEM. Spróbuj ponownie później.') from exc
    finally:
        _slots.release()

# DXF conversion is deliberately independent of the data-source adapters.
from io import BytesIO, StringIO
import json
import ezdxf
from fastapi import Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from pyproj import Transformer

class DxfRequest(BaseModel):
    collection: dict
    crs: str = 'EPSG:2180'

    @model_validator(mode='after')
    def validate_export(self):
        if self.crs not in ('EPSG:2180', 'EPSG:32634'):
            raise ValueError('Nieobsługiwany układ DXF')
        if self.collection.get('type') != 'FeatureCollection' or not isinstance(self.collection.get('features'), list):
            raise ValueError('Wymagana kolekcja GeoJSON')
        if len(self.collection['features']) > 50000:
            raise ValueError('Przekroczono limit obiektów')
        return self

def dxf_layer(properties):
    p = properties or {}
    if p.get('layer') == 'contours': return 'POZIOMICE'
    if 'building' in p: return 'BUDYNKI'
    if 'highway' in p: return 'DROGI'
    if 'railway' in p: return 'KOLEJ'
    if 'waterway' in p or p.get('natural') == 'water' or p.get('landuse') == 'reservoir': return 'WODA'
    if any(k in p for k in ('natural', 'landuse', 'leisure')): return 'ZIELEN'
    return 'INNE'

def make_dxf(collection, crs):
    doc = ezdxf.new('R2010')
    doc.units = ezdxf.units.M
    for name, color in [('BUDYNKI', 7), ('DROGI', 2), ('WODA', 5), ('ZIELEN', 3), ('KOLEJ', 6), ('POZIOMICE', 30), ('INNE', 8), ('METADANE', 8)]:
        doc.layers.new(name, dxfattribs={'color': color})
    model = doc.modelspace()
    transform = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    warnings = []
    count = 0
    anchors = []
    def coords(line):
        points = []
        for point in line:
            lon, lat = float(point[0]), float(point[1])
            if not math.isfinite(lon) or not math.isfinite(lat) or not -180 <= lon <= 180 or not -85 <= lat <= 85: raise ValueError('Niepoprawne współrzędne')
            x, y = transform.transform(lon, lat, errcheck=True)
            if not math.isfinite(x) or not math.isfinite(y): raise ValueError('Błąd transformacji')
            points.append((x, y))
        return points
    def draw(geometry, layer, elevation=0):
        if not isinstance(geometry, dict): raise ValueError('Brak geometrii')
        kind, c = geometry.get('type'), geometry.get('coordinates')
        if kind == 'Point':
            point = coords([c])[0]; model.add_point((*point, elevation), dxfattribs={'layer': layer}); anchors.append(point)
        elif kind in ('LineString', 'MultiLineString', 'Polygon', 'MultiPolygon'):
            lines = [c] if kind == 'LineString' else c if kind in ('MultiLineString','Polygon') else [ring for polygon in c for ring in polygon]
            for line in lines:
                points = coords(line)
                if len(points) < 2: raise ValueError('Linia zawiera mniej niż dwa punkty')
                closed = kind in ('Polygon','MultiPolygon') and points[0] == points[-1]
                if closed: points = points[:-1]
                if len(points) < 2: raise ValueError('Niepoprawny pierścień')
                model.add_lwpolyline(points, close=closed, dxfattribs={'layer': layer, 'elevation': elevation})
                anchors.append(points[0])
        else: raise ValueError('Nieobsługiwany typ geometrii')
    for index, feature in enumerate(collection['features']):
        try:
            p = feature.get('properties') or {}
            height = float(p.get('elevation_m', 0))
            if not math.isfinite(height): raise ValueError('Niepoprawna wysokość')
            draw(feature['geometry'], dxf_layer(p), height)
            count += 1
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            warnings.append(f'Obiekt {index}: {str(exc)[:120]}')
    # Visible attribution inside the drawing, separated from the imported objects.
    meta = collection.get('metadata') or {}
    x, y = min(anchors, key=lambda p: p[0]+p[1]) if anchors else (0, 0)
    lines = ['GeoExport | ' + crs + ' | jednostki: m', 'Data: (c) OpenStreetMap contributors | ODbL 1.0 | openstreetmap.org/copyright',
             'Pobrano: ' + str(meta.get('retrieved_at', 'nieznana'))[:40]]
    elevation = meta.get('elevation')
    if isinstance(elevation, dict):
        lines.append(str(elevation.get('attribution', 'Copernicus DEM'))[:100] + ' | DSM ~30m')
        lines.append('Copernicus DEM: dataspace.copernicus.eu | dane pogladowe')
    upstream = meta.get('warnings', [])
    if isinstance(upstream, list):
        lines.extend(str(w)[:120] for w in upstream[:25])
    if meta.get('data_timestamp'): lines.append('Wersja danych OSM: ' + str(meta['data_timestamp'])[:40])
    lines.append(f'Pominiete obiekty DXF: {len(warnings)}')
    lines.extend(warnings[:25])
    if len(warnings) > 25: lines.append('Pozostale ostrzezenia pominieto.')
    for i, text in enumerate(lines):
        model.add_text(text, dxfattribs={'layer': 'METADANE', 'height': 2, 'insert': (x, y - 10 - i*3)})
    out = StringIO(); doc.write(out)
    return out.getvalue().encode('utf-8'), warnings, count

@app.post('/api/export/dxf')
async def export_dxf(request: Request):
    # Bound the body before JSON parsing; this endpoint is not a production security boundary.
    body = await request.body()
    if len(body) > 20_000_000: raise HTTPException(413, 'Plik jest zbyt duży')
    try:
        data = DxfRequest.model_validate_json(body)
    except Exception as exc:
        raise HTTPException(422, 'Niepoprawne dane eksportu') from exc
    content, warnings, count = await run_in_threadpool(make_dxf, data.collection, data.crs)
    return Response(content, media_type='application/dxf', headers={'Content-Disposition':'attachment; filename="geoexport.dxf"', 'X-GeoExport-Skipped': str(len(warnings)), 'X-GeoExport-Features': str(count)})

# SVG uses the same normalized GeoJSON and projected CRS as the CAD exporter.
from xml.etree import ElementTree as ET

SVG_NS = 'http://www.w3.org/2000/svg'
ET.register_namespace('', SVG_NS)
SVG_COLORS = {'BUDYNKI':'#b87960', 'DROGI':'#d6ad71', 'WODA':'#76afbf', 'ZIELEN':'#87aa8a', 'KOLEJ':'#8d8491', 'POZIOMICE':'#ad9078', 'INNE':'#7d8b82'}

def make_svg(collection, crs):
    transform = Transformer.from_crs('EPSG:4326', crs, always_xy=True)
    paths, all_points, warnings = [], [], []
    def project_line(line):
        if not isinstance(line, list) or len(line) < 2: raise ValueError('Niepoprawna linia')
        result = []
        for point in line:
            lon, lat = float(point[0]), float(point[1])
            if not math.isfinite(lon) or not math.isfinite(lat) or not (-180 <= lon <= 180 and -85 <= lat <= 85): raise ValueError('Niepoprawne współrzędne')
            x, y = transform.transform(lon, lat, errcheck=True)
            if not math.isfinite(x) or not math.isfinite(y): raise ValueError('Błąd transformacji')
            result.append((x, -y))
        return result
    for index, feature in enumerate(collection['features']):
        try:
            geom = feature['geometry']
            if not isinstance(geom, dict): raise ValueError('Brak geometrii')
            kind, coordinates = geom.get('type'), geom.get('coordinates')
            layer = dxf_layer(feature.get('properties'))
            if kind == 'Point':
                xy = project_line([coordinates, coordinates])[0]
                paths.append((layer, 'point', xy)); all_points.append(xy)
            else:
                if kind == 'LineString': lines = [coordinates]
                elif kind in ('MultiLineString', 'Polygon'): lines = coordinates
                elif kind == 'MultiPolygon': lines = [ring for poly in coordinates for ring in poly]
                else: raise ValueError('Nieobsługiwany typ geometrii')
                projected = [project_line(line) for line in lines]
                if not projected: raise ValueError('Pusta geometria')
                paths.append((layer, 'polygon' if kind in ('Polygon','MultiPolygon') else 'line', projected))
                all_points.extend(pt for line in projected for pt in line)
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            warnings.append(f'Obiekt {index}: {str(exc)[:120]}')
    if not all_points: raise ValueError('Brak poprawnych geometrii do eksportu')
    minx, maxx = min(x for x,y in all_points), max(x for x,y in all_points)
    miny, maxy = min(y for x,y in all_points), max(y for x,y in all_points)
    width, height = max(maxx-minx, 10), max(maxy-miny, 10)
    padding = max(width, height) * .03
    # Extra space below the map makes attribution visible in any SVG viewer.
    font_size = max(width, height) / 95
    footer = font_size * 5
    root = ET.Element(f'{{{SVG_NS}}}svg', {'viewBox': f'{minx-padding:.3f} {miny-padding:.3f} {width+2*padding:.3f} {height+2*padding+footer:.3f}', 'role':'img'})
    ET.SubElement(root, f'{{{SVG_NS}}}title').text = 'GeoExport — mapa OSM'
    meta = dict(collection.get('metadata') or {})
    meta.update({'export_crs': crs, 'units':'meters', 'svg_warnings': warnings})
    ET.SubElement(root, f'{{{SVG_NS}}}metadata').text = json.dumps(meta, ensure_ascii=False)
    # Grouping and editable vector paths preserve layers in graphics software.
    for layer in SVG_COLORS:
        group = ET.SubElement(root, f'{{{SVG_NS}}}g', {'id':layer, 'stroke':SVG_COLORS[layer], 'stroke-width':str(max(width,height)/1200), 'stroke-linejoin':'round', 'stroke-linecap':'round'})
        for item_layer, kind, geometry in paths:
            if item_layer != layer: continue
            if kind == 'point':
                ET.SubElement(group, f'{{{SVG_NS}}}circle', {'cx':f'{geometry[0]:.3f}', 'cy':f'{geometry[1]:.3f}', 'r':str(max(width,height)/500), 'fill': SVG_COLORS[layer]})
            else:
                d = ' '.join('M ' + ' L '.join(f'{x:.3f},{y:.3f}' for x,y in line) + (' Z' if kind=='polygon' else '') for line in geometry)
                ET.SubElement(group, f'{{{SVG_NS}}}path', {'d':d, 'fill':SVG_COLORS[layer] if kind=='polygon' else 'none', 'fill-opacity':'.35', 'fill-rule':'evenodd'})
    credits = ['© OpenStreetMap contributors · ODbL · openstreetmap.org/copyright', f'{crs} · metry · dane poglądowe']
    if isinstance(meta.get('elevation'), dict): credits.append(str(meta['elevation'].get('attribution', 'Copernicus DEM')) + ' · DSM ~30 m')
    for i, line in enumerate(credits):
        ET.SubElement(root, f'{{{SVG_NS}}}text', {'x':f'{minx:.3f}', 'y':f'{maxy+padding+font_size*(i+1):.3f}', 'font-size':f'{font_size:.3f}', 'font-family':'sans-serif', 'fill':'#3f5147'}).text = line
    return ET.tostring(root, encoding='utf-8', xml_declaration=True), warnings

@app.post('/api/export/svg')
async def export_svg(request: Request):
    body = await request.body()
    if len(body) > 20_000_000: raise HTTPException(413, 'Plik jest zbyt duży')
    try:
        data = DxfRequest.model_validate_json(body)
        content, warnings = await run_in_threadpool(make_svg, data.collection, data.crs)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return Response(content, media_type='image/svg+xml', headers={'Content-Disposition':'attachment; filename="geoexport.svg"', 'X-GeoExport-Skipped':str(len(warnings))})

import zipfile
import trimesh
from shapely.geometry import Polygon

class ModelRequest(DxfRequest):
    bounds: tuple[float, float, float, float]
    format: str

    @model_validator(mode='after')
    def validate_model(self):
        ElevationRequest(bounds=self.bounds)
        if self.format not in ('OBJ', 'glTF'):
            raise ValueError('Nieobsługiwany format 3D')
        return self

def building_height(properties):
    raw = properties.get('height')
    if raw is not None:
        try:
            value = float(str(raw).strip().removesuffix(' m').replace(',', '.'))
            if 0 < value <= 1000: return value, False
        except (ValueError, TypeError): pass
    try:
        levels = float(properties.get('building:levels', 0))
        if 0 < levels <= 200: return levels * 3, True
    except (ValueError, TypeError): pass
    return 9.0, True

def make_model(request):
    west, south, east, north = request.bounds
    data, affine, tiles = read_dem_mosaic(request.bounds)
    arr = np.ma.asarray(data, dtype=float).filled(np.nan)
    if arr.shape[0] < 2 or arr.shape[1] < 2 or not np.isfinite(arr).any(): raise ValueError('Niewystarczające dane wysokościowe.')
    if arr.shape[0] * arr.shape[1] > 100000: raise ValueError('Siatka terenu jest zbyt duża.')
    transformer = Transformer.from_crs('EPSG:4326', request.crs, always_xy=True)
    origin_x, origin_y = transformer.transform(west, south, errcheck=True)
    base_z = float(np.nanmin(arr))
    vertices = []
    for row in range(arr.shape[0]):
        for col in range(arr.shape[1]):
            lon, lat = affine @ (col+.5, row+.5)
            x, y = transformer.transform(lon, lat, errcheck=True)
            value = arr[row,col]
            vertices.append((x-origin_x, y-origin_y, float(value-base_z) if np.isfinite(value) else 0))
    faces=[]
    rows, cols = arr.shape
    for row in range(rows-1):
        for col in range(cols-1):
            a=row*cols+col; b=a+1; c=a+cols; d=c+1
            if np.isfinite(arr[row,col]) and np.isfinite(arr[row+1,col]) and np.isfinite(arr[row,col+1]): faces.append((a,c,b))
            if np.isfinite(arr[row,col+1]) and np.isfinite(arr[row+1,col]) and np.isfinite(arr[row+1,col+1]): faces.append((b,c,d))
    if not faces: raise ValueError('Brak ciągłej siatki wysokościowej.')
    scene=trimesh.Scene()
    scene.add_geometry(trimesh.Trimesh(vertices=vertices, faces=faces, process=False), geom_name='terrain_dsm', node_name='terrain_dsm')
    warnings=[]; building_count=0; estimated=0
    for i, feature in enumerate(request.collection['features']):
        props=feature.get('properties') or {}
        if not isinstance(props, dict) or 'building' not in props: continue
        try:
            geom=feature['geometry']; kind=geom['type']; coordinates=geom['coordinates']
            if kind == 'Polygon': polygons=[coordinates]
            elif kind == 'MultiPolygon': polygons=coordinates
            else: raise ValueError('Obrys budynku nie jest poligonem')
            for poly in polygons:
                if not poly or not poly[0]: raise ValueError('Pusty obrys')
                def project(ring):
                    points=[]
                    for lon,lat,*_ in ring:
                        x,y=transformer.transform(float(lon),float(lat),errcheck=True)
                        if not math.isfinite(x) or not math.isfinite(y): raise ValueError('Niepoprawne współrzędne')
                        points.append((x-origin_x,y-origin_y))
                    return points
                shape=Polygon(project(poly[0]), [project(r) for r in poly[1:]])
                if not shape.is_valid or shape.is_empty or shape.area <= 0: raise ValueError('Niepoprawny obrys')
                # A DSM sample is only an approximation, not a true ground level.
                lon,lat=poly[0][0][:2]; col,row=(~affine) @ (float(lon),float(lat))
                rr=max(0,min(rows-1,int(row))); cc=max(0,min(cols-1,int(col)))
                z=float(arr[rr,cc]-base_z) if np.isfinite(arr[rr,cc]) else 0
                height, assumed=building_height(props)
                mesh=trimesh.creation.extrude_polygon(shape,height,engine='earcut')
                mesh.apply_translation((0,0,z)); scene.add_geometry(mesh,geom_name=f'building_{building_count}',node_name=f'building_{building_count}')
                building_count+=1; estimated+=int(assumed)
        except Exception as exc:
            warnings.append(f'Budynek {i}: {str(exc)[:120]}')
    metadata={'source':'OpenStreetMap and Copernicus DEM GLO-30', 'attribution':'© OpenStreetMap contributors; Contains modified Copernicus DEM data',
              'licenses':['https://www.openstreetmap.org/copyright','https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM'],
              'crs':request.crs, 'units':'meters', 'axes': ('X=east, Y=up, Z=south' if request.format == 'glTF' else 'X=east, Y=north, Z=up'), 'local_origin_easting_northing': [origin_x,origin_y], 'vertical_origin_m':base_z,
              'data_timestamp':request.collection.get('metadata',{}).get('data_timestamp'), 'retrieved_at':datetime.now(timezone.utc).isoformat(),
              'dem_tiles':tiles, 'dem_nodata_pixels':int(np.ma.count_masked(data)), 'buildings':building_count,'estimated_building_heights':estimated,'warnings':warnings,
              'disclaimer':'DEM is a surface model (DSM), not bare earth. Building elevations and fallback heights are approximate; not for surveying.'}
    memory=BytesIO()
    with zipfile.ZipFile(memory,'w',zipfile.ZIP_DEFLATED) as archive:
        if request.format == 'OBJ':
            obj=trimesh.exchange.obj.export_obj(scene)
            archive.writestr('model.obj', '# © OpenStreetMap contributors, ODbL; Contains modified Copernicus DEM data\n# Metadata: metadata.json\n'+obj)
        else:
            # glTF uses a right-handed, Y-up coordinate system; OBJ stays Z-up.
            scene.apply_transform([[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]])
            def add_attribution(tree):
                tree.setdefault('asset', {})['copyright'] = metadata['attribution']
                tree.setdefault('asset', {})['extras'] = {'license_urls': metadata['licenses'], 'metadata_file':'metadata.json'}
            archive.writestr('model.glb',trimesh.exchange.gltf.export_glb(scene,tree_postprocessor=add_attribution))
        archive.writestr('metadata.json',json.dumps(metadata,ensure_ascii=False,indent=2))
    return memory.getvalue(), len(warnings)

@app.post('/api/export/model')
async def export_model(request: Request):
    body=await request.body()
    if len(body)>20_000_000: raise HTTPException(413,'Plik jest zbyt duży')
    try:
        data=ModelRequest.model_validate_json(body)
    except Exception as exc:
        raise HTTPException(422,'Niepoprawne dane modelu') from exc
    if not _slots.acquire(blocking=False): raise HTTPException(429,'Serwer jest zajęty')
    try:
        content,warnings=await run_in_threadpool(make_model,data)
        return Response(content,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="geoexport-model.zip"','X-GeoExport-Skipped':str(warnings)})
    except ValueError as exc:
        raise HTTPException(422,str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502,'Nie udało się pobrać lub przetworzyć danych DEM dla modelu 3D.') from exc
    finally:
        _slots.release()

@app.post('/api/export/geotiff')
def export_geotiff(data: ElevationRequest):
    if not _slots.acquire(blocking=False): raise HTTPException(429,'Serwer jest zajęty')
    try:
        raster,transform,tiles = read_dem_mosaic(data.bounds)
        if raster.size>100000 or np.ma.count(raster)==0: raise HTTPException(422,'Niepoprawna lub zbyt duża siatka DEM.')
        from rasterio.io import MemoryFile
        with MemoryFile() as memory:
            with memory.open(driver='GTiff',height=raster.shape[0],width=raster.shape[1],count=1,dtype='float32',crs='EPSG:4326',transform=transform,nodata=-9999,compress='deflate') as dst:
                dst.write(np.ma.asarray(raster,dtype='float32').filled(-9999),1)
                dst.update_tags(source='Copernicus DEM GLO-30', attribution=f'Contains modified Copernicus DEM data {datetime.now(timezone.utc).year}', license='https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions/collections-description/COP-DEM', retrieved_at=datetime.now(timezone.utc).isoformat(), source_tiles=json.dumps(tiles), nodata_pixels=str(np.ma.count_masked(raster)), description='DSM, not bare-earth DTM; approximate 30m resolution; not for surveying')
            return Response(memory.read(),media_type='image/tiff',headers={'Content-Disposition':'attachment; filename="geoexport-dem.tif"'})
    except HTTPException: raise
    except Exception as exc: raise HTTPException(502,'Nie udało się pobrać danych Copernicus DEM.') from exc
    finally: _slots.release()
