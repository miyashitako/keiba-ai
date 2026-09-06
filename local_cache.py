"""
local_cache.py（v1.0新規作成）

netkeibaから取得した生データ（馬の過去走履歴・レース確定結果）を
ローカルディスクにキャッシュするモジュール。

────────────────────────────────────────────
■ 背景
────────────────────────────────────────────
再キャリブレーションのサイクル（定数を変える→同じ期間を再検証→定数を
調整…）を繰り返すたびに、同じ馬・同じレースを何度もnetkeibaから再取得
しており、NAR・JRAとも1回あたり4〜5時間かかっていた。しかし「確定した
過去のレース結果」「馬の過去走履歴」は一度確定すれば変わらないデータ
なので、生データ自体をローカルにキャッシュしてしまえば、2回目以降は
ネットワークアクセスなしで即座に再計算できる（定数（calculator.py・
calculator_nar.py）が変わるたびに必要なのはスコアの再計算だけで、生データ
の再取得は不要）。

────────────────────────────────────────────
■ ディレクトリ構成（デフォルトはこのファイルと同じ場所の cache/ 以下。
  環境変数 KEIBA_CACHE_DIR で変更可能）
────────────────────────────────────────────
  cache/
    horses/{horse_id}.json   … その馬の生涯全過去走（PastRaceのリスト）
                                 + fetched_at（取得日）
    races/{race_id}.json     … そのレースの確定結果（RaceInfo + Horseの
                                 リスト、実績値含む）。レース結果は確定後は
                                 変化しないため、有効期限なしで永続キャッシュ
                                 する。

────────────────────────────────────────────
■ キャッシュの有効性判定
────────────────────────────────────────────
- レース結果（races/）：確定済みの過去レースは内容が変わらない前提のため、
  存在すれば無条件で有効。
- 馬の過去走（horses/）：fetch_past_races(horse_id, limit=None)は「今日
  時点での生涯全走」を毎回取得する設計のため、過去にキャッシュした時点
  （fetched_at）より後の日付のレースを評価したい場合、その間に新たに
  走ったレースがキャッシュに含まれていない可能性がある。そのため、
  「評価対象レースの日付 <= fetched_at」の場合のみキャッシュを有効とみなし、
  それ以外は再取得してキャッシュを更新する（＝過去日付の再検証である限り、
  一度取得した馬は基本的にずっとキャッシュが効き続ける）。
"""

import os
import json
import datetime
from dataclasses import fields

CACHE_DIR = os.environ.get(
    "KEIBA_CACHE_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache"),
)
HORSES_DIR = os.path.join(CACHE_DIR, "horses")
RACES_DIR = os.path.join(CACHE_DIR, "races")


def _ensure_dirs() -> None:
    os.makedirs(HORSES_DIR, exist_ok=True)
    os.makedirs(RACES_DIR, exist_ok=True)


def _obj_to_dict(obj) -> dict:
    """dataclassインスタンスを辞書化する。setattrで動的付与された属性
    （Horse.actual_finish等）もvars()経由で含める。
    注意：Horseをキャッシュする時点ではpast_races（PastRaceオブジェクトの
    リスト）はまだ空のはず（fetch_race_result直後にキャッシュする運用の
    ため）。ネストしたdataclassの再帰変換はここでは行わない。
    """
    return dict(vars(obj))


def _dict_to_obj(cls, d: dict):
    """辞書からdataclassインスタンスを復元する。dataclass宣言済みフィールドは
    コンストラクタに渡し、それ以外（動的付与されたactual_*等）はsetattrする。
    """
    declared_names = {f.name for f in fields(cls)}
    declared = {k: v for k, v in d.items() if k in declared_names}
    extra = {k: v for k, v in d.items() if k not in declared_names}
    obj = cls(**declared)
    for k, v in extra.items():
        setattr(obj, k, v)
    return obj


def get_horse_past_races(horse_id: str, min_valid_date: "str | None" = None):
    """
    キャッシュ済みの馬の生涯過去走を返す。キャッシュが無い、または
    min_valid_date（評価対象レースの日付、"YYYY/MM/DD"形式。省略時は
    日付チェックをスキップ）より fetched_at が古い場合は None を返す
    （＝呼び出し側は再取得が必要と判断すること）。
    """
    from scraper import PastRace  # 循環import回避のため関数内import

    path = os.path.join(HORSES_DIR, f"{horse_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    fetched_at = data.get("fetched_at", "")
    if min_valid_date and fetched_at < min_valid_date:
        return None   # キャッシュ取得時点より後の対象日 → 再取得が必要
    try:
        return [_dict_to_obj(PastRace, r) for r in data.get("past_races", [])]
    except Exception:
        return None   # 壊れたキャッシュファイル等 → 再取得にフォールバック


def set_horse_past_races(horse_id: str, past_races: list) -> None:
    _ensure_dirs()
    path = os.path.join(HORSES_DIR, f"{horse_id}.json")
    today_str = datetime.date.today().strftime("%Y/%m/%d")
    data = {
        "fetched_at": today_str,
        "past_races": [_obj_to_dict(r) for r in past_races],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def get_race_result(race_id: str):
    """
    キャッシュ済みのレース確定結果 (RaceInfo, list[Horse]) を返す。
    レース結果は確定後変化しない前提のため、存在すれば無条件で有効と
    みなす。無ければNoneを返す。
    """
    from scraper import RaceInfo, Horse

    path = os.path.join(RACES_DIR, f"{race_id}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        race_info = _dict_to_obj(RaceInfo, data["race_info"])
        horses = [_dict_to_obj(Horse, h) for h in data["horses"]]
        return race_info, horses
    except Exception:
        return None   # 壊れたキャッシュファイル等 → 再取得にフォールバック


def set_race_result(race_id: str, race_info, horses: list) -> None:
    """
    レース確定結果をキャッシュする。呼び出し側は、horses の各要素の
    past_races がまだ空（fetch_race_result直後）の状態で呼ぶこと
    （past_races入り後に呼ぶとPastRaceオブジェクトがJSON化できずエラーに
    なる）。
    """
    _ensure_dirs()
    path = os.path.join(RACES_DIR, f"{race_id}.json")
    data = {
        "race_info": _obj_to_dict(race_info),
        "horses": [_obj_to_dict(h) for h in horses],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def cache_stats() -> dict:
    """キャッシュ済み件数を返す（動作確認・状況把握用）。"""
    _ensure_dirs()
    n_horses = len([f for f in os.listdir(HORSES_DIR) if f.endswith(".json")])
    n_races = len([f for f in os.listdir(RACES_DIR) if f.endswith(".json")])
    return {"horses": n_horses, "races": n_races, "cache_dir": CACHE_DIR}
