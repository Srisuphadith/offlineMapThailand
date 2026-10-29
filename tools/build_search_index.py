#!/usr/bin/env python3
"""สร้าง search-index.json จาก thailand.pmtiles สำหรับช่องค้นหาใน index.html

อ่านทุก tile ที่ zoom 14 (OpenMapTiles ใส่ POI/ชื่อครบที่ระดับนี้) แล้วเก็บชื่อ + พิกัด
และใส่ชื่อจังหวัดให้แต่ละรายการ โดยใช้ขอบเขตจังหวัดจากไฟล์ OSM (.osm.pbf)
ใช้แค่ standard library ไม่ต้องติดตั้งอะไรเพิ่ม

    python3 tools/build_search_index.py [--osm data/thailand-latest.osm.pbf] [web/thailand.pmtiles] [web/search-index.json]

รูปแบบไฟล์ที่ได้: {"provinces": [ชื่อจังหวัด, ...],
                   "items": [[ชื่อ, ชื่ออื่น, ประเภท, lon, lat, index จังหวัด หรือ -1], ...]}
"""
import argparse
import gzip
import json
import math
import os
import struct
import sys
from multiprocessing import Pool

from provinces import ProvinceLocator, load_provinces

ZOOM = 14
LAYERS = {"place", "poi", "transportation_name", "water_name", "mountain_peak", "aerodrome_label"}


# ---------- PMTiles v3 ----------

def varint(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if x < 0x80:
            return r, i


def read_header(f):
    h = f.read(127)
    if h[:7] != b"PMTiles":
        sys.exit("ไม่ใช่ไฟล์ PMTiles")
    root_off, root_len, _, _, leaf_off, _, data_off = struct.unpack("<7Q", h[8:64])
    return root_off, root_len, leaf_off, data_off


def read_gz(f, off, length):
    f.seek(off)
    return gzip.decompress(f.read(length))


def parse_dir(b):
    n, i = varint(b, 0)
    ids, last = [], 0
    for _ in range(n):
        v, i = varint(b, i)
        last += v
        ids.append(last)
    runs, lens, offs = [], [], []
    for _ in range(n):
        v, i = varint(b, i)
        runs.append(v)
    for _ in range(n):
        v, i = varint(b, i)
        lens.append(v)
    for k in range(n):
        v, i = varint(b, i)
        offs.append(offs[k - 1] + lens[k - 1] if v == 0 and k > 0 else v - 1)
    return list(zip(ids, runs, offs, lens))


def all_entries(f, root_off, root_len, leaf_off):
    """คืน (tile_id, run_length, offset, length) ของทุก tile โดยไล่ leaf directory ด้วย"""
    out, stack = [], [parse_dir(read_gz(f, root_off, root_len))]
    while stack:
        for tid, run, off, length in stack.pop():
            if run == 0:
                stack.append(parse_dir(read_gz(f, leaf_off + off, length)))
            else:
                out.append((tid, run, off, length))
    return out


def zoom_base(z):
    return (4 ** z - 1) // 3


def tileid_to_xy(tid, z):
    # แปลงตำแหน่งบน Hilbert curve กลับเป็น x, y
    n = 1 << z
    t = tid - zoom_base(z)
    x = y = 0
    s = 1
    while s < n:
        rx = 1 & (t // 2)
        ry = 1 & (t ^ rx)
        if ry == 0:
            if rx == 1:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
        x += s * rx
        y += s * ry
        t //= 4
        s *= 2
    return x, y


# ---------- Mapbox Vector Tile ----------

def fields(b):
    i, n = 0, len(b)
    while i < n:
        key, i = varint(b, i)
        num, wt = key >> 3, key & 7
        if wt == 2:
            length, i = varint(b, i)
            yield num, b[i:i + length]
            i += length
        elif wt == 0:
            v, i = varint(b, i)
            yield num, v
        elif wt == 5:
            yield num, b[i:i + 4]
            i += 4
        elif wt == 1:
            yield num, b[i:i + 8]
            i += 8


def packed(b):
    out, i = [], 0
    while i < len(b):
        v, i = varint(b, i)
        out.append(v)
    return out


def decode_value(b):
    for num, v in fields(b):
        if num == 1:
            return v.decode("utf-8", "replace")
        if num == 2:
            return struct.unpack("<f", v)[0]
        if num == 3:
            return struct.unpack("<d", v)[0]
        if num in (4, 5):
            return v
        if num == 6:
            return (v >> 1) ^ -(v & 1)
        if num == 7:
            return bool(v)
    return None


def first_point(geom):
    # คำสั่งแรกของทุก geometry คือ MoveTo -> ใช้จุดแรกเป็นตำแหน่ง
    if len(geom) < 3:
        return None
    dx, dy = geom[1], geom[2]
    return (dx >> 1) ^ -(dx & 1), (dy >> 1) ^ -(dy & 1)


def decode_tile(data):
    """คืน [(layer, props, px, py, extent)] เฉพาะ layer ที่ใช้ค้นหา"""
    out = []
    for num, layer in fields(data):
        if num != 3:
            continue
        name, feats, keys, vals, extent = None, [], [], [], 4096
        for n, v in fields(layer):
            if n == 1:
                name = v.decode()
                if name not in LAYERS:
                    break
            elif n == 2:
                feats.append(v)
            elif n == 3:
                keys.append(v.decode())
            elif n == 4:
                vals.append(decode_value(v))
            elif n == 5:
                extent = v
        if name not in LAYERS:
            continue
        for fb in feats:
            tags, geom = [], []
            for n, v in fields(fb):
                if n == 2:
                    tags = packed(v)
                elif n == 4:
                    geom = packed(v)
            pt = first_point(geom)
            if pt is None:
                continue
            props = {keys[tags[i]]: vals[tags[i + 1]] for i in range(0, len(tags) - 1, 2)}
            out.append((name, props, pt[0], pt[1], extent))
    return out


# ---------- สร้าง index ----------

def kind_of(layer, p):
    if layer == "place":
        return "place:" + str(p.get("class", ""))
    if layer == "poi":
        return "poi:" + str(p.get("class", ""))
    if layer == "transportation_name":
        return "road"
    if layer == "water_name":
        return "water"
    if layer == "mountain_peak":
        return "peak"
    return "airport"


def process(chunk):
    path, entries, data_off = chunk
    rows = []
    with open(path, "rb") as f:
        for tid, run, off, length in entries:
            f.seek(data_off + off)
            feats = decode_tile(gzip.decompress(f.read(length)))
            if not feats:
                continue
            for t in range(tid, tid + run):
                x, y = tileid_to_xy(t, ZOOM)
                n = 1 << ZOOM
                for layer, p, px, py, extent in feats:
                    # จุดที่อยู่ในขอบ buffer จะซ้ำกับ tile ข้าง ๆ ให้นับเฉพาะใน tile ตัวเอง
                    # (ยกเว้นเส้นถนน/แหล่งน้ำ ที่จุดแรกมักอยู่นอก tile)
                    if layer not in ("transportation_name", "water_name") and not (0 <= px < extent and 0 <= py < extent):
                        continue
                    name = p.get("name:th") or p.get("name")
                    if not name:
                        continue
                    alt = p.get("name:en") or p.get("name_en") or p.get("name") or ""
                    if alt == name:
                        alt = ""
                    lon = (x + px / extent) / n * 360 - 180
                    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + py / extent) / n))))
                    rows.append((name, alt, kind_of(layer, p), round(lon, 5), round(lat, 5)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?", default="web/thailand.pmtiles")
    ap.add_argument("dst", nargs="?", default="web/search-index.json")
    ap.add_argument("--osm", default="data/thailand-latest.osm.pbf",
                    help="ไฟล์ OSM สำหรับขอบเขตจังหวัด (ไม่มีไฟล์ = ไม่ใส่จังหวัด)")
    args = ap.parse_args()
    src, dst = args.src, args.dst
    with open(src, "rb") as f:
        root_off, root_len, leaf_off, data_off = read_header(f)
        entries = all_entries(f, root_off, root_len, leaf_off)
    lo, hi = zoom_base(ZOOM), zoom_base(ZOOM + 1)
    entries = [e for e in entries if lo <= e[0] < hi]
    print(f"z{ZOOM}: {len(entries)} entries", file=sys.stderr)

    chunks = [(src, entries[i:i + 2000], data_off) for i in range(0, len(entries), 2000)]
    seen, items = set(), []
    with Pool() as pool:
        for done, rows in enumerate(pool.imap_unordered(process, chunks), 1):
            for name, alt, kind, lon, lat in rows:
                # ถนนเส้นเดียวกันโผล่หลาย tile -> เก็บ 1 จุดต่อช่องกริดประมาณ 2 กม.
                grid = 50 if kind in ("road", "water") else 10000
                key = (name, kind, round(lon * grid), round(lat * grid))
                if key in seen:
                    continue
                seen.add(key)
                items.append([name, alt, kind, lon, lat])
            print(f"\r  {done}/{len(chunks)} chunks, {len(items)} items", end="", file=sys.stderr)
    print(file=sys.stderr)

    names = []
    if os.path.exists(args.osm):
        print(f"อ่านขอบเขตจังหวัดจาก {args.osm}", file=sys.stderr)
        locator = ProvinceLocator(load_provinces(args.osm))
        # "จังหวัดเชียงใหม่" -> "จ.เชียงใหม่", ส่วน "กรุงเทพมหานคร"/"แขวง..." (ลาว) คงเดิม
        names = ["จ." + n[len("จังหวัด"):] if n.startswith("จังหวัด") else n for n in locator.names]
        for it in items:
            idx = locator.find(it[3], it[4])
            it.append(-1 if idx is None else idx)
        missing = sum(1 for it in items if it[5] < 0)
        print(f"  {len(names)} เขต, ไม่พบจังหวัด {missing} รายการ (เช่น จุดในทะเล)", file=sys.stderr)
    else:
        print(f"ไม่พบ {args.osm} จะไม่ใส่ชื่อจังหวัด", file=sys.stderr)
        for it in items:
            it.append(-1)

    with open(dst, "w", encoding="utf-8") as out:
        json.dump({"provinces": names, "items": items}, out, ensure_ascii=False, separators=(",", ":"))
    print(f"เขียน {dst}: {len(items)} รายการ", file=sys.stderr)


if __name__ == "__main__":
    main()
