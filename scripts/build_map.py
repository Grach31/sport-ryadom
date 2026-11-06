#!/usr/bin/env python3
"""Собирает data/map.json: контур Хостинского района, берег, дороги, реки из OpenStreetMap
и координаты 45 площадок (геокодер Nominatim). Запускается в GitHub Actions (там есть интернет).

Ручные поправки: data/coords_override.json  ->  {"12": [43.5521, 39.8123], ...}  (id площадки: [широта, долгота])
"""
import json, math, os, re, sys, time, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "sport-ryadom/1.0 (https://github.com/Grach31/sport-ryadom)"
OVERPASS = ["https://overpass-api.de/api/interpreter",
            "https://overpass.kumi.systems/api/interpreter",
            "https://overpass.private.coffee/api/interpreter"]
NOMINATIM = "https://nominatim.openstreetmap.org/search"
FALLBACK_BBOX = (43.43, 39.62, 43.70, 40.02)  # юг, запад, север, восток — на случай, если контур не найден
SIDE = 2600.0   # длинная сторона карты в условных пикселях
PAD = 70.0


# ------------------------------------------------------------------ сеть
def http(url, data=None, timeout=300):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def overpass(query):
    last = None
    for attempt in range(3):
        for ep in OVERPASS:
            try:
                return json.loads(http(ep, urllib.parse.urlencode({"data": query}).encode()))
            except Exception as e:  # noqa
                last = e
                print("overpass fail", ep, e, file=sys.stderr)
                time.sleep(5)
    raise RuntimeError(f"Overpass недоступен: {last}")


def nominatim(q, bbox):
    s, w, n, e = bbox
    params = {"q": q, "format": "json", "limit": 1, "countrycodes": "ru", "accept-language": "ru",
              "viewbox": f"{w},{n},{e},{s}", "bounded": 1}
    time.sleep(1.1)  # правила Nominatim: не чаще 1 запроса в секунду
    try:
        res = json.loads(http(NOMINATIM + "?" + urllib.parse.urlencode(params), timeout=60))
    except Exception as e:  # noqa
        print("nominatim fail", q, e, file=sys.stderr)
        return None
    if not res:
        return None
    return float(res[0]["lat"]), float(res[0]["lon"]), res[0].get("type", "")


# ------------------------------------------------------------------ геометрия
def dp(points, tol):
    """Дуглас–Пекер (для замкнутых контуров делим пополам)."""
    if len(points) < 3:
        return points
    if len(points) > 4 and points[0] == points[-1]:
        m = len(points) // 2
        return dp(points[:m + 1], tol)[:-1] + dp(points[m:], tol)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy) or 1e-9
        idx, dmax = -1, 0.0
        for i in range(a + 1, b):
            px, py = points[i]
            d = abs(dy * (px - ax) - dx * (py - ay)) / L
            if d > dmax:
                idx, dmax = i, d
        if dmax > tol and idx > 0:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return [p for p, k in zip(points, keep) if k]


def path_d(points, close=False):
    if len(points) < 2:
        return ""
    s = "M" + " ".join(f"{x:.1f},{y:.1f}" for x, y in points[:1])
    s += "L" + " ".join(f"{x:.1f},{y:.1f}" for x, y in points[1:])
    return s + ("Z" if close else "")


def join_chains(ways, key_round=7):
    """Склеивает линии, у которых совпадают концы (для береговой линии и границы)."""
    key = lambda p: (round(p[0], key_round), round(p[1], key_round))
    chains = [list(w) for w in ways if len(w) > 1]
    changed = True
    while changed:
        changed = False
        for i in range(len(chains)):
            a = chains[i]
            if a is None:
                continue
            for j in range(len(chains)):
                if i == j or chains[j] is None:
                    continue
                b = chains[j]
                if key(a[-1]) == key(b[0]):
                    chains[i] = a + b[1:]
                elif key(a[-1]) == key(b[-1]):
                    chains[i] = a + b[::-1][1:]
                elif key(a[0]) == key(b[-1]):
                    chains[i] = b + a[1:]
                elif key(a[0]) == key(b[0]):
                    chains[i] = b[::-1] + a[1:]
                else:
                    continue
                chains[j] = None
                changed = True
                break
            if changed:
                break
    return [c for c in chains if c]


def geom(el):
    return [(g["lon"], g["lat"]) for g in el.get("geometry", [])]


def pip(x, y, poly):
    ins = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi:
            ins = not ins
        j = i
    return ins


def nearest_inside(x, y, poly):
    """ближайшая к точке (x, y) точка на границе района, сдвинутая на несколько пикселей внутрь"""
    best, bp = 1e18, (x, y)
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        dx, dy = x2 - x1, y2 - y1
        L = dx * dx + dy * dy
        t = 0 if L == 0 else max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / L))
        px, py = x1 + t * dx, y1 + t * dy
        d = (x - px) ** 2 + (y - py) ** 2
        if d < best:
            best, bp = d, (px, py)
    px, py = bp
    d = math.sqrt(best) or 1.0
    ux, uy = (px - x) / d, (py - y) / d          # от исходной точки к границе и дальше внутрь
    for off in (4, 7, 12, 20):
        nx, ny = px + ux * off, py + uy * off
        if pip(nx, ny, poly):
            return nx, ny
    return px, py


def snap_to_land(x, y, coast_pts, sea_poly):
    """Если точка попала в море — возвращает ближайшую точку суши у берега."""
    if not sea_poly or not pip(x, y, sea_poly):
        return x, y
    best, bp = 1e18, None
    for (x0, y0), (x1, y1) in zip(coast_pts, coast_pts[1:]):
        dx, dy = x1 - x0, y1 - y0
        L = dx * dx + dy * dy or 1e-9
        t = max(0.0, min(1.0, ((x - x0) * dx + (y - y0) * dy) / L))
        px, py = x0 + t * dx, y0 + t * dy
        d = math.hypot(x - px, y - py)
        if d < best:
            best, bp = d, (px, py)
    px, py = bp
    ux, uy = (px - x) / (best or 1.0), (py - y) / (best or 1.0)   # направление от моря к берегу
    for off in (12, 22, 36, 60, 90):
        nx, ny = px + ux * off, py + uy * off
        if not pip(nx, ny, sea_poly):
            return nx, ny
    return px, py


# ------------------------------------------------------------------ основная сборка
def find_boundary():
    q = '[out:json][timeout:120];rel["boundary"="administrative"]["name"~"Хостинск"](43.2,39.3,43.9,40.4);out geom;'
    data = overpass(q)
    best = None
    for el in data.get("elements", []):
        if el.get("type") != "relation":
            continue
        name = el.get("tags", {}).get("name", "")
        if "Хостинск" not in name:
            continue
        outer = [geom(m) for m in el.get("members", []) if m.get("role") == "outer" and m.get("geometry")]
        rings = join_chains(outer)
        if not rings:
            continue
        ring = max(rings, key=len)
        if best is None or len(ring) > len(best):
            best = ring
    return best


def build():
    boundary = find_boundary()
    if boundary:
        lons = [p[0] for p in boundary]
        lats = [p[1] for p in boundary]
        mlat, mlon = (max(lats) - min(lats)) * 0.04, (max(lons) - min(lons)) * 0.04
        bbox = (min(lats) - mlat, min(lons) - mlon, max(lats) + mlat, max(lons) + mlon)
        print("контур района найден, точек:", len(boundary))
    else:
        bbox = FALLBACK_BBOX
        print("контур района НЕ найден, беру прямоугольник", bbox)
    s, w, n, e = bbox
    bb = f"({s},{w},{n},{e})"
    query = f"""[out:json][timeout:300];
(
 way["natural"="coastline"]{bb};
 way["highway"~"^(motorway|trunk|primary|secondary|tertiary|unclassified|residential|living_street|motorway_link|trunk_link|primary_link)$"]{bb};
 way["waterway"~"^(river|stream)$"]{bb};
 way["natural"="water"]{bb};
 way["landuse"~"^(forest|recreation_ground)$"]{bb};
 way["natural"="wood"]{bb};
 way["leisure"~"^(park|garden)$"]{bb};
);
out geom qt;"""
    raw = overpass(query)
    return bbox, boundary, raw.get("elements", [])


def make_projection(bbox):
    s, w, n, e = bbox
    latm = (s + n) / 2
    kx = math.cos(math.radians(latm))
    width_deg, height_deg = (e - w) * kx, (n - s)
    k = (SIDE - 2 * PAD) / max(width_deg, height_deg)
    WW = int(width_deg * k + 2 * PAD)
    WH = int(height_deg * k + 2 * PAD)

    def proj(lon, lat):
        return (PAD + (lon - w) * kx * k, PAD + (n - lat) * k)

    return proj, WW, WH


def process(bbox, boundary, elements, places, override, cache, gyms=()):
    proj, WW, WH = make_projection(bbox)
    P = lambda pts: [proj(lon, lat) for lon, lat in pts]

    coast_ways, roads = [], {"major": [], "mid": [], "minor": []}
    rivers, lakes, parks = [], [], []
    for el in elements:
        if el.get("type") != "way" or not el.get("geometry"):
            continue
        t = el.get("tags", {})
        g = geom(el)
        if t.get("natural") == "coastline":
            coast_ways.append(g)
        elif "highway" in t:
            h = t["highway"]
            cls = "major" if h in ("motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link") else \
                  "mid" if h in ("secondary", "tertiary") else "minor"
            roads[cls].append(dp(P(g), 1.2 if cls != "minor" else 1.6))
        elif t.get("waterway"):
            rivers.append((t["waterway"], dp(P(g), 1.5)))
        elif t.get("natural") == "water":
            lakes.append(dp(P(g), 1.5))
        else:
            parks.append(dp(P(g), 2.0))

    # --- море
    sea = coast = ""
    sea_poly, coast_pts = None, []
    chains = join_chains(coast_ways)
    if chains:
        main = max(chains, key=len)
        pts = dp(P(main), 1.2)
        coast = path_d(pts)
        if len(pts) > 2:
            (x0, y0), (x1, y1) = pts[0], pts[-1]
            ax, ay = pts[min(3, len(pts) - 1)][0] - x0, pts[min(3, len(pts) - 1)][1] - y0
            bx, by = x1 - pts[max(-4, -len(pts))][0], y1 - pts[max(-4, -len(pts))][1]
            la, lb = math.hypot(ax, ay) or 1, math.hypot(bx, by) or 1
            ax, ay, bx, by = ax / la, ay / la, bx / lb, by / lb
            # OSM: суша слева по ходу линии, вода справа. Правая нормаль в экранных координатах (y вниз): (-dy, dx)
            rn = (-by, bx)
            BIG = 9000
            q1 = (x1 + bx * BIG, y1 + by * BIG)
            q2 = (q1[0] + rn[0] * BIG, q1[1] + rn[1] * BIG)
            q3 = (x0 - ax * BIG + rn[0] * BIG, y0 - ay * BIG + rn[1] * BIG)
            q4 = (x0 - ax * BIG, y0 - ay * BIG)
            sea = path_d(pts + [q1, q2, q3, q4], close=True)
            sea_poly, coast_pts = pts + [q1, q2, q3, q4], pts

    # --- район
    district = path_d(dp(P(boundary), 1.0), close=True) if boundary else ""
    dist_poly = P(boundary) if boundary else None
    MIN_GAP = 9   # маркеры почти не раздвигаем: близкие на карте объединяет в кружок сам сайт (index.html)

    def bad_spot(cx, cy, inside0, placed):
        """точка занята другим маркером, лежит в море или (если исходная была в районе) вышла за границу района"""
        if any(math.hypot(cx - a, cy - b) < MIN_GAP for a, b in placed):
            return True
        if sea_poly and pip(cx, cy, sea_poly):
            return True
        return bool(inside0 and dist_poly and not pip(cx, cy, dist_poly))

    # --- площадки
    pl_out = {}
    reg_pts = {}
    for p in places:
        pid = str(p["id"])
        ll, prec = None, None
        if pid in override:
            ll, prec = (override[pid][0], override[pid][1]), "manual"
        else:
            q = p["q"]
            street = re.sub(r",\s*\d+[0-9а-яА-Яa-zA-Z/–\- ]*$", "", q)
            vill = re.search(r"с\. [А-Яа-яЁё\- ]+", q)
            tries = [(q, "house"), (street, "street")]
            if vill:
                tries.append(("Сочи, " + vill.group(0).strip(), "village"))
            for text, level in tries:
                if text in cache:
                    r = cache[text]
                else:
                    r = nominatim(text, bbox)
                    cache[text] = r
                if r:
                    ll, prec = (r[0], r[1]), level
                    break
        if ll:
            x, y = proj(ll[1], ll[0])
            pl_out[pid] = {"x": round(x, 1), "y": round(y, 1), "prec": prec}
            reg_pts.setdefault(p["reg"], []).append((x, y))
    # площадки без координат — рядом с другими из того же микрорайона / из центра района
    allx = [v["x"] for v in pl_out.values()] or [WW / 2]
    ally = [v["y"] for v in pl_out.values()] or [WH / 2]
    for p in places:
        pid = str(p["id"])
        if pid in pl_out:
            continue
        pts = reg_pts.get(p["reg"])
        cx = sum(a for a, _ in pts) / len(pts) if pts else sum(allx) / len(allx)
        cy = sum(b for _, b in pts) / len(pts) if pts else sum(ally) / len(ally)
        pl_out[pid] = {"x": round(cx, 1), "y": round(cy, 1), "prec": "region"}
    # точки, попавшие в море, возвращаем на берег; близкие маркеры раздвигаем, не загоняя их в воду
    placed = []
    for pid in sorted(pl_out, key=int):
        v = pl_out[pid]
        x, y = snap_to_land(v["x"], v["y"], coast_pts, sea_poly)
        cx, cy, k = x, y, 0
        inside0 = bool(dist_poly and pip(x, y, dist_poly))
        while bad_spot(cx, cy, inside0, placed) and k < 60:
            k += 1
            ang, rad = k * 2.4, MIN_GAP + 3 * k
            cx, cy = x + rad * math.cos(ang), y + rad * math.sin(ang)
        cx, cy = snap_to_land(cx, cy, coast_pts, sea_poly)
        v["x"], v["y"] = round(cx, 1), round(cy, 1)
        placed.append((cx, cy))

    # залы и фитнес-клубы: координаты либо заданы в gyms.json (lat/lon), либо находим по адресу (поле q или addr)
    gy_out = {}
    for g in gyms:
        ll = None
        if g.get("lat") is not None and g.get("lon") is not None:
            ll = (g["lat"], g["lon"])
        else:
            q = g.get("q") or (("Сочи, " + g["addr"].split(";")[0]) if g.get("addr") else None)
            if q:
                street = re.sub(r",\s*\d+[0-9а-яА-Яa-zA-Z/–\- ]*$", "", q)
                house = re.sub(r"(\d+[А-Яа-яA-Za-z]?)\s*[/,].*$", r"\1", q)
                tries = []
                for t in (q, house, street):
                    if t and t not in tries:
                        tries.append(t)
                for text in tries:
                    if text in cache:
                        r = cache[text]
                    else:
                        r = nominatim(text, bbox)
                        cache[text] = r
                    if r:
                        ll = (r[0], r[1])
                        break
        if not ll:
            print("зал без координат, на карту не попал:", g["id"], g["name"], file=sys.stderr)
            continue
        x, y = proj(ll[1], ll[0])
        if g.get("pin_inside") and dist_poly and not pip(x, y, dist_poly):
            x, y = nearest_inside(x, y, dist_poly)   # адрес на самой границе: маркер ставим чуть внутрь района
        x, y = snap_to_land(x, y, coast_pts, sea_poly)
        cx, cy, k = x, y, 0
        inside0 = bool(dist_poly and pip(x, y, dist_poly))
        while bad_spot(cx, cy, inside0, placed) and k < 60:
            k += 1
            ang, rad = k * 2.4 + 1.0, MIN_GAP + 3 * k
            cx, cy = x + rad * math.cos(ang), y + rad * math.sin(ang)
        cx, cy = snap_to_land(cx, cy, coast_pts, sea_poly)
        placed.append((cx, cy))
        gy_out[str(g["id"])] = {"x": round(cx, 1), "y": round(cy, 1), "lat": round(ll[0], 6), "lon": round(ll[1], 6)}

    out = {
        "WW": WW, "WH": WH,
        "sea": sea, "coast": coast, "district": district,
        "parks": [path_d(p, True) for p in parks if len(p) > 2],
        "lakes": [path_d(p, True) for p in lakes if len(p) > 2],
        "rivers": [{"k": k, "d": path_d(p)} for k, p in rivers],
        "roads": {k: "".join(path_d(w) for w in v if len(w) > 1) for k, v in roads.items()},
        "places": pl_out,
        "gyms": gy_out,
        "built": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "source": "© участники OpenStreetMap",
    }
    return out


def main():
    places = json.load(open(os.path.join(ROOT, "data/places.json"), encoding="utf-8"))
    ov_path = os.path.join(ROOT, "data/coords_override.json")
    override = json.load(open(ov_path)) if os.path.exists(ov_path) else {}
    cache_path = os.path.join(ROOT, "data/geocode_cache.json")
    cache = json.load(open(cache_path, encoding="utf-8")) if os.path.exists(cache_path) else {}
    gp = os.path.join(ROOT, "data/gyms.json")
    gyms = json.load(open(gp, encoding="utf-8")) if os.path.exists(gp) else []
    bbox, boundary, elements = build()
    result = process(bbox, boundary, elements, places, override, cache, gyms)
    json.dump(cache, open(cache_path, "w", encoding="utf-8"), ensure_ascii=False, indent=0)
    with open(os.path.join(ROOT, "data/map.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, separators=(",", ":"))
    precs = {}
    for v in result["places"].values():
        precs[v["prec"]] = precs.get(v["prec"], 0) + 1
    print("карта готова:", result["WW"], "x", result["WH"], "точность площадок:", precs)


if __name__ == "__main__":
    main()
