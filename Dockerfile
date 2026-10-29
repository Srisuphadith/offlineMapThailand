# เว็บแผนที่ออฟไลน์: ไฟล์ทั้งหมดอยู่ใน image ไม่ต้องใช้เน็ตตอนรัน
# ต้องมี web/thailand.pmtiles และ web/search-index.json ก่อน build (รัน make)
FROM caddy:2.8.4-alpine

COPY Caddyfile /etc/caddy/Caddyfile
COPY web /srv

EXPOSE 80
