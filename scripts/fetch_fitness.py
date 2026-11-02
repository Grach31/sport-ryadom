#!/usr/bin/env python3
"""Собирает из OpenStreetMap фитнес-клубы, залы и спорткомплексы внутри контура Хостинского района.
Результат: data/fitness_raw.json (запускается в GitHub Actions, где есть интернет)."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_map as B

ROOT = B.ROOT

def main():
    boundary = B.find_boundary()
    if boundary:
        lons = [p[0] for p in boundary]; lats = [p[1] for p in boundary]
        bbox = (min(lats), min(lons), max(lats), max(lons))
    else:
        bbox = B.FALLBACK_BBOX
    s, w, n, e = bbox
    bb = f"({s},{w},{n},{e})"
    q = f"""[out:json][timeout:240];
(
 nwr["leisure"="fitness_centre"]{bb};
 nwr["leisure"="sports_centre"]{bb};
 nwr["leisure"="sports_hall"]{bb};
 nwr["sport"~"fitness|gymnastics|crossfit|boxing|martial_arts|yoga|pilates|dance|swimming"]["leisure"!="pitch"]["leisure"!="track"]{bb};
 nwr["amenity"="gym"]{bb};
 nwr["leisure"="swimming_pool"]["access"!="private"]{bb};
 nwr["amenity"~"dojo|dancing_school"]{bb};
 nwr["name"~"фитнес|fitness|GYM|Gym|тренажерн|тренажёрн|кроссфит|CrossFit|йога|пилатес|спорт ?клуб|спорткомплекс|боксёрск|бокс"]{bb};
);
out center tags;"""
    data = B.overpass(q)
    res = []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
        if c.get("lat") is None:
            continue
        inside = B.pip(c["lon"], c["lat"], boundary) if boundary else True
        res.append({
            "osm": f"{el['type']}/{el['id']}",
            "inside_district": inside,
            "name": t.get("name"), "name_ru": t.get("name:ru"),
            "leisure": t.get("leisure"), "amenity": t.get("amenity"), "sport": t.get("sport"),
            "addr": " ".join(x for x in [t.get("addr:street"), t.get("addr:housenumber")] if x) or None,
            "suburb": t.get("addr:suburb") or t.get("addr:city_district"),
            "lat": c["lat"], "lon": c["lon"],
            "opening_hours": t.get("opening_hours"), "phone": t.get("phone") or t.get("contact:phone"),
            "website": t.get("website") or t.get("contact:website"),
            "fee": t.get("fee"), "access": t.get("access"), "operator": t.get("operator"),
            "description": t.get("description"), "building": t.get("building"),
            "other": {k: v for k, v in t.items() if k in ("brand", "level", "wheelchair", "indoor", "email", "contact:vk", "contact:instagram", "contact:telegram")},
        })
    res.sort(key=lambda r: (not r["inside_district"], r["name"] is None, r["name"] or ""))
    with open(os.path.join(ROOT, "data/fitness_raw.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print("всего объектов:", len(res), "внутри района:", sum(r["inside_district"] for r in res), "с названием:", sum(1 for r in res if r["name"]))

if __name__ == "__main__":
    main()
