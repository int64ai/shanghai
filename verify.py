#!/usr/bin/env python3
"""
Triple-source coordinate verifier for Shanghai travel map.
Uses: Photon, Nominatim, Tavily (if available)
Output: JSON with verified coordinates per location
"""
import urllib.request, urllib.parse, json, time, sys, os

TAVILY_KEY = os.environ.get("TAVILY_API_KEY", "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")

def photon_search(query, limit=3):
    """Photon geocoder (Komoot, based on OSM)"""
    url = f"https://photon.komoot.io/api/?q={urllib.parse.quote(query)}&limit={limit}&lang=en&lat=31.23&lon=121.47"
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"openclaw-verify/1.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        results = []
        for f in data.get("features", []):
            coords = f["geometry"]["coordinates"]
            props = f.get("properties", {})
            results.append({
                "lat": round(coords[1], 5),
                "lng": round(coords[0], 5),
                "name": props.get("name", ""),
                "street": props.get("street", ""),
                "city": props.get("city", ""),
                "type": props.get("osm_value", ""),
            })
        return results
    except Exception as e:
        return [{"error": str(e)}]

def nominatim_search(query, limit=3):
    """Nominatim geocoder (OpenStreetMap official)"""
    url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(query)}&format=json&limit={limit}&addressdetails=1&viewbox=120.8,30.7,122.0,31.9&bounded=1"
    try:
        req = urllib.request.Request(url, headers={"User-Agent":"openclaw-verify/1.0"})
        data = json.loads(urllib.request.urlopen(req, timeout=10).read())
        results = []
        for item in data:
            results.append({
                "lat": round(float(item["lat"]), 5),
                "lng": round(float(item["lon"]), 5),
                "name": item.get("display_name", "")[:80],
                "type": item.get("type", ""),
            })
        return results
    except Exception as e:
        return [{"error": str(e)}]

def tavily_search(query):
    """Tavily search for address verification"""
    try:
        url = "https://api.tavily.com/search"
        payload = json.dumps({
            "query": query,
            "max_results": 3,
            "search_depth": "basic",
            "include_answer": True
        }).encode()
        req = urllib.request.Request(url, data=payload, headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {TAVILY_KEY}"
        })
        data = json.loads(urllib.request.urlopen(req, timeout=15).read())
        return {
            "answer": data.get("answer", ""),
            "results": [{"title": r.get("title",""), "url": r.get("url","")} for r in data.get("results",[])]
        }
    except Exception as e:
        return {"error": str(e)}

def distance_m(lat1, lng1, lat2, lng2):
    """Haversine distance in meters"""
    import math
    R = 6371000
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def verify_location(loc):
    """Verify one location using triple sources"""
    result = {
        "id": loc["id"],
        "cn": loc["cn"],
        "addr": loc["addr"],
        "current": {"lat": loc["lat"], "lng": loc["lng"]},
        "sources": {},
        "verdict": None
    }

    # Source 1: Photon (by name)
    time.sleep(1.2)
    p1 = photon_search(f"{loc['cn']} 上海")
    result["sources"]["photon_name"] = p1[:2] if p1 else []

    # Source 2: Photon (by address)
    time.sleep(1.2)
    p2 = photon_search(loc["addr"])
    result["sources"]["photon_addr"] = p2[:2] if p2 else []

    # Source 3: Nominatim (by name)
    time.sleep(1.2)
    n1 = nominatim_search(f"{loc['cn']} 上海市")
    result["sources"]["nominatim"] = n1[:2] if n1 else []

    # Source 4: Tavily (for cross-reference)
    time.sleep(0.5)
    t1 = tavily_search(f"{loc['cn']} {loc['addr']} GPS坐标 经纬度")
    result["sources"]["tavily"] = t1

    # Analyze: collect all valid lat/lng from sources
    candidates = []
    for src_name in ["photon_name", "photon_addr", "nominatim"]:
        for item in result["sources"].get(src_name, []):
            if "lat" in item and "lng" in item and not item.get("error"):
                # Only consider results in Shanghai area
                if 30.5 < item["lat"] < 31.8 and 120.5 < item["lng"] < 122.5:
                    candidates.append({
                        "source": src_name,
                        "lat": item["lat"],
                        "lng": item["lng"],
                        "name": item.get("name", "")
                    })

    result["candidates"] = candidates

    if not candidates:
        result["verdict"] = {"status": "NO_DATA", "lat": loc["lat"], "lng": loc["lng"]}
        return result

    # Find consensus: group candidates within 200m of each other
    groups = []
    used = set()
    for i, c in enumerate(candidates):
        if i in used:
            continue
        group = [c]
        used.add(i)
        for j, c2 in enumerate(candidates):
            if j in used:
                continue
            if distance_m(c["lat"], c["lng"], c2["lat"], c2["lng"]) < 200:
                group.append(c2)
                used.add(j)
        groups.append(group)

    # Pick the largest group (most agreement)
    groups.sort(key=len, reverse=True)
    best = groups[0]
    avg_lat = round(sum(c["lat"] for c in best) / len(best), 5)
    avg_lng = round(sum(c["lng"] for c in best) / len(best), 5)

    dist_from_current = distance_m(loc["lat"], loc["lng"], avg_lat, avg_lng)

    result["verdict"] = {
        "status": "OK" if dist_from_current < 300 else "MISMATCH",
        "lat": avg_lat,
        "lng": avg_lng,
        "agreement": len(best),
        "total_candidates": len(candidates),
        "distance_from_current_m": round(dist_from_current),
        "sources_agreed": list(set(c["source"] for c in best))
    }

    return result

# Main
if __name__ == "__main__":
    locs_file = sys.argv[1] if len(sys.argv) > 1 else "/tmp/shanghai/verify-locations.json"
    start_idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    end_idx = int(sys.argv[3]) if len(sys.argv) > 3 else 999

    with open(locs_file) as f:
        locations = json.load(f)

    locations = locations[start_idx:end_idx]
    results = []

    for loc in locations:
        print(f"Verifying {loc['id']}: {loc['cn']}...", file=sys.stderr)
        r = verify_location(loc)
        v = r["verdict"]
        status = v["status"]
        if status == "OK":
            print(f"  ✅ OK (Δ{v['distance_from_current_m']}m, {v['agreement']}/{v['total_candidates']} agree)", file=sys.stderr)
        elif status == "MISMATCH":
            print(f"  ❌ MISMATCH! Δ{v['distance_from_current_m']}m → [{v['lat']}, {v['lng']}] ({v['agreement']}/{v['total_candidates']} agree)", file=sys.stderr)
        else:
            print(f"  ⚠️ {status}", file=sys.stderr)
        results.append(r)

    # Output
    print(json.dumps(results, ensure_ascii=False, indent=2))
