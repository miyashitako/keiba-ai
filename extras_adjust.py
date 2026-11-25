"""
extras_adjust.py（v0.1新規作成 / 2026-11）

追加項目（年齢・騎手・調教師）の補正表（extras_adjust.json）を読み込み、
出走馬ごとの補正pt（スコアに足す値。−＝有利、＋＝不利）を計算する。

■ 根拠（要約）
  市場の暗黙の評価（オッズ）のうち、現行のスコアで説明できなかった部分に、年齢・騎手・調教師が効く。
  その方向にスコアを調整すると、勝敗の予測が改善する。JRAの2年分（2023-04〜2025-04）で、
  別の年に当てはめる検証を両方向で行い、1位予想の勝率が +1.8〜+2.9pt（掛け率×0.5）、
  勝敗の対数尤度・3着内率・勝ち馬の平均順位が改善することを確認した
  （fit_extras_adjust.py、market_residual_scan.py）。目的は市場の真似ではなく、勝つ馬を1位に予想すること。

■ 補正表の作り方（JRA）
  python fit_extras_adjust.py --system JRA --calc-version "2.26-scan_based_polish" \\
      --train-min 2023-04-01 --train-max 2025-04-25 --out extras_adjust.json
  できたJSONを、アプリ（app.py）と同じ場所に置く（環境変数 KEIBA_EXTRAS_ADJUST でパスを変更可）。

■ 使い方（アプリ側）
  from extras_adjust import load_table, attach_shutuba_extras, extras_points
  table = load_table()                              # 無ければ None（補正なし）
  attach_shutuba_extras(horses, race_url, "JRA")    # 出馬表から年齢・調教師を各Horseに付ける
  parts = extras_points(table, "JRA", h.age, h.jockey, h.trainer)
  pts = parts["total"] * scale                      # スコアに足す（−＝有利）

■ 名前の対応
  補正表の騎手・調教師の名前は、結果ページの表記。出馬表の表記と違う場合に備え、
  騎手は前方一致で対応し、調教師は地域名（美浦・栗東など）を取り除いて照合する。
  対応できなかった馬は補正0になる（extras_points の戻り値の *_matched で分かる）。

■ 注意
  ・補正表はJRA用。NAR用は、NARの追加項目つきデータが十分たまってから別に作る。
  ・出馬表の取得に失敗しても、アプリの他の機能は止めない（その馬の年齢・調教師が空になるだけ）。
"""

import json
import os
import re
import unicodedata

ADJUST_FILE = os.environ.get(
    "KEIBA_EXTRAS_ADJUST",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "extras_adjust.json"),
)

_APPRENTICE = re.compile(r"^[△▲☆◇★▽]+")
_REGION = re.compile(r"^(?:美浦|栗東|地方|海外|外国|[\[［【(（][^\]］】)）]{1,4}[\]］】)）])\s*")
_TABLE_CACHE: dict = {}


# ─────────────────────────────────────────────
# 補正表の読み込み
# ─────────────────────────────────────────────
def load_table(path: "str | None" = None):
    """補正表（dict）を返す。ファイルが無い・壊れている場合はNone。"""
    path = path or ADJUST_FILE
    if path in _TABLE_CACHE:
        return _TABLE_CACHE[path]
    table = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            table = json.load(f)
        for key in ("年齢", "騎手", "調教師"):
            table.setdefault(key, {})
    except Exception:
        table = None
    _TABLE_CACHE[path] = table
    return table


# ─────────────────────────────────────────────
# 名前の正規化・照合
# ─────────────────────────────────────────────
def _nfkc(s) -> str:
    return unicodedata.normalize("NFKC", str(s or "")).strip()


def normalize_trainer(raw, system: str = "JRA") -> str:
    """出馬表の厩舎表記を、補正表（結果ページ表記）に合わせる。
    JRA：『美浦 西田』→『西田』。NAR：『川崎 高橋宏征』→『川崎高橋宏征』（空白を詰める）。"""
    s = _nfkc(raw)
    if system.upper() == "NAR":
        return re.sub(r"\s+", "", s)
    s = _REGION.sub("", s)
    return re.sub(r"\s+", "", s)


def match_jockey(name, keys) -> "str | None":
    """出馬表の騎手名を、補正表のキーに対応づける。完全一致、なければ前方一致（長い方を優先）。"""
    n = _APPRENTICE.sub("", _nfkc(name))
    if not n:
        return None
    if n in keys:
        return n
    best = None
    for k in keys:
        if len(k) >= 2 and len(n) >= 2 and (n.startswith(k) or k.startswith(n)):
            if best is None or len(k) > len(best):
                best = k
    return best


def extras_points(table, system: str, age, jockey, trainer) -> dict:
    """1頭分の補正pt（スコアに足す値。−＝有利）を返す。表に無い・不明は0。"""
    out = {"age": 0.0, "jockey": 0.0, "trainer": 0.0, "total": 0.0,
           "jockey_matched": False, "trainer_matched": False}
    if not table:
        return out
    try:
        a = int(age)
        a = min(a, 7)
        out["age"] = float(table["年齢"].get(str(a), 0.0))
    except (TypeError, ValueError):
        pass
    jk = match_jockey(jockey, table["騎手"].keys()) if jockey else None
    if jk is not None:
        out["jockey"] = float(table["騎手"][jk])
        out["jockey_matched"] = True
    tr = normalize_trainer(trainer, system) if trainer else ""
    if tr and tr in table["調教師"]:
        out["trainer"] = float(table["調教師"][tr])
        out["trainer_matched"] = True
    out["total"] = out["age"] + out["jockey"] + out["trainer"]
    return out


# ─────────────────────────────────────────────
# 出馬表（shutuba.html）から年齢・調教師を取る
# ─────────────────────────────────────────────
_DEFAULT_COLS = {"age": 4, "jockey": 6, "trainer": 7}      # 実際の出馬表で確認済みの並び
_HEADER_KEYS = {"age": "性齢", "jockey": "騎手", "trainer": "厩舎"}


def _col_map(table) -> dict:
    cmap = {}
    try:
        first = table.find("tr")
        for idx, cell in enumerate(first.find_all(["th", "td"]) if first else []):
            text = re.sub(r"\s+", "", _nfkc(cell.get_text()))
            for key, label in _HEADER_KEYS.items():
                if key not in cmap and label in text:
                    cmap[key] = idx
    except Exception:
        cmap = {}
    for k, v in _DEFAULT_COLS.items():
        cmap.setdefault(k, v)
    return cmap


def parse_shutuba_extras(html: str) -> dict:
    """出馬表のHTMLから {horse_id: {age, trainer, jockey}} を作る（テストしやすいよう分離）。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    table = (soup.find("table", class_="Shutuba_Table") or soup.find("table", class_="ShutubaTable")
             or soup.find("table"))
    if table is None:
        return {}
    cmap = _col_map(table)
    result = {}
    for row in table.find_all("tr")[1:]:
        cols = row.find_all("td")
        if len(cols) <= max(cmap.values()):
            continue
        link = row.find("a", href=re.compile(r"/horse/(\d+)"))
        if not link:
            continue
        hid = re.search(r"/horse/(\d+)", link.get("href", "")).group(1)
        m = re.search(r"(\d+)", _nfkc(cols[cmap["age"]].get_text()))
        result[hid] = {
            "age": int(m.group(1)) if m else None,
            "jockey": cols[cmap["jockey"]].get_text(strip=True),
            "trainer": cols[cmap["trainer"]].get_text(" ", strip=True),
        }
    return result


def fetch_shutuba_extras(race_id: str, system: str = "JRA", timeout: float = 15.0) -> dict:
    import requests
    try:
        from scraper import HEADERS
    except Exception:  # pragma: no cover
        HEADERS = {"User-Agent": "Mozilla/5.0"}
    base = "https://nar.netkeiba.com" if system.upper() == "NAR" else "https://race.netkeiba.com"
    res = requests.get(f"{base}/race/shutuba.html?race_id={race_id}", headers=HEADERS, timeout=timeout)
    if res.status_code != 200:
        raise ConnectionError(f"HTTPエラー: {res.status_code}")
    out = parse_shutuba_extras(res.content.decode("utf-8", errors="replace"))
    if out:
        return out
    # 確定済みのレースは出馬表ページが空・結果ページ相当になることがある（答え合わせモード用）。
    # 結果ページにも 性齢・騎手・厩舎 の列があり、見出しから列を特定するので同じ解析が使える。
    res = requests.get(f"{base}/race/result.html?race_id={race_id}", headers=HEADERS, timeout=timeout)
    if res.status_code != 200:
        raise ConnectionError(f"HTTPエラー: {res.status_code}")
    return parse_shutuba_extras(res.content.decode("utf-8", errors="replace"))


def attach_shutuba_extras(horses, race_url_or_id: str, system: str = "JRA") -> int:
    """各Horseに h.age / h.trainer を付ける（取得失敗は握りつぶし、空のままにする）。
    付けられた頭数を返す。"""
    m = re.search(r"(\d{12})", str(race_url_or_id))
    for h in horses:
        h.age = None
        h.trainer = ""
    if not m:
        return 0
    try:
        ex = fetch_shutuba_extras(m.group(1), system)
    except Exception as e:  # ネットワークや構造変化でも、予想そのものは止めない
        print(f"[WARN] 出馬表の追加項目（年齢・調教師）の取得に失敗: {e}")
        return 0
    n = 0
    for h in horses:
        info = ex.get(str(getattr(h, "horse_id", "")))
        if info:
            h.age = info["age"]
            h.trainer = info["trainer"]
            n += 1
    return n
