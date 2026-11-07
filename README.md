# GeoExport

Interaktywny prototyp kreatora eksportów geoprzestrzennych.

## Uruchomienie

```bash
npm install
npm run dev
npm test

# W drugim terminalu: usługa wysokościowa
python -m venv .venv
. .venv/bin/activate
pip install -r backend/requirements.txt
cd backend && uvicorn main:app --host 0.0.0.0 --port 8000
```

Alternatywnie: `docker compose up --build`, a następnie otwórz `http://localhost:5173`. Compose uruchamia frontend Vite i backend FastAPI; jest to konfiguracja deweloperska.

Kafle OpenStreetMap, wyszukiwarka Nominatim i publiczny Overpass API wymagają dostępu do internetu. Kliknij dwa przeciwległe narożniki na mapie, aby zmienić prostokąt. Powierzchnia jest obliczana geodezyjnie przez Turf.js, a limit wynosi 1 km².

## Aktualny zakres

Działają wyszukiwanie lokalizacji, wybór prostokątnego obszaru, obliczanie powierzchni, kreator konfiguracji oraz eksport wybranych warstw OSM (budynki, drogi, woda, zieleń, kolej) do GeoJSON w WGS 84 lub DXF i SVG w EPSG:2180 / UTM 34N (metry). SVG zawiera edytowalne grupy warstw, widoczną atrybucję oraz metadane i ostrzeżenia w elemencie metadata. DXF zawiera oddzielne warstwy, poziomice na odpowiedniej wysokości i tekst atrybucji oraz ostrzeżeń na warstwie METADANE. Geometrie nieobsługiwane lub niepoprawne są pomijane. Układy DXF są przeznaczone dla Polski, nie dla całego świata. Dane są pobierane w przeglądarce z publicznego Overpass API i przycinane do prostokąta. Eksport zawiera atrybucję, licencję, datę pobrania, wersję danych Overpass i ostrzeżenia. Publiczna instancja ma limity i nie zapewnia dostępności ani czasu wykonania; jest to rozwiązanie prototypowe, nie infrastruktura produkcyjna. Historia jest przechowywana tylko w pamięci bieżącej sesji.

Opcjonalnie backend pobiera okno publicznego kafla Copernicus DEM GLO-30 i generuje poglądowe poziomice do GeoJSON. Jest to model powierzchni DSM (około 30 m), nie model gołego terenu NMT; mały interwał nie poprawia dokładności danych. W razie błędu warstwa jest pomijana, a przyczyna jest odnotowana w ostrzeżeniach. Backend musi mieć dostęp HTTPS do `copernicus-dem-30m.s3.amazonaws.com`; środowisko podglądu Arena ogranicza połączenia wychodzące i może nie pozwalać na pobranie kafli. To prototypowy adapter, bez gwarancji dostępności, wydajności czy pełnej walidacji topologii. GeoTIFF eksportuje próbki DSM w WGS 84 wraz z metadanymi. Eksport OBJ i binarny glTF (GLB) tworzy ZIP z siatką DSM, bryłami LoD1 budynków i metadanymi; współrzędne są lokalne, a przesunięcie do CRS jest zapisane w pliku metadata.json. Wysokość brakujących budynków jest szacowana, a podstawa budynków z DSM jest orientacyjna. Obecnie GeoTIFF i 3D obsługują obszar w granicach jednego kafla DEM, a 3D wymaga co najmniej jednej warstwy OSM. Nie zaimplementowano polskiego NMT, kont, trwałej historii, kolejki zadań, PostGIS, S3 ani panelu administracyjnego. Wybór szczegółowości nie wpływa jeszcze na wynik, podobnie jak pozostałe ustawienia stylu. Dostępne jest tylko zaznaczanie prostokąta. Brak testów E2E w CAD/QGIS, testu wydajności 60 sekund i integracji live z DEM. Aplikacja nie spełnia jeszcze kryteriów pełnego MVP; dane mapowe są poglądowe i nie zastępują pomiarów geodezyjnych.

Dane mapy: © OpenStreetMap contributors, ODbL: https://www.openstreetmap.org/copyright.

Testy backendu: `cd backend && pytest -q` (obejmują symulowane kafle; nie sprawdzają dostępu do zewnętrznego źródła). Nie wdrażać publicznie bez uwierzytelniania, limitowania, cache i kolejki zadań.
