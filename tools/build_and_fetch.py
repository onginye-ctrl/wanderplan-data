#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建 Discover.json + 批量抓取景点图片上传到本仓库
用法:
  python3 tools/build_and_fetch.py build   # 生成 Discover.json（合并旧数据+新增数据）
  python3 tools/build_and_fetch.py fetch   # 抓取图片 → images/<id>.jpg，回填 URL
  python3 tools/build_and_fetch.py backfill # 只回填 URL（基于已下载的图片）
"""
import json, os, re, sys, time, hashlib, subprocess, urllib.request, urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_JSON = "/Users/cashbox/Documents/AI项目/个人作品集/temp_20261019_01/WanderPlan/Resources/Discover.json"
BUNDLE_IMG_DIR = "/Users/cashbox/Documents/AI项目/个人作品集/temp_20261019_01/WanderPlan/Resources/AttractionImages"
IMAGES_DIR = os.path.join(ROOT, "images")
FAILED_LOG = os.path.join(ROOT, "tools", "failed.jsonl")
OVERRIDES = os.path.join(ROOT, "tools", "image_overrides.json")  # id -> commons搜索词 或 https直链

CITIES = ["北京", "上海", "广州", "深圳", "南京", "武汉", "苏州", "杭州",
          "成都", "重庆", "西安", "长沙", "厦门", "昆明", "大理", "丽江", "三亚", "桂林"]

CDN = "https://cdn.jsdelivr.net/gh/onginye-ctrl/wanderplan-data@main/images/{}.jpg"

VALID_CATEGORIES = {"landmark","palace","temple","park","scenicArea","lake","museum",
                    "amusementPark","greatWall","garden","village","beach","zoo",
                    "aquarium","theater","market","shopping","island"}

def build():
    with open(WORKSPACE_JSON, encoding="utf-8") as f:
        old = json.load(f)
    existing = {a["id"]: a for a in old["attractions"]}

    attractions = []
    seen = set()
    # 旧数据（35个）保持顺序在前
    for a in old["attractions"]:
        attractions.append(a)
        seen.add(a["id"])

    for part in ["data_part1", "data_part2", "data_part3", "data_part4"]:
        mod = __import__(part)
        for row in mod.DATA:
            aid, name, cat, lat, lng, desc, hours, price, info = row
            if aid in seen:
                continue
            if not name or lat == 0:
                continue
            assert cat in VALID_CATEGORIES, f"bad category {cat} {aid}"
            assert isinstance(lat, float) and isinstance(lng, float), f"bad coord {aid}"
            city = name and None
            # city 由 id 前缀推断
            prefix_city = {"cd":"成都","cq":"重庆","xa":"西安","gz":"广州","sz":"深圳",
                           "wh":"武汉","cs":"长沙","nj":"南京","su":"苏州","bj":"北京",
                           "sh":"上海","hz":"杭州","xm":"厦门","km":"昆明","dl":"大理",
                           "lj":"丽江","sy":"三亚","gl":"桂林"}
            pfx = aid.split("-")[0]
            city = prefix_city[pfx]
            info = info or "免费开放，节假日建议错峰出行"
            attractions.append({
                "id": aid, "city": city, "name": name, "category": cat,
                "imageURL": "", "thumbnailURL": "",
                "latitude": lat, "longitude": lng,
                "description": desc, "openHours": hours,
                "ticketPrice": price, "ticketInfo": info,
            })
            seen.add(aid)

    data = {"cities": CITIES, "attractions": attractions}
    out = os.path.join(ROOT, "Discover.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    from collections import Counter
    print(f"OK {len(attractions)} attractions -> {out}")
    print(Counter(a["city"] for a in attractions))

# ---------------- 图片抓取 ----------------

UA = {"User-Agent": "WanderPlanImageDataBot/1.0 (contact: dev@wanderplan.app) python-urllib"}
BAD_WORDS = ["map", "地图", "logo", "icon", "flag", "seal", "diagram", "plan", "徽",
             "chart", "graph", "coat of arms", "banner", "ticket", "stamp", "banknote", "印章", "路牌", "牌坊门"]

def http_json(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if "429" in str(e):
                time.sleep(3 + i * 3)
                continue
            if i == tries - 1:
                raise
            time.sleep(1.5)
    return None

def search_commons(query):
    q = urllib.parse.quote(query)
    url = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
           f"&generator=search&gsrsearch={q}&gsrnamespace=6&gsrlimit=12"
           "&prop=imageinfo&iiprop=url%7Csize%7Cmime&iiurlwidth=640")
    data = http_json(url)
    if not data or "query" not in data:
        return []
    pages = data["query"].get("pages", {})
    results = []
    for p in pages.values():
        ii = (p.get("imageinfo") or [{}])[0]
        title = p.get("title", "")
        mime = ii.get("mime", "")
        w, h = ii.get("width", 0), ii.get("height", 0)
        if mime not in ("image/jpeg", "image/png"):
            continue
        if w < 600 or h < 400:
            continue
        if h > w * 4 or w > h * 5:
            continue
        low = title.lower()
        if any(bad in low for bad in BAD_WORDS):
            continue
        thumb = ii.get("thumburl") or ii.get("url")
        if not thumb:
            continue
        score = w * h
        # 相关性优先：标题包含搜索词的大幅加权，避免"最大面积"误命中
        if query and query in title:
            score += 10 ** 12
        else:
            # 部分匹配：查询词中连续 2 字以上出现在标题
            hit = 0
            for i in range(len(query) - 1):
                if query[i:i+3] in title:
                    hit += 1
            score += hit * 10 ** 9
        results.append({"title": title, "thumb": thumb, "score": score})
    results.sort(key=lambda r: -r["score"])
    return results[:3]

def compress(src, dst):
    # sips: 转 jpeg、限制 640px、压质量
    subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "62",
                    "--resampleWidth", "640", src, "--out", dst],
                   capture_output=True, check=True)

def download(url, dst):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        data = r.read()
    tmp = dst + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    return tmp

def fetch():
    with open(os.path.join(ROOT, "Discover.json"), encoding="utf-8") as f:
        data = json.load(f)
    overrides = {}
    if os.path.exists(OVERRIDES):
        with open(OVERRIDES, encoding="utf-8") as f:
            overrides = json.load(f)

    os.makedirs(IMAGES_DIR, exist_ok=True)
    failed = open(FAILED_LOG, "a", encoding="utf-8")
    done = skipped = 0
    for a in data["attractions"]:
        out = os.path.join(IMAGES_DIR, f"{a['id']}.jpg")
        if os.path.exists(out) and os.path.getsize(out) > 5000:
            skipped += 1
            continue
        term = overrides.get(a["id"], a["name"])
        results = []
        if isinstance(term, str) and term.startswith("http"):
            results = [{"title": "override", "thumb": term, "score": 0}]
        else:
            results = search_commons(f"{term}")
            if not results:
                time.sleep(0.5)
                results = search_commons(f"{a['name']} {a['city']}")
        ok = False
        for r in results:
            try:
                tmp = download(r["thumb"], out)
                compress(tmp, out)
                os.remove(tmp)
                if os.path.getsize(out) < 5000:
                    os.remove(out); continue
                ok = True
                break
            except Exception as e:
                print(f"  dl fail {a['id']}: {e}")
                continue
        if ok:
            done += 1
        else:
            failed.write(json.dumps({"id": a["id"], "name": a["name"], "city": a["city"]},
                                    ensure_ascii=False) + "\n")
            failed.flush()
        time.sleep(0.4)
    failed.close()
    print(f"fetch done: new={done} skipped={skipped} failed_log={FAILED_LOG}")

def backfill():
    with open(os.path.join(ROOT, "Discover.json"), encoding="utf-8") as f:
        data = json.load(f)
    with_image = no_image = 0
    for a in data["attractions"]:
        out = os.path.join(IMAGES_DIR, f"{a['id']}.jpg")
        if os.path.exists(out) and os.path.getsize(out) > 5000:
            url = CDN.format(a["id"])
            a["imageURL"] = url
            a["thumbnailURL"] = url
            with_image += 1
        else:
            a["imageURL"] = ""
            a["thumbnailURL"] = ""
            no_image += 1
    with open(os.path.join(ROOT, "Discover.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"backfill: with_image={with_image} without={no_image}")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "build":
        build()
    elif cmd == "fetch":
        fetch()
    elif cmd == "backfill":
        backfill()
