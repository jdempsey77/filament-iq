import urllib.request
import json
import re
import sys

SPOOLMAN = "http://192.168.4.124:7912"
FILAMENTS_JSON = "/Users/jdempsey/code/filament-profiles-data/filaments.json"

data = json.loads(urllib.request.urlopen(f"{SPOOLMAN}/api/v1/filament?limit=200").read())
if isinstance(data, dict):
    data = data.get("items", data)

profiles = json.load(open(FILAMENTS_JSON))["filaments"]

KEYWORDS = [
    "matte", "silk", "sparkle", "glitter", "glow", "marble", "wood", "metal",
    "carbon", "fiber", "filled", "flex", "high speed", "rapid", "plus", "basic",
    "galaxy", "rainbow", "multicolor", "dual", "gradient", "tough", "transparent",
    "clear", "regular", "high-flow", "hf", "hs",
]


def normalize(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def extract_type(name):
    n = name.lower()
    for kw in KEYWORDS:
        if kw in n:
            return kw
    return None


def match(vendor, material, name):
    nb = normalize(vendor)
    nm = normalize(material)
    nt = extract_type(name)
    best_score = 0.0
    best = None
    for p in profiles:
        if p.get("brand_key") != nb:
            continue
        if p.get("material_key") != nm:
            continue
        type_score = 0.0
        if nt and p.get("material_type_key"):
            pk = p.get("material_type_key", "")
            if nt in pk or pk in nt:
                type_score = 0.4
        score = 0.6 + type_score
        if score > best_score:
            best_score = score
            best = p
    if best is None:
        return "none", 0.0
    if best_score >= 0.9:
        return "high", best_score
    if best_score >= 0.7:
        return "medium", best_score
    return "low", best_score


counts = {"high": 0, "medium": 0, "low": 0, "none": 0}

for f in sorted(data, key=lambda x: x.get("id", 0)):
    vendor = (f.get("vendor") or {}).get("name", "Unknown")
    material = f.get("material", "")
    name = f.get("name", "")
    conf, score = match(vendor, material, name)
    counts[conf] += 1
    flag = "  <--" if conf in ("low", "none") else ""
    print(f"  [{f['id']:3}] {conf:6} {score:.2f}  {vendor:12} {material:6} {name}{flag}")

total = sum(counts.values())
enhanced = counts["high"] + counts["medium"]
print()
print(f"high={counts['high']} medium={counts['medium']} low={counts['low']} none={counts['none']}")
print(f"Enhanced labels: {enhanced}/{total} ({enhanced/total*100:.0f}%)")
