"""อ่านขอบเขตจังหวัด (boundary=administrative, admin_level=4) จากไฟล์ OSM .osm.pbf
แล้วใช้หาว่าพิกัดหนึ่งอยู่จังหวัดไหน ใช้แค่ standard library

    locator = ProvinceLocator(load_provinces("data/thailand-latest.osm.pbf"))
    locator.find(100.53, 13.74)  # -> index ใน locator.names หรือ None
"""
import struct
import zlib
from collections import defaultdict
from multiprocessing import Pool

ADMIN_LEVEL = "4"


# ---------- protobuf ----------

def varint(b, i):
    r = s = 0
    while True:
        x = b[i]
        i += 1
        r |= (x & 0x7F) << s
        s += 7
        if x < 0x80:
            return r, i


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


def packed_delta(b):
    # packed sint64 แบบ delta (ใช้กับ id, lat, lon, refs, memids)
    out, i, acc = [], 0, 0
    while i < len(b):
        v, i = varint(b, i)
        acc += (v >> 1) ^ -(v & 1)
        out.append(acc)
    return out


# ---------- OSM PBF ----------

def data_blobs(path):
    """คืน (offset, length) ของทุก blob ชนิด OSMData"""
    out = []
    with open(path, "rb") as f:
        while True:
            head = f.read(4)
            if len(head) < 4:
                return out
            header = f.read(struct.unpack(">I", head)[0])
            btype, size = None, 0
            for num, v in fields(header):
                if num == 1:
                    btype = v.decode()
                elif num == 3:
                    size = v
            if btype == "OSMData":
                out.append((f.tell(), size))
            f.seek(size, 1)


def read_block(path, offset, size):
    with open(path, "rb") as f:
        f.seek(offset)
        blob = f.read(size)
    for num, v in fields(blob):
        if num == 1:
            return v
        if num == 3:
            return zlib.decompress(v)
    raise ValueError("รองรับเฉพาะ blob แบบ raw หรือ zlib")


def block_parts(block):
    strings, groups, gran, lat_off, lon_off = [], [], 100, 0, 0
    for num, v in fields(block):
        if num == 1:
            strings = [s for n, s in fields(v) if n == 1]
        elif num == 2:
            groups.append(v)
        elif num == 17:
            gran = v
        elif num == 19:
            lat_off = v
        elif num == 20:
            lon_off = v
    return strings, groups, gran, lat_off, lon_off


def scan_relations(job):
    path, offset, size = job
    strings, groups, *_ = block_parts(read_block(path, offset, size))
    found = []
    for g in groups:
        for num, rel in fields(g):
            if num != 4:
                continue
            keys = vals = roles = memids = types = ()
            for n, v in fields(rel):
                if n == 2:
                    keys = packed(v)
                elif n == 3:
                    vals = packed(v)
                elif n == 8:
                    roles = packed(v)
                elif n == 9:
                    memids = packed_delta(v)
                elif n == 10:
                    types = packed(v)
            tags = {strings[k].decode(): strings[x].decode() for k, x in zip(keys, vals)}
            if tags.get("boundary") != "administrative" or tags.get("admin_level") != ADMIN_LEVEL:
                continue
            ways = [m for m, t, r in zip(memids, types, roles)
                    if t == 1 and strings[r] in (b"outer", b"inner", b"")]
            found.append((tags.get("name:th") or tags.get("name", ""), tags.get("name:en", ""), ways))
    return found


_wanted = None


def _init(wanted):
    global _wanted
    _wanted = wanted


def scan_ways(job):
    path, offset, size = job
    _, groups, *_ = block_parts(read_block(path, offset, size))
    found = {}
    for g in groups:
        for num, way in fields(g):
            if num != 3:
                continue
            wid, refs = None, None
            for n, v in fields(way):
                if n == 1:
                    if v not in _wanted:
                        break
                    wid = v
                elif n == 8:
                    refs = v
            if wid is not None and refs is not None:
                found[wid] = packed_delta(refs)
    return found


def scan_nodes(job):
    path, offset, size = job
    _, groups, gran, lat_off, lon_off = block_parts(read_block(path, offset, size))
    found = {}
    for g in groups:
        for num, dense in fields(g):
            if num != 2:
                continue
            parts = dict((n, v) for n, v in fields(dense) if n in (1, 8, 9))
            ids = packed_delta(parts.get(1, b""))
            hits = [i for i, nid in enumerate(ids) if nid in _wanted]
            if not hits:
                continue
            lats, lons = packed_delta(parts[8]), packed_delta(parts[9])
            for i in hits:
                found[ids[i]] = (1e-9 * (lon_off + gran * lons[i]), 1e-9 * (lat_off + gran * lats[i]))
    return found


def assemble_rings(ways):
    """ต่อเส้น way ให้เป็น ring ปิด (ไม่สนใจ outer/inner เพราะใช้กฎ even-odd ตอนทดสอบจุด)"""
    rings, pool = [], [w for w in ways if len(w) >= 2]
    while pool:
        ring = list(pool.pop())
        changed = True
        while ring[0] != ring[-1] and changed:
            changed = False
            for k, w in enumerate(pool):
                if w[0] == ring[-1]:
                    ring += w[1:]
                elif w[-1] == ring[-1]:
                    ring += w[-2::-1]
                elif w[-1] == ring[0]:
                    ring = w[:-1] + ring
                elif w[0] == ring[0]:
                    ring = w[:0:-1] + ring
                else:
                    continue
                pool.pop(k)
                changed = True
                break
        if ring[0] != ring[-1]:
            ring.append(ring[0])   # เส้นขาด (ข้อมูลไม่ครบ) ปิดให้เอง
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def load_provinces(path):
    """คืน [(ชื่อไทย, ชื่ออังกฤษ, [ring ของ (lon, lat)])]"""
    jobs = [(path, o, s) for o, s in data_blobs(path)]
    with Pool() as pool:
        rels = [r for part in pool.map(scan_relations, jobs, chunksize=16) for r in part]
    way_ids = {w for _, _, ws in rels for w in ws}
    with Pool(initializer=_init, initargs=(way_ids,)) as pool:
        ways = {}
        for part in pool.imap_unordered(scan_ways, jobs, chunksize=16):
            ways.update(part)
    node_ids = {n for refs in ways.values() for n in refs}
    with Pool(initializer=_init, initargs=(node_ids,)) as pool:
        nodes = {}
        for part in pool.imap_unordered(scan_nodes, jobs, chunksize=16):
            nodes.update(part)

    out = []
    for name, name_en, ws in rels:
        lines = [[n for n in ways[w] if n in nodes] for w in ws if w in ways]
        rings = [[nodes[n] for n in r] for r in assemble_rings(lines)]
        if rings:
            out.append((name, name_en, rings))
    return out


class ProvinceLocator:
    """หาจังหวัดของพิกัดด้วย ray casting โดยแบ่ง edge เป็นแถบละติจูดให้เช็กเฉพาะ edge ที่เกี่ยวข้อง"""
    BAND = 100   # 1/100 องศา ต่อแถบ

    def __init__(self, provinces):
        self.names = [p[0] for p in provinces]
        self.shapes = []
        for _, _, rings in provinces:
            bands = defaultdict(list)
            xs = [x for r in rings for x, _ in r]
            ys = [y for r in rings for _, y in r]
            for r in rings:
                for (x1, y1), (x2, y2) in zip(r, r[1:]):
                    for b in range(int(min(y1, y2) * self.BAND), int(max(y1, y2) * self.BAND) + 1):
                        bands[b].append((x1, y1, x2, y2))
            self.shapes.append(((min(xs), min(ys), max(xs), max(ys)), bands))

    def find(self, lon, lat):
        band = int(lat * self.BAND)
        for idx, ((x0, y0, x1, y1), bands) in enumerate(self.shapes):
            if not (x0 <= lon <= x1 and y0 <= lat <= y1):
                continue
            inside = False
            for ax, ay, bx, by in bands.get(band, ()):
                if (ay > lat) != (by > lat) and lon < ax + (lat - ay) * (bx - ax) / (by - ay):
                    inside = not inside
            if inside:
                return idx
        return None
