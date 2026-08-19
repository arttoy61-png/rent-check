# -*- coding: utf-8 -*-
"""강서구 아파트 실거래 탐색기 데이터 빌더
molit_kangseo.csv → gangseo_apt_summary.json + gangseo_apt_detail.json
- 아파트만
- 최근 거래/중위/전세가율 계산
- 카카오 지오코딩 캐시를 재사용해 강서구 전체 단지 좌표 부여
"""
import csv, json, hashlib, re, sys, statistics as st
from pathlib import Path
from collections import defaultdict

_here = Path(__file__).resolve()
_p = _here.parents
_default_root = _p[2] if len(_p) > 2 else _p[len(_p) - 1]
ROOT = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else _default_root
CSV = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else _default_root / "data" / "molit_kangseo.csv"
HWAGOK = ROOT / "hwagok_apt_data.json"
GEO_CACHE = ROOT / "automation" / "data" / "geocode_cache.json"
OUT_SUM = ROOT / "gangseo_apt_summary.json"
OUT_DET = ROOT / "gangseo_apt_detail.json"

EXCLUDE = {"우장산역해링턴타워"}

NAME_FIX = {
    "우장산아이파크,이편한세상": "우장산아이파크이편한세상",
    "우장산에스케이뷰": "우장산SK뷰",
}


def norm(name):
    return re.sub(r"[,\s·]+", "", name)


def cid(dong, name):
    return hashlib.md5(f"{dong}|{norm(name)}".encode()).hexdigest()[:8]


def display_area_m2(m2):
    if 59.0 <= m2 < 60.0:
        return 59
    if 84.0 <= m2 < 85.0:
        return 84
    return int(round(m2))


def drop_lease(deps):
    if len(deps) < 5:
        return deps
    from collections import Counter
    c = Counter(deps)
    bad = {v for v, n in c.items() if n >= 10 and n / len(deps) >= 0.4}
    kept = [d for d in deps if d not in bad]
    return kept if len(kept) >= 3 else deps


def med(a):
    return round(st.median(a), 2) if a else None


def load_geo():
    if not GEO_CACHE.exists():
        print(f"지오코딩 캐시 없음: {GEO_CACHE}")
        return {}
    try:
        raw = json.loads(GEO_CACHE.read_text(encoding="utf-8"))
        return {k: v for k, v in raw.items() if isinstance(v, list) and len(v) == 2}
    except Exception as e:
        print(f"지오코딩 캐시 읽기 실패: {e}")
        return {}


def main():
    rows = []
    with open(CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if str(r.get("cdeal_type") or "").strip() == "O":
                continue
            if "아파트" not in (r.get("deal_type") or ""):
                continue
            nm = (r.get("building_name") or "").strip()
            nm = NAME_FIX.get(nm, nm)
            r["building_name"] = nm
            dong = (r.get("umd_name") or "").strip()
            if not nm or not dong or nm in EXCLUDE:
                continue
            try:
                float(r["area_m2"])
            except Exception:
                continue
            rows.append(r)

    C = defaultdict(lambda: {
        "yr": None,
        "names": defaultdict(int),
        "jibuns": defaultdict(int),
        "areas": defaultdict(lambda: {"sale": [], "je": [], "wo": []}),
    })

    for r in rows:
        nm, dong = r["building_name"].strip(), r["umd_name"].strip()
        k = (dong, norm(nm))
        C[k]["names"][nm] += 1
        jibun = (r.get("jibun") or "").strip()
        if jibun:
            C[k]["jibuns"][jibun] += 1

        m2 = float(r["area_m2"])
        band = display_area_m2(m2)
        d = f"{r['deal_ym'][:4]}.{r['deal_ym'][4:6]}.{int(r['deal_day']):02d}"
        try:
            fl = int(float(r["floor"])) if r.get("floor") else None
        except Exception:
            fl = None
        if r.get("build_year"):
            try:
                C[k]["yr"] = int(float(r["build_year"]))
            except Exception:
                pass

        a = C[k]["areas"][band]
        if "매매" in r["deal_type"]:
            if r.get("deal_amount"):
                try:
                    a["sale"].append({
                        "date": d, "m2": m2, "fl": fl,
                        "amt": round(float(str(r["deal_amount"]).replace(",", "")) / 10000, 2)
                    })
                except Exception:
                    pass
        else:
            try:
                dep = round(float(str(r.get("deposit") or "0").replace(",", "")) / 10000, 2)
            except Exception:
                dep = 0
            try:
                rent = float(str(r.get("monthly_rent") or "0").replace(",", ""))
            except Exception:
                rent = 0
            if rent > 0:
                a["wo"].append({"date": d, "m2": m2, "fl": fl, "dep": dep, "rent": int(rent)})
            elif dep > 0:
                a["je"].append({"date": d, "m2": m2, "fl": fl, "dep": dep})

    enrich = {}
    if HWAGOK.exists():
        try:
            hw = json.loads(HWAGOK.read_text(encoding="utf-8"))
            for c in hw.get("complexes", []):
                enrich[("화곡동", norm(c["name"]))] = {
                    "un": c.get("units"),
                    "lat": c.get("lat"),
                    "lng": c.get("lng"),
                }
        except Exception:
            pass

    geo = load_geo()

    summary_dong = defaultdict(lambda: {"n": 0, "sale6": 0, "amts": []})
    complexes = []
    detail = {}

    for (dong, nk), v in C.items():
        nm = max(v["names"], key=v["names"].get)
        _id = hashlib.md5(f"{dong}|{nk}".encode()).hexdigest()[:8]
        areas_out = []
        tot = 0
        tS = tJ = tW = 0

        for band in sorted(
            v["areas"],
            key=lambda b: (-len(v["areas"][b]["sale"]), -(len(v["areas"][b]["je"]) + len(v["areas"][b]["wo"])))
        ):
            a = v["areas"][band]
            for arr in (a["sale"], a["je"], a["wo"]):
                arr.sort(key=lambda x: x["date"], reverse=True)

            sa = [x["amt"] for x in a["sale"]]
            sale_areas = [x["m2"] for x in a["sale"] if x.get("m2")]
            all_areas = [x["m2"] for arr in (a["sale"], a["je"], a["wo"]) for x in arr if x.get("m2")]
            sale_mid = med(sa) if len(sa) >= 3 else None
            area_mid = med(sale_areas) if len(sale_areas) >= 3 else None
            area_ref = med(all_areas) if all_areas else float(band)

            areas_out.append({
                "m2": band,
                "py": round(area_ref / 3.3058, 1),
                "mid": sale_mid,
                "ppy": round(sale_mid / (area_mid / 3.3058), 2) if (sale_mid is not None and area_mid) else None,
                "nS": len(a["sale"]), "nJ": len(a["je"]), "nW": len(a["wo"]),
                "sale": a["sale"], "jeonse": a["je"], "wolse": a["wo"],
            })
            tot += len(a["sale"]) + len(a["je"]) + len(a["wo"])
            tS += len(a["sale"])
            tJ += len(a["je"])
            tW += len(a["wo"])

        if not areas_out:
            continue

        rep = areas_out[0]
        last = rep["sale"][0] if rep["sale"] else None
        je_dep = drop_lease([x["dep"] for x in rep["jeonse"] if x["dep"]])
        je_mid = med(je_dep) if len(je_dep) >= 3 else None
        ratio = round(je_mid / rep["mid"] * 100) if (je_mid and rep["mid"] and rep["nS"] >= 3) else None
        e = enrich.get((dong, nk), {})

        item = {
            "id": _id, "nm": nm, "dong": dong, "yr": v["yr"],
            "m2": rep["m2"], "py": rep["py"],
            "last": ({"date": last["date"], "amt": last["amt"]} if last else None),
            "mid": rep["mid"], "je": je_mid, "ratio": ratio,
            "nS": rep["nS"], "nJ": rep["nJ"], "nW": rep["nW"],
            "tS": tS, "tJ": tJ, "tW": tW, "tot": tot, "few": tot < 3,
        }

        if e.get("un"):
            item["un"] = e["un"]

        jibun = max(v["jibuns"], key=v["jibuns"].get) if v["jibuns"] else ""
        if jibun:
            item["j"] = jibun
            xy = geo.get(f"{dong} {jibun}")
            if xy:
                item["lat"], item["lng"] = xy[0], xy[1]
        if "lat" not in item and e.get("lat") and e.get("lng"):
            item["lat"], item["lng"] = e["lat"], e["lng"]

        rc, ppy, mxn = None, None, -1
        for ao in areas_out:
            if ao.get("ppy") is not None and ao["nS"] > mxn:
                mxn, ppy = ao["nS"], ao["ppy"]
            for key, tag in (("sale", "sale"), ("jeonse", "je"), ("wolse", "wo")):
                for rr in ao.get(key, []):
                    amt = rr.get("amt") if tag == "sale" else rr.get("dep")
                    if amt is None:
                        continue
                    cand = {
                        "d": rr["date"], "t": tag, "m2": display_area_m2(rr["m2"]),
                        "fl": rr.get("fl"), "a": amt
                    }
                    if tag == "wo":
                        cand["r"] = rr.get("rent")
                    if rc is None or cand["d"] > rc["d"]:
                        rc = cand

        if rc:
            item["rc"] = rc
        if ppy is not None:
            item["ppy"] = round(ppy, 3)

        complexes.append(item)
        detail[_id] = {"nm": nm, "dong": dong, "yr": v["yr"], "areas": areas_out}

        sd = summary_dong[dong]
        if tot >= 3:
            sd["n"] += 1
        sd["sale6"] += sum(len(v["areas"][b]["sale"]) for b in v["areas"])
        sd["amts"] += [x["amt"] for b in v["areas"] for x in v["areas"][b]["sale"]]

    complexes.sort(key=lambda c: -c["tot"])
    dongs = []
    for dong, sd in summary_dong.items():
        dongs.append({
            "dong": dong, "n": sd["n"], "sale6": sd["sale6"],
            "mid": med(sd["amts"]) if len(sd["amts"]) >= 3 else None
        })
    dongs.sort(key=lambda d: -d["sale6"])

    ym_all = sorted({r["deal_ym"] for r in rows})
    meta = {"updated": max(ym_all), "range": [min(ym_all), max(ym_all)], "n_complex": len(complexes)}
    from datetime import datetime, timezone
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    OUT_SUM.write_text(
        json.dumps({"meta": meta, "generated_at": generated_at, "dongs": dongs, "complexes": complexes},
                   ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    OUT_DET.write_text(json.dumps(detail, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    geo_count = sum(1 for c in complexes if c.get("lat") and c.get("lng"))
    print(f"단지 {len(complexes)} (노출 {sum(1 for c in complexes if not c['few'])} / 검색전용 {sum(1 for c in complexes if c['few'])})")
    print(f"좌표 {geo_count}/{len(complexes)} 단지")
    print(f"summary {OUT_SUM.stat().st_size/1024:.0f}KB / detail {OUT_DET.stat().st_size/1024:.0f}KB")


if __name__ == "__main__":
    main()
