# สร้างข้อมูลแผนที่ออฟไลน์
#
#   make              สร้าง web/thailand.pmtiles และ web/search-index.json (ทำเฉพาะส่วนที่ต้องทำใหม่)
#   make update       โหลดข้อมูล OSM ล่าสุด แล้วสร้างใหม่ทั้งหมด
#   make pmtiles      สร้างเฉพาะ thailand.pmtiles
#   make search-index สร้างเฉพาะ search-index.json
#   make lib          โหลด MapLibre / pmtiles.js ตามเวอร์ชันที่ระบุด้านล่าง (ต้องใช้เน็ต)
#   make docker       build docker image แล้วรันด้วย docker compose
#   make clean        ลบไฟล์ชั่วคราวของ Planetiler
#
# ตัวแปรที่ปรับได้ เช่น  make pmtiles JAVA_XMX=3g
#   JAVA_XMX ต้องน้อยกว่า memory ที่ตั้งให้ Docker Desktop ไม่งั้นจะโดน OOM kill (exit 137)

OSM_URL    := https://download.geofabrik.de/asia/thailand-latest.osm.pbf
DATA       := data
WEB        := web
OSM        := $(DATA)/thailand-latest.osm.pbf
PMTILES    := $(WEB)/thailand.pmtiles
INDEX      := $(WEB)/search-index.json
# ล็อก image ไว้ที่ตัวที่ build ผ่านแล้ว (Planetiler 0.10.3-SNAPSHOT, OpenMapTiles 3.16)
# เปลี่ยนเป็น :latest ได้ แต่ schema อาจเปลี่ยนจน style ใน index.html ต้องแก้ตาม
PLANETILER := ghcr.io/onthegomap/planetiler@sha256:23dae8c006cfd4612cc1c439ca1ca6d02561eeffb3d6a78115ee020a62fca884
MAPLIBRE_VERSION := 4.7.1
PMTILES_JS_VERSION := 4.3.0
JAVA_XMX   ?= 6g
PYTHON     ?= python3

.PHONY: all pmtiles search-index update download lib docker clean

all: pmtiles search-index

pmtiles: $(PMTILES)
search-index: $(INDEX)

$(OSM):
	mkdir -p $(DATA)
	curl -fL -o $@ $(OSM_URL)

# --download โหลดเฉพาะข้อมูลประกอบที่ยังไม่มี (water polygons, Natural Earth) ไว้ใน data/sources
# สร้างลง data/ ก่อน แล้วค่อยย้ายมาแทนตัวจริงเมื่อเสร็จ กันไฟล์ที่สร้างไม่เสร็จไปทับตัวที่ใช้อยู่
$(PMTILES): $(OSM)
	docker run --rm -e JAVA_TOOL_OPTIONS="-Xmx$(JAVA_XMX)" \
	  -v "$(CURDIR)/$(DATA)":/data $(PLANETILER) \
	  --area=thailand \
	  --osm-path=/data/$(notdir $(OSM)) \
	  --download \
	  --output=/data/$(notdir $(PMTILES)) \
	  --force
	@head -c 7 $(DATA)/$(notdir $(PMTILES)) | grep -q PMTiles || { echo "$(DATA)/$(notdir $(PMTILES)) ไม่สมบูรณ์"; exit 1; }
	mv $(DATA)/$(notdir $(PMTILES)) $@

$(INDEX): $(PMTILES) $(OSM) tools/build_search_index.py tools/provinces.py
	$(PYTHON) tools/build_search_index.py --osm $(OSM) $(PMTILES) $@

# โหลด OSM ใหม่เฉพาะเมื่อบน Geofabrik ใหม่กว่าไฟล์ในเครื่อง (-z) แล้วสร้างทุกอย่างตามมา
update: download all

download:
	mkdir -p $(DATA)
	curl -fL -z $(OSM) -o $(OSM) $(OSM_URL)

# -f: ถ้าเวอร์ชันไม่มีอยู่จริง curl จะ error แทนที่จะบันทึกหน้า 404 ลงไฟล์
lib:
	mkdir -p $(WEB)/lib
	curl -fL -o $(WEB)/lib/maplibre-gl.js  https://unpkg.com/maplibre-gl@$(MAPLIBRE_VERSION)/dist/maplibre-gl.js
	curl -fL -o $(WEB)/lib/maplibre-gl.css https://unpkg.com/maplibre-gl@$(MAPLIBRE_VERSION)/dist/maplibre-gl.css
	curl -fL -o $(WEB)/lib/pmtiles.js      https://unpkg.com/pmtiles@$(PMTILES_JS_VERSION)/dist/pmtiles.js

docker: all
	docker compose up -d --build

clean:
	rm -rf $(DATA)/tmp
