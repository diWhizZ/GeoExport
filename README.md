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

Działają wyszukiwanie adresu lub pary współrzędnych (szerokość, długość; kropka dziesiętna), wybór prostokątnego obszaru, obliczanie powierzchni, podgląd wybranych warstw na żądanie, kreator konfiguracji oraz eksport wybranych warstw OSM (budynki, drogi, woda, zieleń, kolej) do GeoJSON w WGS 84 lub DXF i SVG w EPSG:2180 / UTM 34N (metry). SVG zawiera edytowalne grupy warstw, widoczną atrybucję oraz metadane i ostrzeżenia w elemencie metadata. DXF zawiera oddzielne warstwy, poziomice na odpowiedniej wysokości i tekst atrybucji oraz ostrzeżeń na warstwie METADANE. Geometrie nieobsługiwane lub niepoprawne są pomijane. Układy DXF są przeznaczone dla Polski, nie dla całego świata. Dane są pobierane w przeglądarce z publicznego Overpass API i przycinane do prostokąta. Eksport zawiera atrybucję, licencję, datę pobrania, wersję danych Overpass i ostrzeżenia. Publiczna instancja ma limity i nie zapewnia dostępności ani czasu wykonania; jest to rozwiązanie prototypowe, nie infrastruktura produkcyjna. Historia konfiguracji eksportów jest zapisywana lokalnie w przeglądarce (maksymalnie 50 pozycji); można ją wyczyścić lub przywrócić ustawienia. Pliki nie są w niej przechowywane, a ponowny eksport pobiera aktualne dane. Historia nie jest kontem użytkownika, nie synchronizuje się między urządzeniami i może zostać usunięta wraz z danymi przeglądarki.

Opcjonalnie backend pobiera okno publicznego kafla Copernicus DEM GLO-30 i generuje poglądowe poziomice do GeoJSON. Jest to model powierzchni DSM (około 30 m), nie model gołego terenu NMT; mały interwał nie poprawia dokładności danych. W razie błędu warstwa jest pomijana, a przyczyna jest odnotowana w ostrzeżeniach. Backend musi mieć dostęp HTTPS do `copernicus-dem-30m.s3.amazonaws.com`; środowisko podglądu Arena ogranicza połączenia wychodzące i może nie pozwalać na pobranie kafli. To prototypowy adapter, bez gwarancji dostępności, wydajności czy pełnej walidacji topologii. GeoTIFF eksportuje próbki DSM w WGS 84 wraz z metadanymi. Eksport OBJ i binarny glTF (GLB) tworzy ZIP z siatką DSM, bryłami LoD1 budynków i metadanymi; współrzędne są lokalne, a przesunięcie do CRS jest zapisane w pliku metadata.json. Wysokość brakujących budynków jest szacowana, a podstawa budynków z DSM jest orientacyjna. Poziomice, GeoTIFF i modele 3D obsługują obszary przecinające wiele kafli DEM (maksymalnie 16); kafle są łączone na wspólnej siatce metodą najbliższego sąsiada, a brakujące piksele pozostają NoData. Granice rastra mogą wystawać poza obszar o niecały piksel. 3D wymaga co najmniej jednej warstwy OSM. OBJ używa osi Z jako pionowej, a glTF używa osi Y; układ osi i przesunięcie zapisane są w metadanych. Nie zaimplementowano polskiego NMT, kont, historii na serwerze, kolejki zadań, PostGIS, S3 ani panelu administracyjnego. Wybór szczegółowości nie wpływa jeszcze na wynik, podobnie jak pozostałe ustawienia stylu. Dostępne jest tylko zaznaczanie prostokąta. Brak testów E2E w CAD/QGIS, testu wydajności 60 sekund i integracji live z DEM. Aplikacja nie spełnia jeszcze kryteriów pełnego MVP; dane mapowe są poglądowe i nie zastępują pomiarów geodezyjnych.

Dane mapy: © OpenStreetMap contributors, ODbL: https://www.openstreetmap.org/copyright.

Testy backendu: `cd backend && pytest -q` (obejmują lokalne testowe kafle GeoTIFF, granice kafli i NoData; nie sprawdzają dostępu do zewnętrznego źródła). Nie wdrażać publicznie bez uwierzytelniania, limitowania, cache i kolejki zadań.
