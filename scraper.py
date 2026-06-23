"""
netkeiba スクレイピングモジュール
出走表：race.netkeiba.com/race/shutuba_past.html（出走前データ）
過去走：db.netkeiba.com/horse/result/{horse_id}/
"""

import requests
from bs4 import BeautifulSoup
import re
import time
from dataclasses import dataclass, field
from typing import Optional

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://race.netkeiba.com/",
}


# ──────────────────────────────────────────────
# データクラス
# ──────────────────────────────────────────────

@dataclass
class RaceInfo:
    """当日レース情報"""
    race_id: str = ""
    race_name: str = ""
    venue: str = ""
    surface: str = ""
    distance: int = 0
    direction: str = ""
    track_cond: str = ""
    weather: str = ""
    race_class: str = ""
    race_date: str = ""
    is_age_limited: bool = False    # 馬齢限定戦フラグ（v1.0追加）
    is_classic_distance: bool = False  # 秋華賞・菊花賞等の長距離フラグ（v1.0追加）
    is_female_only: bool = False    # 牝馬限定戦フラグ（v1.1追加）


@dataclass
class PastRace:
    """過去走1レース分"""
    date: str = ""
    venue: str = ""
    race_class: str = ""
    distance: int = 0
    surface: str = ""
    condition: str = ""
    finish: int = 0
    time_sec: float = 0.0
    margin: float = 0.0      # 直前馬との着差（秒）
    winner_time_sec: float = 0.0  # 1着馬タイム（v0.8追加）
    last3f: float = 0.0
    weight_carried: float = 55.0
    jockey: str = ""
    is_local: bool = False   # 地方競馬場の走（v0.9追加）
    field_size: int = 0       # 出走頭数（v1.1追加）
    corner_pos: str = ""      # コーナー通過順位（v1.1追加）例："10-9", "3-3-4-4"
    race_day: int = 0         # 開催日次（v1.1追加）例：3東京2→2、開幕週判定用
    is_female_only: bool = False  # 牝馬限定戦フラグ（v1.1追加）


@dataclass
class Horse:
    """出走馬1頭分"""
    horse_id: str = ""
    name: str = ""
    frame: int = 0
    number: int = 0
    jockey: str = ""
    weight_carried: float = 55.0
    past_races: list = field(default_factory=list)
    sex: str = ""   # 性別：牡/牝/騸（v1.1追加）


# ──────────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────────

def time_to_sec(time_str: str) -> float:
    """'1:34.5' → 94.5 / '34.5' → 34.5"""
    time_str = str(time_str).strip()
    if not time_str or time_str in ("---", ""):
        return 0.0
    try:
        if ":" in time_str:
            parts = time_str.split(":")
            return int(parts[0]) * 60 + float(parts[1])
        return float(time_str)
    except Exception:
        return 0.0


def margin_to_sec(margin_str: str) -> float:
    """
    着差文字列 → 秒（float）
    netkeibaのdb_h_race_resultsの着差欄は秒差で記録されているが、
    馬身表記（ハナ/アタマ/クビ/1/2など）が混在する。
    馬身表記は1馬身≒0.2秒として秒に変換する。
    """
    margin_str = str(margin_str).strip()
    if not margin_str or margin_str in ("---", "同", "0", ""):
        return 0.0
    # 馬身表記 → 秒換算（1馬身 ≒ 0.2秒）
    mapping = {
        "ハナ": 0.05, "アタマ": 0.1, "クビ": 0.15,
        "1/4": 0.1, "1/2": 0.2, "3/4": 0.3,
        "1": 0.2, "2": 0.4, "3": 0.6,
    }
    if margin_str in mapping:
        return mapping[margin_str]
    # 「1.1/2」のような複合表記（馬身数）
    m = re.match(r"(\d+)\.(\d+)/(\d+)", margin_str)
    if m:
        lengths = int(m.group(1)) + int(m.group(2)) / int(m.group(3))
        return round(lengths * 0.2, 3)
    # 「1/2」のような分数馬身
    m = re.match(r"^(\d+)/(\d+)$", margin_str)
    if m:
        lengths = int(m.group(1)) / int(m.group(2))
        return round(lengths * 0.2, 3)
    # 数値のみ → 秒差としてそのまま使用（例: "1.1", "0.3"）
    try:
        return float(margin_str)
    except Exception:
        return 0.0

# 後方互換エイリアス（手動入力モード用）
def margin_to_lengths(margin_str: str) -> float:
    """後方互換：margin_to_sec()のエイリアス（手動入力から呼ばれる場合用）"""
    return margin_to_sec(margin_str)


def extract_race_id(url: str) -> Optional[str]:
    m = re.search(r"race_id=(\d+)", url)
    return m.group(1) if m else None


def extract_horse_id(href: str) -> Optional[str]:
    m = re.search(r"/horse/(\d+)", href)
    return m.group(1) if m else None


# ──────────────────────────────────────────────
# レース情報取得
# ──────────────────────────────────────────────

def fetch_race_info(race_url: str) -> RaceInfo:
    """
    shutuba_past.html からレース情報を取得する

    取得元:
    - RaceData01: '14:30発走 /芝1600m(左 A)\n/ 天候:曇/ 馬場:良'
    - RaceData02: '4回東京4日目サラ系３歳以上２勝クラス...'
    - title: '鷹巣山特別(2勝クラス) ... 2025年10月12日 東京9R'
    - h1.RaceName: 'レース名'
    """
    race_id = extract_race_id(race_url)
    info = RaceInfo(race_id=race_id or "")

    shutuba_url = f"https://race.netkeiba.com/race/shutuba_past.html?race_id={race_id}"
    try:
        res = requests.get(shutuba_url, headers=HEADERS, timeout=15)
        res.encoding = "EUC-JP"
    except Exception:
        return info

    if res.status_code != 200:
        return info

    # race.netkeiba.comはUTF-8（v1.1修正）
    html_text = res.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_text, "html.parser")

    # ── レース名
    race_name_el = soup.find("h1", class_="RaceName")
    if race_name_el:
        info.race_name = race_name_el.get_text(strip=True)

    # ── RaceData01: 距離・芝ダ・馬場状態・天候
    data01 = soup.find("div", class_="RaceData01")
    if data01:
        text01 = data01.get_text(strip=True)

        # 芝ダ・距離（例: 芝1600m / ダ1700m）
        m = re.search(r"([芝ダ])(\d+)m", text01)
        if m:
            info.surface  = m.group(1)
            info.distance = int(m.group(2))

        # 回り（左・右）
        m = re.search(r"\(([左右])", text01)
        if m:
            info.direction = m.group(1)

        # 天候
        m = re.search(r"天候:(\S+?)(?:/|$)", text01)
        if m:
            info.weather = m.group(1).strip()

        # 馬場状態
        m = re.search(r"馬場:(\S+?)(?:/|$|\s)", text01)
        if m:
            info.track_cond = m.group(1).strip()

    # ── RaceData02: 競馬場・クラス
    data02 = soup.find("div", class_="RaceData02")
    if data02:
        text02 = data02.get_text(strip=True)

        # クラス（2勝クラス・G1 等）
        # RaceData02は全角数字で「２勝クラス」と入ることがあるため
        # 全角→半角に正規化してからマッチする
        import unicodedata
        text02_norm = unicodedata.normalize("NFKC", text02)
        for cls in ["G1", "G2", "G3", "Jpn1", "Jpn2", "Jpn3",
                    "OP", "オープン", "3勝クラス",
                    "2勝クラス", "1勝クラス", "未勝利", "新馬"]:
            if cls in text02_norm:
                info.race_class = cls
                break

    # ── title タグから競馬場・日付を取得
    title_el = soup.find("title")
    if title_el:
        title_text = title_el.get_text(strip=True)

        # 日付（例: 2025年10月12日）
        m = re.search(r"(\d{4}年\d{1,2}月\d{1,2}日)", title_text)
        if m:
            info.race_date = m.group(1)

        # 競馬場（例: 東京9R → 東京）
        venues = ["東京", "中山", "阪神", "京都", "中京", "小倉",
                  "新潟", "福島", "札幌", "函館"]
        for v in venues:
            if v in title_text:
                info.venue = v
                break

    # ── 馬齢限定戦・クラシック距離の自動判定（v1.0追加）
    AGE_LIMITED_KEYWORDS = ["2歳", "3歳", "牝馬限定", "牡馬限定"]
    CLASSIC_RACE_NAMES = {
        "オークス", "桜花賞", "皐月賞", "日本ダービー", "菊花賞", "秋華賞",
        "阪神JF", "朝日杯FS", "ホープフルS", "NHKマイルC",
        "フローラS", "スプリングS", "弥生賞", "毎日杯",
        "フィリーズレビュー", "チューリップ賞", "青葉賞", "忘れな草賞",
        "セントライト記念", "神戸新聞杯",
    }
    CLASSIC_DISTANCE_RACES = {"秋華賞", "菊花賞", "神戸新聞杯", "セントライト記念"}

    combined_text = info.race_name + info.race_class
    for kw in AGE_LIMITED_KEYWORDS:
        if kw in combined_text:
            info.is_age_limited = True
            break

    # 牝馬限定戦の判定（v1.1追加）
    FEMALE_ONLY_RACE_KEYWORDS = ["牝馬限定", "牝限定"]
    FEMALE_ONLY_RACE_NAMES = {
        "桜花賞", "オークス", "秋華賞", "阪神JF", "エリザベス女王杯",
        "ヴィクトリアマイル", "フィリーズレビュー", "チューリップ賞",
        "フローラS", "忘れな草賞", "紫苑S", "クイーンS",
        "阪神ジュベナイルフィリーズ", "府中牝馬S", "愛知杯",
        "マーメイドS", "福島牝馬S", "北九州短距離S",
    }
    if any(kw in combined_text for kw in FEMALE_ONLY_RACE_KEYWORDS):
        info.is_female_only = True
    # レース名に「牝馬」が含まれる場合も牝馬限定戦とみなす
    elif "牝馬" in info.race_name:
        info.is_female_only = True
    else:
        for name in FEMALE_ONLY_RACE_NAMES:
            if name in info.race_name:
                info.is_female_only = True
                break
    for name in CLASSIC_RACE_NAMES:
        if name in info.race_name:
            info.is_age_limited = True
            break
    for name in CLASSIC_DISTANCE_RACES:
        if name in info.race_name:
            info.is_classic_distance = True
            break

    return info


# ──────────────────────────────────────────────
# 出走表取得
# ──────────────────────────────────────────────

def fetch_shutuba(race_url: str) -> list[Horse]:
    """
    出走表を shutuba_past.html から取得する
    着順列がないため出走前データとして正しい
    """
    race_id = extract_race_id(race_url)
    if not race_id:
        raise ValueError(f"race_id をURLから取得できませんでした: {race_url}")

    shutuba_url = f"https://race.netkeiba.com/race/shutuba_past.html?race_id={race_id}"

    try:
        res = requests.get(shutuba_url, headers=HEADERS, timeout=15)
        res.encoding = "EUC-JP"
    except Exception as e:
        raise ConnectionError(f"出走表の取得に失敗しました: {e}")

    if res.status_code != 200:
        raise ConnectionError(f"HTTPエラー: {res.status_code}")

    # race.netkeiba.comはUTF-8（v1.1修正）
    html_text = res.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        raise ValueError("テーブルが見つかりませんでした。")

    table = tables[0]  # Shutuba_Table
    rows = table.find_all("tr")[1:]

    horses = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 4:
            continue

        horse = Horse()

        # 枠番（列0）
        try:
            horse.frame = int(cols[0].get_text(strip=True))
        except Exception:
            pass

        # 馬番（列1）
        try:
            horse.number = int(cols[1].get_text(strip=True))
        except Exception:
            pass

        # 馬名・horse_id（列3のリンク）
        name_cell = cols[3]
        horse_link = name_cell.find("a", href=re.compile(r"/horse/\d+"))
        if horse_link:
            horse.horse_id = extract_horse_id(horse_link["href"]) or ""
            horse.name = horse_link.get_text(strip=True)
        else:
            horse.name = name_cell.get_text(strip=True)[:10]

        if not horse.name:
            continue

        # 騎手・斤量・性別（列4）
        # 列4の形式: "牡5鹿岩田望58.0" → 先頭1文字が性別
        jockey_cell_text = cols[4].get_text(strip=True)
        # ── 診断ログ用：列4の生テキストを記録（エラー調査用）
        _col4_raw = repr(jockey_cell_text)  # 不可視文字も見えるようにrepr
        horse._col4_raw = _col4_raw         # Horseオブジェクトに付与（後でデバッグパネルに使用）
        # 性別判定：牡/牝/セ（騸馬、netkeibaはカタカナ表記）
        if jockey_cell_text:
            if jockey_cell_text[0] in ("牡", "牝"):
                horse.sex = jockey_cell_text[0]
            elif jockey_cell_text[0] in ("セ", "騸"):
                horse.sex = "セ"
            else:
                # 先頭が性別文字でない場合：HTML構造変化の可能性
                # 空白・記号・数字などが入っていたらログに残す
                horse.sex = ""
                horse._sex_parse_warning = (
                    f"列4先頭が性別文字でない: {_col4_raw[:40]}"
                )

        # 斤量（末尾の数値）
        wc_match = re.search(r"(\d{2,3}(?:\.\d)?)\s*$", jockey_cell_text)
        if wc_match:
            try:
                val = float(wc_match.group(1))
                if 48.0 <= val <= 60.0:
                    horse.weight_carried = val
            except Exception:
                pass

        # 騎手（jockeyリンク優先）
        jockey_link = cols[4].find("a", href=re.compile(r"/jockey/"))
        if jockey_link:
            horse.jockey = jockey_link.get_text(strip=True)
        else:
            jockey_match = re.search(r"([^\d]{2,6})\d{2,3}(?:\.\d)?$", jockey_cell_text)
            if jockey_match:
                horse.jockey = jockey_match.group(1).strip()

        horses.append(horse)

    if all(h.number == 0 for h in horses):
        for i, h in enumerate(horses, 1):
            h.number = i

    return horses


# ──────────────────────────────────────────────
# 各馬過去走取得
# ──────────────────────────────────────────────

def fetch_past_races(horse_id: str, limit: int = 5) -> list[PastRace]:
    """
    馬の過去走データを取得する（最大limit走）
    URL: https://db.netkeiba.com/horse/result/{horse_id}/
    テーブル: db_h_race_results

    確定列定義:
    00:日付 01:開催 04:レース名 11:着順 12:騎手 13:斤量
    14:距離 16:馬場 18:タイム 19:着差 27:上り
    """
    url = f"https://db.netkeiba.com/horse/result/{horse_id}/"

    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
    except Exception as e:
        raise ConnectionError(f"馬情報の取得に失敗しました ({horse_id}): {e}")

    if res.status_code != 200:
        return []

    html_text = res.content.decode("euc-jp", errors="replace")
    soup = BeautifulSoup(html_text, "html.parser")
    table = soup.find("table", class_="db_h_race_results")
    if table is None:
        return []

    past_races = []
    rows = table.find_all("tr")[1:]

    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 20:
            continue

        def get(idx, default=""):
            if 0 <= idx < len(cols):
                return cols[idx].get_text(strip=True)
            return default

        pr = PastRace()
        try:
            pr.date = get(0)

            # レース名（列4）＋グレードアイコン（spanタグ）を組み合わせてrace_classを構築
            # netkeibaはグレードを<span class="Icon_GradeType1">G1</span>等で付与している
            race_name_cell = cols[4] if len(cols) > 4 else None
            grade_text = ""
            if race_name_cell:
                for span in race_name_cell.find_all("span"):
                    cls_attr = span.get("class", [])
                    span_text = span.get_text(strip=True)
                    # Icon_GradeType系のスパンからグレードを取得
                    if any("GradeType" in c for c in cls_attr):
                        grade_text = span_text
                        break
                    # テキストがG1/G2/G3/Jpn系なら採用
                    if re.match(r"^(G[123]|Jpn[123]|OP|L)$", span_text):
                        grade_text = span_text
                        break
            # race_name_textはspanを除去したテキストを使う（v1.2修正）
            # get(4)はspanテキストが連結されるため「2歳未勝利G1」等になってしまう
            if race_name_cell:
                import copy as _copy
                _cell_copy = _copy.copy(race_name_cell)
                for _s in _cell_copy.find_all("span"):
                    _s.decompose()
                race_name_text = _cell_copy.get_text(strip=True)
            else:
                race_name_text = get(4)
            # グレードが取れた場合：「レース名(グレード)」形式で保存
            # → _detect_grade_keyがレース名からも正確なグレードを判定できる
            if grade_text:
                pr.race_class = f"{race_name_text}({grade_text})"
            else:
                # レース名からグレードを推定（"G2"等の文字が含まれる場合）
                import unicodedata as _ud
                name_norm = _ud.normalize("NFKC", race_name_text)
                grade_match = re.search(r"(G[123]|Jpn[123])", name_norm)
                if grade_match:
                    pr.race_class = grade_match.group(1)
                else:
                    pr.race_class = race_name_text

            finish_str = get(11)
            nums = re.findall(r"\d+", finish_str)
            pr.finish = int(nums[0]) if nums else 0

            # 出走頭数（列6）v1.1追加
            field_str = get(6)
            field_nums = re.findall(r"\d+", field_str)
            pr.field_size = int(field_nums[0]) if field_nums else 0

            # 牝馬限定戦フラグ（v1.1追加 / v1.2修正：漏れレース名追加）
            # キーワード方式（「牝」等の文字を含む）＋著名牝馬限定レース名セット
            rc_for_female = pr.race_class
            FEMALE_ONLY_KEYWORDS = ["牝", "フィリーズ", "オークス", "エリザベス女王杯",
                                    "ヴィクトリアマイル", "阪神ジュベナイルフィリーズ",
                                    "桜花賞", "秋華賞", "チューリップ賞"]
            # キーワードでは拾えない牝馬限定レース名を別途チェック
            FEMALE_ONLY_RACE_NAMES_PAST = {
                "エルフィンS", "アルテミスS", "フラワーC", "クイーンC", "フィリーズレビュー", "アネモネS",
                "スイートピーS", "フローラS", "忘れな草賞", "マーメイドS",
                "クイーンS", "紫苑S", "府中牝馬S", "ローズS", "愛知杯",
                "福島牝馬S", "北九州短距離S", "阪神JF",
            }
            pr.is_female_only = (
                any(kw in rc_for_female for kw in FEMALE_ONLY_KEYWORDS)
                or any(name in rc_for_female for name in FEMALE_ONLY_RACE_NAMES_PAST)
            )

            # コーナー通過順位（列25）v1.1追加
            pr.corner_pos = get(25) if len(cols) > 25 else ""

            # 開催日次（列3）v1.1追加 例："3東京2" → 列01, 列03="8"
            # 列01="3東京2"の末尾数字が開催日次
            kaisan_str = get(1)   # 例："3東京2"
            day_m = re.search(r"(\d+)$", kaisan_str)
            pr.race_day = int(day_m.group(1)) if day_m else 0

            pr.jockey = get(12)

            try:
                pr.weight_carried = float(get(13, "55"))
            except Exception:
                pr.weight_carried = 55.0

            # 距離・芝ダ（例: 芝1600 / ダ1700）
            course_str = get(14)
            m = re.search(r"([芝ダ])(\d+)", course_str)
            if m:
                pr.surface  = m.group(1)
                pr.distance = int(m.group(2))

            # 競馬場（開催欄から抽出）・is_local判定（v0.9追加）
            venue_str = get(1)
            JRA_VENUES   = ["東京", "中山", "阪神", "京都", "中京", "小倉",
                            "新潟", "福島", "札幌", "函館"]
            LOCAL_VENUES = ["門別", "盛岡", "水沢", "金沢", "笠松", "名古屋",
                            "園田", "姫路", "高知", "佐賀", "荒尾", "川崎",
                            "船橋", "大井", "浦和", "帯広"]
            for v in JRA_VENUES:
                if v in venue_str:
                    pr.venue = v
                    pr.is_local = False
                    break
            else:
                for v in LOCAL_VENUES:
                    if v in venue_str:
                        pr.venue = v
                        pr.is_local = True
                        break

            pr.condition = get(16)
            pr.time_sec  = time_to_sec(get(18))
            pr.margin    = margin_to_sec(get(19))

            # 1着タイムの推定（v0.8追加）
            # db_h_race_resultsの列20は「タイム差（1着との差）」が入っている場合がある
            # 取れない場合はtime_secから着差累積で逆算
            time_diff_str = get(20, "")
            time_diff = time_to_sec(time_diff_str)
            if pr.finish == 1:
                pr.winner_time_sec = pr.time_sec
            elif time_diff > 0:
                # 列20がタイム差（秒）として取れた場合
                pr.winner_time_sec = round(pr.time_sec - time_diff, 3)
            elif pr.margin > 0 and pr.time_sec > 0:
                # フォールバック：直前馬差×0.1秒を着順分累積（粗い推定）
                # 実際は累積できないが、1秒以上の差は大差負けとして検知可能
                pr.winner_time_sec = round(pr.time_sec - pr.margin * 0.1, 3)

            try:
                pr.last3f = float(get(27, "0"))
            except Exception:
                pr.last3f = 0.0

        except Exception:
            pass

        # time_sec=0（タイム未取得）でも着順・頭数が取れていれば取り込む（v1.1）
        # 海外レースはタイムが取れないケースが多いが、着順・着差でペナルティ評価できる
        if pr.distance > 0 and pr.finish > 0:
            past_races.append(pr)

        if len(past_races) >= limit:
            break

    return past_races


# ──────────────────────────────────────────────
# 全出走馬の過去走をまとめて取得
# ──────────────────────────────────────────────

def fetch_all_horses(race_url: str, past_limit: int = 5) -> tuple[RaceInfo, list[Horse]]:
    """
    レース情報取得 → 出走表取得 → 各馬の過去走取得 をまとめて実行
    戻り値: (RaceInfo, [Horse, ...])
    """
    race_info = fetch_race_info(race_url)
    horses    = fetch_shutuba(race_url)

    for horse in horses:
        if horse.horse_id:
            try:
                horse.past_races = fetch_past_races(horse.horse_id, limit=past_limit)
            except Exception as e:
                horse.past_races = []
                print(f"[WARN] {horse.name} の過去走取得失敗: {e}")
            time.sleep(1.0)

    return race_info, horses
