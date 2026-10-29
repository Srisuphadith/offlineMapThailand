# Offline Map Thailand

เว็บแผนที่ประเทศไทยที่ใช้งานได้โดยไม่ต้องต่อเน็ต วาดด้วย [MapLibre GL JS](https://maplibre.org/)
จากไฟล์ vector tiles ไฟล์เดียว (`.pmtiles`, schema OpenMapTiles) ที่สร้างจากข้อมูล OpenStreetMap
มีช่องค้นหาสถานที่พร้อมชื่อจังหวัด

## โครงสร้าง

```
web/                      ทุกอย่างที่หน้าเว็บใช้ (โฟลเดอร์นี้คือสิ่งที่ถูก deploy)
  index.html              style แผนที่ + ช่องค้นหา
  lib/                    MapLibre GL JS 4.7.1, pmtiles.js (โหลดใหม่ได้ด้วย make lib)
  fonts/                  glyph ฟอนต์ Noto Sans (มีช่วงภาษาไทย)
  thailand.pmtiles        ข้อมูลแผนที่      ← สร้างด้วย make
  search-index.json       ข้อมูลค้นหา       ← สร้างด้วย make
tools/
  build_search_index.py   ดึงชื่อสถานที่จาก .pmtiles เป็น search-index.json
  provinces.py            อ่านขอบเขตจังหวัดจาก .osm.pbf เพื่อใส่ชื่อจังหวัดให้ผลค้นหา
data/                     ไฟล์ OSM และไฟล์ชั่วคราวของ Planetiler (ไม่ commit)
Makefile                  ขั้นตอน build ทั้งหมด
Dockerfile, Caddyfile, compose.yaml   รันด้วย Docker
run.sh                    รันในเครื่องด้วย Caddy
```

## สิ่งที่ต้องมี

- Docker (ใช้รัน [Planetiler](https://github.com/onthegomap/planetiler) ตอนสร้าง `.pmtiles`)
  ตั้ง memory ของ Docker Desktop อย่างน้อย 8 GB (Settings → Resources)
- Python 3.9+ (ใช้แค่ standard library)
- [Caddy](https://caddyserver.com/) ถ้าจะรันในเครื่องด้วย `run.sh`

## สร้างข้อมูล

```sh
make            # โหลด OSM ประเทศไทย (ครั้งแรก) → สร้าง .pmtiles → สร้าง search index
make update     # โหลด OSM ล่าสุดจาก Geofabrik แล้วสร้างใหม่ทั้งหมด
```

`make` ทำเฉพาะส่วนที่ต้องทำใหม่ เช่น แก้แค่สคริปต์ใน `tools/` จะสร้างแค่ search index (~30 วินาที)
ไม่รัน Planetiler ซ้ำ

ถ้า Docker มี memory น้อย ให้ลด heap ของ Java ลง เช่น `make pmtiles JAVA_XMX=3g`
(ถ้า Planetiler จบด้วย exit code 137 แปลว่าโดน OOM kill)

## รัน

ในเครื่อง:

```sh
./run.sh        # แล้วเปิด http://localhost:8080
```

ด้วย Docker:

```sh
make docker     # = docker compose up -d --build แล้วเปิด http://localhost:8080
```

image มีข้อมูลทุกอย่างอยู่ข้างใน ย้ายไปเครื่องที่ไม่มีเน็ตได้ด้วย
`docker save offline-map-thailand | gzip > offline-map.tar.gz` แล้ว `docker load < offline-map.tar.gz`

## ข้อควรรู้

- **ต้องเปิดผ่าน HTTP server ที่รองรับ Range request** (Caddy รองรับ) pmtiles.js อ่านไฟล์ทีละส่วน
  เปิด `index.html` ตรง ๆ แบบ `file://` หรือใช้ `python3 -m http.server` จะไม่ทำงาน
- **ห้ามบีบอัด `.pmtiles` ที่ระดับ server** เพราะจะทำให้ Range request เพี้ยน
  ([Caddyfile](Caddyfile) บีบอัดเฉพาะ html/js/css/json)
- ถ้าเปลี่ยนเวอร์ชัน Planetiler ใน [Makefile](Makefile) ให้ตรวจชื่อ layer/field ใน `index.html` ด้วย
- ข้อมูลแผนที่ © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright)
