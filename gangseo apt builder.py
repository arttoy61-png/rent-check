# -*- coding: utf-8 -*-
"""강서구 아파트 실거래 탐색기 데이터 빌더
molit_kangseo.csv → gangseo_apt_summary.json(첫 화면) + gangseo_apt_detail.json(단지 클릭 시)
- 아파트만, 최근 6개월
- 노출 기준: 단지 6개월 총거래 3건 이상(few=False), 미만은 검색 전용(few=True)
- 표본 가드: 중위·전세가율은 3건 이상일 때만
- 화곡 12단지는 hwagok_apt_data.json에서 세대수·좌표 승계
"""
import csv, json, hashlib, re, statistics as st
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[2]
CSV = ROOT / "data" / "molit_kangseo.csv"
HWAGOK = ROOT / "hwagok_apt_data.json"
OUT_SUM = ROOT / "gangseo_apt_summary.json"
OUT_DET = ROOT / "gangseo_apt_detail.json"

# 임대·특수 단지 제외 (표시 자체 안 함)
EXCLUDE = {"우장산역해링턴타워"}  # 청년주택(임대형) — 일반 시세 아님

# 표기 통일 (CSV 원표기 → 블로그 표준 표기). 병합·좌표 승계 전에 적용.
NAME_FIX = {
    "우장산아이파크,이편한세상": "우장산아이파크이편한세상",
    "우장산에스케이뷰": "우장산SK뷰",
}

def norm(name):
    """표기 흔들림 병합용: 쉼표·공백·중점 제거"""
    return re.sub(r"[,\s·]+", "", name)

def cid(dong, name):
    return hashlib.md5(f"{dong}|{norm(name)}".encode()).hexdigest()[:8]

def med(a):
    return round(st.median(a), 2) if a else None

def main():
    rows = []
    enc = "utf-8-sig"
    with open(CSV, encoding=enc) as f:
        for r in csv.DictReader(f):
            if "아파트" not in (r["deal_type"] or ""): continue
            nm = (r["building_name"] or "").strip()
            nm = NAME_FIX.get(nm, nm)
            r["building_name"] = nm
            dong = (r["umd_name"] or "").strip()
            if not nm or not dong or nm in EXCLUDE: continue
            try: m2 = float(r["area_m2"])
            except: continue
            rows.append(r)

    # 단지·면적대별 묶기
    C = defaultdict(lambda: {"yr": None, "names": defaultdict(int), "areas": defaultdict(lambda: {"sale": [], "je": [], "wo": []})})
    for r in rows:
        nm, dong = r["building_name"].strip(), r["umd_name"].strip()
        k = (dong, norm(nm))
        C[k]["names"][nm] += 1
        m2 = float(r["area_m2"]); band = int(round(m2))
        d = f"{r['deal_ym'][:4]}.{r['deal_ym'][4:6]}.{int(r['deal_day']):02d}"
        fl = int(r["floor"]) if r["floor"] else None
        if r["build_year"]: C[k]["yr"] = int(float(r["build_year"]))
        a = C[k]["areas"][band]
        if "매매" in r["deal_type"]:
            if r["deal_amount"]:
                a["sale"].append({"date": d, "m2": m2, "fl": fl, "amt": round(float(r["deal_amount"])/10000, 2)})
        else:
            dep = round(float(r["deposit"])/10000, 2) if r["deposit"] else 0
            rent = float(r["monthly_rent"]) if r["monthly_rent"] else 0
            if rent > 0:
                a["wo"].append({"date": d, "m2": m2, "fl": fl, "dep": dep, "rent": int(rent)})
            else:
                a["je"].append({"date": d, "m2": m2, "fl": fl, "dep": dep})

    # 화곡 12 세대수·좌표 승계
    enrich = {}
    if HWAGOK.exists():
        hw = json.loads(HWAGOK.read_text(encoding="utf-8"))
        for c in hw.get("complexes", []):
            enrich[("화곡동", norm(c["name"]))] = {"un": c.get("units"), "lat": c.get("lat"), "lng": c.get("lng")}

    summary_dong = defaultdict(lambda: {"n": 0, "sale6": 0, "amts": []})
    complexes = []
    detail = {}
    for (dong, nk), v in C.items():
        nm = max(v["names"], key=v["names"].get)
        _id = hashlib.md5(f"{dong}|{nk}".encode()).hexdigest()[:8]
        areas_out = []
        tot = 0
        for band in sorted(v["areas"], key=lambda b: (-len(v["areas"][b]["sale"]), -(len(v["areas"][b]["je"])+len(v["areas"][b]["wo"])))):
            a = v["areas"][band]
            for arr in (a["sale"], a["je"], a["wo"]):
                arr.sort(key=lambda x: x["date"], reverse=True)
            sa = [x["amt"] for x in a["sale"]]
            areas_out.append({
                "m2": band, "py": round(band/3.3058, 1),
                "mid": med(sa) if len(sa) >= 3 else None,
                "ppy": round(med(sa)/(band/3.3058), 2) if len(sa) >= 3 else None,
                "nS": len(a["sale"]), "nJ": len(a["je"]), "nW": len(a["wo"]),
                "sale": a["sale"], "jeonse": a["je"], "wolse": a["wo"],
            })
            tot += len(a["sale"])+len(a["je"])+len(a["wo"])
        rep = areas_out[0]
        last = rep["sale"][0] if rep["sale"] else None
        je_dep = [x["dep"] for x in rep["jeonse"] if x["dep"]]
        je_mid = med(je_dep) if len(je_dep) >= 3 else None
        ratio = round(je_mid/rep["mid"]*100) if (je_mid and rep["mid"] and rep["nS"] >= 3) else None
        e = enrich.get((dong, nk), {})
        item = {
            "id": _id, "nm": nm, "dong": dong, "yr": v["yr"],
            "m2": rep["m2"], "py": rep["py"],
            "last": ({"date": last["date"], "amt": last["amt"]} if last else None),
            "mid": rep["mid"], "je": je_mid, "ratio": ratio,
            "nS": rep["nS"], "tot": tot, "few": tot < 3,
        }
        if e.get("un"): item["un"] = e["un"]
        if e.get("lat"): item["lat"], item["lng"] = e["lat"], e["lng"]
        complexes.append(item)
        detail[_id] = {"nm": nm, "dong": dong, "yr": v["yr"], "areas": areas_out}
        sd = summary_dong[dong]
        if tot >= 3: sd["n"] += 1
        sd["sale6"] += sum(len(v["areas"][b]["sale"]) for b in v["areas"])
        sd["amts"] += [x["amt"] for b in v["areas"] for x in v["areas"][b]["sale"]]

    complexes.sort(key=lambda c: -c["tot"])
    dongs = []
    for dong, sd in summary_dong.items():
        dongs.append({"dong": dong, "n": sd["n"], "sale6": sd["sale6"],
                      "mid": med(sd["amts"]) if len(sd["amts"]) >= 3 else None})
    dongs.sort(key=lambda d: -d["sale6"])

    ym_all = sorted({r["deal_ym"] for r in rows})
    meta = {"updated": max(ym_all), "range": [min(ym_all), max(ym_all)], "n_complex": len(complexes)}
    OUT_SUM.write_text(json.dumps({"meta": meta, "dongs": dongs, "complexes": complexes},
                                  ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    OUT_DET.write_text(json.dumps(detail, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"단지 {len(complexes)} (노출 {sum(1 for c in complexes if not c['few'])} / 검색전용 {sum(1 for c in complexes if c['few'])})")
    print(f"summary {OUT_SUM.stat().st_size/1024:.0f}KB / detail {OUT_DET.stat().st_size/1024:.0f}KB")

if __name__ == "__main__":
    main()
