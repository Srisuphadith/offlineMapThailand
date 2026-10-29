#!/bin/bash
# รันเว็บแผนที่ในเครื่องที่ http://localhost:8080 (เสิร์ฟเฉพาะโฟลเดอร์ web/)
cd "$(dirname "$0")" || exit 1
exec caddy file-server --root web --listen :8080
