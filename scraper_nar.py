"""
netkeiba 地方競馬（NAR）スクレイピングモジュール v0.1（骨格）

JRAとの違い：
- race_idの構成が「西暦4桁 + 競馬場番号2桁 + 日付4桁(MMDD) + レース番号2桁」＝12桁。
  JRAは「西暦4桁 + 競馬場番号2桁 + 開催回2桁 + 開催日2桁 + レース番号2桁」であり、
  中央の「開催回・開催日」に相当する部分が、地方では実日付そのものになっている。
- そのためUIは「競馬場」「日付（カレンダー）」「レース番号」の3つだけで完結でき、
  JRAのような「開催回」「開催日目」プルダウンは不要になる。

このファイルはrace_id組み立て・分解のみを担当する。
HTML取得・解析（fetch_race_info / fetch_shutuba / fetch_past_races）は
scraper.py の実装をそのまま再利用する（地方競馬場もscraper.py側の
LOCAL_VENUES判定に既に対応済みのため、原則そのまま動く想定）。

※ 現時点ではnetkeiba.comへの実アクセス検証ができていない骨格版。
   実機（Windows環境）で1レース分テストしてから本運用に入ること。

将来構想：
- 共通関数（time_to_sec / margin_to_sec / RaceInfo / PastRace / Horse 等）は
  utils.py に切り出し、scraper.py / scraper_nar.py 両方から参照する形に整理する予定。
  現状はscraper.pyから直接importする暫定構成。
"""

import datetime
import re
import unicodedata
from typing import Optional

# ── RaceData02からのクラス抽出用候補リスト ─────────────────────────
# 注意：門別3Rの事後検証（2026/7/15）でC4クラスの存在が確認されたため追加。
# 他場でもA4・B4等の存在は未確認だが、将来の同様の見落としを防ぐため
# 念のためA1〜A3/B1〜B4/C1〜C4まで用意しておく。
NAR_RACE_CLASS_CANDIDATES = [
    "OP", "オープン", "重賞", "Jpn3", "Jpn2", "Jpn1",
    "3勝クラス", "2勝クラス", "1勝クラス", "未勝利", "新馬",
    "A1", "A2", "A3",
    "B1", "B2", "B3", "B4",
    "C1", "C2", "C3", "C4",
    # 馬齢限定戦の呼称違い（組・数字を使わない場合がある）：
    # フレッシュ＝新馬戦相当、チャレンジ＝未勝利戦相当
    # ("2歳フレッシュ"のように年齢表記と併記されることがあるため、
    #  より具体的なこちらを先に判定する)
    "フレッシュ", "チャレンジ",
    # 馬齢限定戦（A/B/Cが付かず、世代内の賞金順「組」のみで分けられる
    # 2〜3歳戦。例："2歳ー3組"）。誤検出防止のため必ずA/B/C判定より後ろに
    # 置くこと（"3歳以上C1"のような age条件+クラスの表記でC1を優先させるため）。
    "2歳", "3歳", "4歳",
]


def _extract_nar_race_class(text_norm: str) -> str:
    """
    RaceData02等の連結テキストから、クラス表記に加えて直後に続く
    「組」相当の細分表記（例："C4ー2"の"ー2"、"C2七組"の"七組"）まで
    まとめて拾う。extract_fine_tier()側の解析対象を豊かにするための処理。

    見つかった大枠クラス（例:"C4"）の直後最大8文字だけを覗き、
    ハイフン/長音符+数字、または漢数字（+空白+漢数字、+"組"）の
    パターンに一致すれば付加する。一致しなければ大枠のみ返す。
    """
    for cls in NAR_RACE_CLASS_CANDIDATES:
        idx = text_norm.find(cls)
        if idx == -1:
            continue
        tail = text_norm[idx + len(cls): idx + len(cls) + 8]
        m = re.match(r'[-ー－][0-9]{1,2}', tail)
        if m:
            return cls + m.group(0)
        m = re.match(r'(?:[ 　]{0,2}[一二三四五六七八九十]{1,3}){1,3}(?:組)?', tail)
        if m and m.group(0).strip():
            return cls + m.group(0)
        return cls
    return ""

import requests
from bs4 import BeautifulSoup

# scraper.py の共通処理をそのまま再利用（暫定。将来utils.pyへ移行予定）
from scraper import (
    HEADERS,
    RaceInfo,
    PastRace,
    Horse,
    extract_race_id,
    extract_horse_id,
    fetch_past_races,   # db.netkeiba.com/horse/result/ は中央・地方共通なのでそのまま使う
    KNOWN_HANDICAP_RACES,
)

# ──────────────────────────────────────────────
# 重要：地方競馬(NAR)は race.netkeiba.com ではなく nar.netkeiba.com
# ドメインが異なる。またパスも「shutuba_past.html」ではなく「shutuba.html」。
# 例: https://nar.netkeiba.com/race/shutuba.html?race_id=202635071301
#
# scraper.py の fetch_race_info / fetch_shutuba は、渡されたURLから
# race_idだけ抜き出し、内部で "https://race.netkeiba.com/race/shutuba_past.html?..."
# を再構築してしまう実装のため、そのまま使うとNARレースでも
# 誤ってJRAドメインにアクセスしてしまう。そのためNAR専用のfetch関数を
# 以下に用意する（HTML構造はJRA版shutuba_past.htmlと同一という前提で
# 実装しているが、未検証。実機でtest_nar.pyを実行して確認すること）。
# ──────────────────────────────────────────────

NAR_SHUTUBA_URL_TEMPLATE = "https://nar.netkeiba.com/race/shutuba.html?race_id={race_id}"


# ──────────────────────────────────────────────
# 地方競馬場コード表（ユーザー確認済み）
# ──────────────────────────────────────────────
NAR_VENUE_OPTIONS = [
    ("65", "帯広"),   # ばんえい競走
    ("30", "門別"),
    ("35", "盛岡"),
    ("36", "水沢"),
    ("42", "浦和"),
    ("43", "船橋"),
    ("44", "大井"),
    ("45", "川崎"),
    ("46", "金沢"),
    ("47", "笠松"),
    ("48", "名古屋"),
    ("50", "園田"),
    ("51", "姫路"),
    ("54", "高知"),
    ("55", "佐賀"),
]

NAR_VENUE_CODE_TO_NAME = {code: name for code, name in NAR_VENUE_OPTIONS}
NAR_VENUE_NAME_TO_CODE = {name: code for code, name in NAR_VENUE_OPTIONS}


# ──────────────────────────────────────────────
# race_id 組み立て・分解
# ──────────────────────────────────────────────

def build_nar_race_id(
    venue: str,
    race_date: "datetime.date | str",
    race_no: int,
    year: Optional[int] = None,
) -> str:
    """
    地方競馬のrace_idを組み立てる。

    Parameters
    ----------
    venue : str
        競馬場名（例："大井"）または競馬場番号（例："44"）
    race_date : datetime.date または "MM-DD"/"MMDD" 形式の文字列
        レース開催日。yearを別途指定しない場合、datetime.date型ならその年を使う。
        文字列で渡す場合は年を含まないため、year引数の指定が必須。
    race_no : int
        レース番号（1〜12）
    year : int, optional
        西暦年。race_dateがdatetime.date型の場合は省略可（date.yearを使用）。

    Returns
    -------
    str
        12桁のrace_id（例："202644071301"）

    Raises
    ------
    ValueError
        競馬場名/番号が不明、年が特定できない、レース番号が範囲外の場合
    """
    # 競馬場コード解決
    if venue in NAR_VENUE_CODE_TO_NAME:
        venue_code = venue
    elif venue in NAR_VENUE_NAME_TO_CODE:
        venue_code = NAR_VENUE_NAME_TO_CODE[venue]
    else:
        raise ValueError(f"不明な地方競馬場です: {venue}")

    # 日付解決
    if isinstance(race_date, datetime.date):
        y = year or race_date.year
        mmdd = f"{race_date.month:02d}{race_date.day:02d}"
    else:
        date_str = str(race_date).strip().replace("-", "").replace("/", "")
        if len(date_str) != 4 or not date_str.isdigit():
            raise ValueError(
                f"race_dateが文字列の場合は'MMDD'または'MM-DD'形式で指定してください: {race_date}"
            )
        if year is None:
            raise ValueError("race_dateを文字列で指定する場合はyearを必ず指定してください。")
        y = year
        mmdd = date_str

    if not (1 <= race_no <= 12):
        raise ValueError(f"レース番号は1〜12の範囲で指定してください: {race_no}")

    return f"{y:04d}{venue_code}{mmdd}{race_no:02d}"


def parse_nar_race_id(race_id: str) -> dict:
    """
    地方競馬のrace_id（12桁）を分解する。

    Returns
    -------
    dict
        {"year": int, "venue_code": str, "venue_name": str,
         "month": int, "day": int, "race_no": int}
    """
    race_id = str(race_id).strip()
    if len(race_id) != 12 or not race_id.isdigit():
        raise ValueError(f"race_idは12桁の数字である必要があります: {race_id}")

    year = int(race_id[0:4])
    venue_code = race_id[4:6]
    month = int(race_id[6:8])
    day = int(race_id[8:10])
    race_no = int(race_id[10:12])

    return {
        "year": year,
        "venue_code": venue_code,
        "venue_name": NAR_VENUE_CODE_TO_NAME.get(venue_code, "不明"),
        "month": month,
        "day": day,
        "race_no": race_no,
    }


def build_day_race_ids(
    venue: str,
    race_date: "datetime.date | str",
    year: Optional[int] = None,
    max_race_no: int = 12,
) -> list[str]:
    """
    指定の競馬場・日付について、1R〜max_race_noまでのrace_idを一括生成する。
    「その日の全レースを機械的に取得する」用途を想定（ばんえい帯広は最大12Rでない
    場合があるため、必要に応じてmax_race_noを調整すること）。
    """
    return [
        build_nar_race_id(venue, race_date, r, year=year)
        for r in range(1, max_race_no + 1)
    ]


def race_id_to_shutuba_url(race_id: str) -> str:
    """race_id → 出走表URL（nar.netkeiba.com/race/shutuba.html）"""
    return NAR_SHUTUBA_URL_TEMPLATE.format(race_id=race_id)


def race_id_to_db_url(race_id: str) -> str:
    """
    race_id → レース結果ページURL（確定後のレースのみ有効）
    ※ 未検証：地方競馬の確定結果ページがdb.netkeiba.comと同一ドメインか、
      nar.netkeiba.com側にあるか未確認。要実機確認。
    """
    return f"https://nar.netkeiba.com/race/result.html?race_id={race_id}"


# ──────────────────────────────────────────────
# NAR専用：レース情報取得
# ──────────────────────────────────────────────

def fetch_race_info_nar(race_id: str) -> RaceInfo:
    """
    nar.netkeiba.com/race/shutuba.html からレース情報を取得する。

    ※ 未検証：nar.netkeiba.comのHTML構造がJRA版(race.netkeiba.com)の
      shutuba_past.htmlと同一クラス名（RaceName / RaceData01 / RaceData02）
      かどうか未確認。test_nar.pyで実機確認すること。
      構造が違えば下記の抽出処理を調整する必要がある。
    """
    info = RaceInfo(race_id=race_id)

    # race_idからわかる情報は先に埋めておく（ページ解析が失敗しても最低限残る）
    try:
        parsed = parse_nar_race_id(race_id)
        info.venue = parsed["venue_name"]
        info.race_date = f"{parsed['year']}年{parsed['month']}月{parsed['day']}日"
    except ValueError:
        pass

    shutuba_url = race_id_to_shutuba_url(race_id)
    try:
        res = requests.get(shutuba_url, headers=HEADERS, timeout=15)
    except Exception:
        return info

    if res.status_code != 200:
        return info

    # エンコーディング未検証。UTF-8を第一候補とし、文字化けが疑わしい場合は
    # EUC-JPも試す必要がある（要実機確認）
    html_text = res.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_text, "html.parser")

    race_name_el = soup.find("h1", class_="RaceName")
    if race_name_el:
        info.race_name = race_name_el.get_text(strip=True)

    # titleタグからのレース名抽出（h1が空の場合のフォールバック）
    # title例: "ランチタイム 出馬表 | 2026年7月13日 浦和1R 地方競馬レース情報 - netkeiba"
    # 「出馬表」より前の部分が実際のレース名（無名の条件戦だとクラス名"C2"等になる）
    if not info.race_name:
        title_el = soup.find("title")
        if title_el:
            title_text = title_el.get_text(strip=True)
            m = re.match(r"^(.+?)\s*出馬表\s*\|", title_text)
            if m:
                info.race_name = m.group(1).strip()

    # それでも取れない場合の最終フォールバック：「盛岡1R」のような簡易名を合成
    if not info.race_name:
        try:
            parsed = parse_nar_race_id(race_id)
            info.race_name = f"{parsed['venue_name']}{parsed['race_no']}R"
        except ValueError:
            pass

    data01 = soup.find("div", class_="RaceData01")
    if data01:
        text01 = data01.get_text(strip=True)
        m = re.search(r"([芝ダ])(\d+)m", text01)
        if m:
            info.surface = m.group(1)
            info.distance = int(m.group(2))
        m = re.search(r"\(([左右])", text01)
        if m:
            info.direction = m.group(1)
        m = re.search(r"天候:(\S+?)(?:/|$)", text01)
        if m:
            info.weather = m.group(1).strip()
        m = re.search(r"馬場:(\S+?)(?:/|$|\s)", text01)
        if m:
            info.track_cond = m.group(1).strip()

    data02 = soup.find("div", class_="RaceData02")
    _wt_text = ""
    if data02:
        text02_norm = unicodedata.normalize("NFKC", data02.get_text(strip=True))
        _wt_text = text02_norm
        info.race_class = _extract_nar_race_class(text02_norm)

    # 斤量方式（ハンデ/別定/定量）。地方は判定基準がJRAと異なる可能性があるため参考程度
    _wt_combined = _wt_text + info.race_name
    if "ハンデ" in _wt_combined:
        info.weight_type = "ハンデ"
    elif "別定" in _wt_combined:
        info.weight_type = "別定"
    elif "定量" in _wt_combined or "馬齢重量" in _wt_combined:
        info.weight_type = "定量"
    else:
        for known in KNOWN_HANDICAP_RACES:
            if known in info.race_name:
                info.weight_type = "ハンデ"
                break

    return info


# ──────────────────────────────────────────────
# NAR専用：出走表取得
# ──────────────────────────────────────────────

def fetch_shutuba_nar(race_id: str) -> list[Horse]:
    """
    nar.netkeiba.com/race/shutuba.html から出走表を取得する。

    実機確認済みの列構成（2026/7/13 盛岡1Rで確認）：
      0:枠 1:馬番 2:印 3:馬名 4:性齢 5:斤量 6:騎手 7:厩舎 8:馬体重(増減) ...
    JRA版（shutuba_past.html）と違い「印」列が1つ挟まる分ズレるが、
    性齢・斤量・騎手がそれぞれ独立した列になっているため、
    JRA版のような「列4の文字列をパースして性別・斤量・騎手を分離する」
    処理は不要で、むしろシンプルに取得できる。
    """
    shutuba_url = race_id_to_shutuba_url(race_id)

    try:
        res = requests.get(shutuba_url, headers=HEADERS, timeout=15)
    except Exception as e:
        raise ConnectionError(f"出走表の取得に失敗しました: {e}")

    if res.status_code != 200:
        raise ConnectionError(f"HTTPエラー: {res.status_code}")

    html_text = res.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_text, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        raise ValueError("テーブルが見つかりませんでした。HTML構造がJRAと異なる可能性があります。")

    table = tables[0]
    rows = table.find_all("tr")[1:]

    horses = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 7:
            continue

        horse = Horse()

        try:
            horse.frame = int(cols[0].get_text(strip=True))
        except Exception:
            pass
        try:
            horse.number = int(cols[1].get_text(strip=True))
        except Exception:
            pass

        # col[2]は「印」列（予想マーク）のためスキップ
        name_cell = cols[3]
        horse_link = name_cell.find("a", href=re.compile(r"/horse/\d+"))
        if horse_link:
            horse.horse_id = extract_horse_id(horse_link["href"]) or ""
            horse.name = horse_link.get_text(strip=True)
        else:
            horse.name = name_cell.get_text(strip=True)[:10]

        if not horse.name:
            continue

        # col[4]: 性齢（例："牝5"）
        sex_age_text = cols[4].get_text(strip=True)
        horse._col4_raw = repr(sex_age_text)
        if sex_age_text:
            if sex_age_text[0] in ("牡", "牝"):
                horse.sex = sex_age_text[0]
            elif sex_age_text[0] in ("セ", "騸"):
                horse.sex = "セ"

        # col[5]: 斤量（例："54.0"）
        weight_text = cols[5].get_text(strip=True)
        try:
            val = float(weight_text)
            if 40.0 <= val <= 65.0:   # 地方は軽量斤量レースもあるため下限をJRAより緩めに設定
                horse.weight_carried = val
        except Exception:
            pass

        # col[6]: 騎手
        jockey_link = cols[6].find("a", href=re.compile(r"/jockey/"))
        if jockey_link:
            horse.jockey = jockey_link.get_text(strip=True)
        else:
            horse.jockey = cols[6].get_text(strip=True)

        horses.append(horse)

    if all(h.number == 0 for h in horses):
        for i, h in enumerate(horses, 1):
            h.number = i

    return horses


def debug_dump_shutuba_columns_nar(race_id: str, num_rows: int = 3) -> None:
    """
    診断用：出走表テーブルの各列の生テキストをそのまま表示する。
    fetch_shutuba_narの列インデックス（斤量・騎手等）がズレている場合に、
    正しい列位置を特定するために使う。

    また、race_nameが取れない問題の調査用に、
    h1タグ・title・その他候補となりそうな要素も併せてダンプする。
    """
    shutuba_url = race_id_to_shutuba_url(race_id)
    res = requests.get(shutuba_url, headers=HEADERS, timeout=15)
    html_text = res.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_text, "html.parser")

    print("\n" + "#" * 60)
    print("[DEBUG] レース名候補の調査")
    print("#" * 60)
    title_el = soup.find("title")
    print(f"  <title>: {title_el.get_text(strip=True) if title_el else '(なし)'}")
    for h1 in soup.find_all("h1"):
        print(f"  <h1 class={h1.get('class')}>: {h1.get_text(strip=True)!r}")
    for h2 in soup.find_all("h2")[:5]:
        print(f"  <h2 class={h2.get('class')}>: {h2.get_text(strip=True)!r}")

    print("\n" + "#" * 60)
    print("[DEBUG] 出走表テーブルの列構成調査")
    print("#" * 60)
    tables = soup.find_all("table")
    print(f"  テーブル数: {len(tables)}")
    if not tables:
        return
    table = tables[0]
    rows = table.find_all("tr")
    print(f"  行数（ヘッダ含む）: {len(rows)}")

    header_cells = rows[0].find_all(["th", "td"])
    print(f"  ヘッダ列数: {len(header_cells)}")
    for i, c in enumerate(header_cells):
        print(f"    header[{i}]: {c.get_text(strip=True)!r}")

    for r_idx, row in enumerate(rows[1:1 + num_rows], start=1):
        cols = row.find_all("td")
        print(f"\n  --- データ行{r_idx}（列数={len(cols)}） ---")
        for i, c in enumerate(cols):
            text = c.get_text(strip=True)
            links = [a.get("href") for a in c.find_all("a")]
            link_info = f" links={links}" if links else ""
            print(f"    col[{i}]: {text!r}{link_info}")


# ──────────────────────────────────────────────
# 便利関数：競馬場名＋日付＋レース番号 から直接データ取得
# ──────────────────────────────────────────────

def fetch_race_result_nar(race_id: str) -> tuple[RaceInfo, list]:
    """
    nar.netkeiba.com/race/result.html から確定結果を取得する（事後検証用）。

    レース終了後はshutuba.html（出走前ページ）が使えなくなるため、
    「日付をまたいでしまって同じレースで再検証したい」場合はこちらを使う。

    実データ確認済みの列構成（イーハトーブマイル・盛岡11Rで確認）：
      0:着順 1:枠 2:馬番 3:馬名 4:性齢 5:斤量 6:騎手 7:タイム 8:着差
      9:人気 10:単勝オッズ 11:後3F 12:厩舎 13:馬体重(増減)
    shutuba.html（0:枠 1:馬番 2:印 3:馬名 4:性齢 5:斤量 6:騎手...）とは
    列順が異なるので注意（「印」列がなく、先頭に「着順」が来る）。

    Returns
    -------
    (RaceInfo, list[Horse])
        Horseオブジェクトには実際の着順・タイム・人気を
        actual_finish / actual_time_str / actual_popularity として追加する
        （dataclassの標準フィールドではなく、setattrで動的に付与）。
    """
    result_url = f"https://nar.netkeiba.com/race/result.html?race_id={race_id}"

    try:
        res = requests.get(result_url, headers=HEADERS, timeout=15)
    except Exception as e:
        raise ConnectionError(f"確定結果の取得に失敗しました: {e}")
    if res.status_code != 200:
        raise ConnectionError(f"HTTPエラー: {res.status_code}")

    html_text = res.content.decode("utf-8", errors="replace")
    soup = BeautifulSoup(html_text, "html.parser")

    # ── レース情報（fetch_race_info_narと同じロジックを再利用）
    info = RaceInfo(race_id=race_id)
    try:
        parsed = parse_nar_race_id(race_id)
        info.venue = parsed["venue_name"]
        info.race_date = f"{parsed['year']}年{parsed['month']}月{parsed['day']}日"
    except ValueError:
        pass

    race_name_el = soup.find("h1", class_="RaceName")
    if race_name_el:
        info.race_name = race_name_el.get_text(strip=True)
    if not info.race_name:
        title_el = soup.find("title")
        if title_el:
            m = re.match(r"^(.+?)\s*結果・払戻\s*\|", title_el.get_text(strip=True))
            if m:
                info.race_name = m.group(1).strip()
    if not info.race_name:
        try:
            parsed = parse_nar_race_id(race_id)
            info.race_name = f"{parsed['venue_name']}{parsed['race_no']}R"
        except ValueError:
            pass

    data01 = soup.find("div", class_="RaceData01")
    if data01:
        text01 = data01.get_text(strip=True)
        m = re.search(r"([芝ダ])(\d+)m", text01)
        if m:
            info.surface = m.group(1)
            info.distance = int(m.group(2))
        m = re.search(r"\(([左右])", text01)
        if m:
            info.direction = m.group(1)
        m = re.search(r"天候:(\S+?)(?:/|$)", text01)
        if m:
            info.weather = m.group(1).strip()
        m = re.search(r"馬場:(\S+?)(?:/|$|\s)", text01)
        if m:
            info.track_cond = m.group(1).strip()

    data02 = soup.find("div", class_="RaceData02")
    if data02:
        text02_norm = unicodedata.normalize("NFKC", data02.get_text(strip=True))
        info.race_class = _extract_nar_race_class(text02_norm)

    # ── 確定結果テーブル
    tables = soup.find_all("table")
    if not tables:
        raise ValueError("結果テーブルが見つかりませんでした。")
    table = tables[0]
    rows = table.find_all("tr")[1:]

    horses = []
    for row in rows:
        cols = row.find_all("td")
        if len(cols) < 9:
            continue

        h = Horse()
        actual_finish_text = cols[0].get_text(strip=True)
        try:
            actual_finish = int(actual_finish_text)
        except Exception:
            actual_finish = 0   # 失格・中止等（"取消""除外""中止"等の文字列）

        try:
            h.frame = int(cols[1].get_text(strip=True))
        except Exception:
            pass
        try:
            h.number = int(cols[2].get_text(strip=True))
        except Exception:
            pass

        name_cell = cols[3]
        horse_link = name_cell.find("a", href=re.compile(r"/horse/\d+"))
        if horse_link:
            h.horse_id = extract_horse_id(horse_link["href"]) or ""
            h.name = horse_link.get_text(strip=True)
        else:
            h.name = name_cell.get_text(strip=True)[:10]
        if not h.name:
            continue

        sex_age_text = cols[4].get_text(strip=True)
        if sex_age_text:
            if sex_age_text[0] in ("牡", "牝"):
                h.sex = sex_age_text[0]
            elif sex_age_text[0] in ("セ", "騸"):
                h.sex = "セ"

        try:
            val = float(cols[5].get_text(strip=True))
            if 40.0 <= val <= 65.0:
                h.weight_carried = val
        except Exception:
            pass

        jockey_link = cols[6].find("a", href=re.compile(r"/jockey/"))
        h.jockey = jockey_link.get_text(strip=True) if jockey_link else cols[6].get_text(strip=True)

        # 実績値（Horseの標準フィールドにはないため動的付与）
        h.actual_finish = actual_finish
        h.actual_time_str = cols[7].get_text(strip=True) if len(cols) > 7 else ""
        h.actual_margin_str = cols[8].get_text(strip=True) if len(cols) > 8 else ""
        h.actual_popularity = cols[9].get_text(strip=True) if len(cols) > 9 else ""

        horses.append(h)

    return info, horses


def fetch_all_horses_nar_backtest(
    venue: str,
    race_date: "datetime.date | str",
    race_no: int,
    year: Optional[int] = None,
    past_limit: int = 3,
) -> tuple[RaceInfo, list]:
    """
    レース終了後の事後検証用：確定結果ページから出走馬情報＋実際の着順を取得し、
    各馬の過去走も取得する。

    重要：このレース自体が対象馬の「最新の過去走」としてfetch_past_racesに
    含まれてしまうため、target_dateと同じ日付の過去走は除外してから
    calc_phase1_narに渡すこと（データリーク防止）。
    このため本関数はpast_limit+1件多めに取得し、対象レース当日の記録を
    自動的にフィルタして返す。
    """
    race_id = build_nar_race_id(venue, race_date, race_no, year=year)
    race_info, horses = fetch_race_result_nar(race_id)

    if isinstance(race_date, datetime.date):
        target_date_str = f"{race_date.year}/{race_date.month:02d}/{race_date.day:02d}"
    else:
        target_date_str = None   # 文字列指定の場合は日付フィルタ不可（要手動確認）

    import time
    for horse in horses:
        if horse.horse_id:
            try:
                raw_past = fetch_past_races(horse.horse_id, limit=past_limit + 3)
                if target_date_str:
                    raw_past = [pr for pr in raw_past if pr.date != target_date_str]
                horse.past_races = raw_past[:past_limit]
            except Exception as e:
                horse.past_races = []
                print(f"[WARN] {horse.name} の過去走取得失敗: {e}")
            time.sleep(1.0)

    return race_info, horses


def fetch_all_horses_nar(
    venue: str,
    race_date: "datetime.date | str",
    race_no: int,
    year: Optional[int] = None,
    past_limit: int = 5,
) -> tuple[RaceInfo, list[Horse]]:
    """
    競馬場名・日付・レース番号からrace_idを組み立て、
    NAR専用fetch関数（nar.netkeiba.comドメイン）でデータ取得する。
    """
    race_id = build_nar_race_id(venue, race_date, race_no, year=year)

    race_info = fetch_race_info_nar(race_id)
    horses = fetch_shutuba_nar(race_id)

    import time
    for horse in horses:
        if horse.horse_id:
            try:
                horse.past_races = fetch_past_races(horse.horse_id, limit=past_limit)
            except Exception as e:
                horse.past_races = []
                print(f"[WARN] {horse.name} の過去走取得失敗: {e}")
            time.sleep(1.0)

    return race_info, horses
