"""
競馬AI予想システム - 計算エンジン
Phase1〜Phase4 を固定数式で計算する
"""

from dataclasses import dataclass, field
from typing import Optional
import statistics
import copy


# ──────────────────────────────────────────────
# 定数
# ──────────────────────────────────────────────

BASE_DISTANCE   = 1600   # 距離補正基準（距離適性ボーナス用）
DISTANCE_FACTOR = 0.1
BASE_WEIGHT     = 55.0
WEIGHT_FACTOR   = 0.1

# ── クラス基準値（v1.0：着順ベース新Phase1）─────────────────────────
# 小さいほど格が高い（良評価）。タイムの代わりにスコアの基軸となる。
# スコア分布が85〜150台になるよう設定（旧タイムベースとスケール統一）
CLASS_BASE = {
    "新馬":       92.0,   # 1勝クラスと同格（v1.1：新馬勝ち ≒ 1勝クラス勝ち）
    "未勝利":     95.0,
    "1勝クラス":  92.0,
    "500万下":    92.0,
    "2勝クラス":  88.0,
    "1000万下":   88.0,
    "3勝クラス":  84.0,
    "1600万下":   84.0,
    "OP":         80.0,
    "オープン":   80.0,
    "L":          81.0,   # Listed
    "G3":         76.0,
    "Jpn3":       76.0,
    "G2":         72.0,
    "Jpn2":       72.0,
    "G1":         68.0,
    "Jpn1":       68.0,
}
CLASS_BASE_DEFAULT = 92.0

# ── 格ボーナステーブル（復活 v1.0）──────────────────────────────────
# 過去走でG1〜Listed戦に4着以内なら付与。スコアから引く（小さいほど有利）。
# ListedやGⅠ5着馬の区別をクラス基準値だけでなく格実績でも補強する。
GRADE_BONUS_TABLE = {
    "G1":       3.0,
    "Jpn1":     3.0,
    "G2":       2.0,
    "Jpn2":     2.0,
    "G3":       1.2,
    "Jpn3":     1.2,
    "OP":       0.6,
    "オープン": 0.6,
    "L":        0.5,
}
GRADE_RANK_SCALE = {1: 1.0, 2: 0.8, 3: 0.6, 4: 0.4}

# ── 近走不振ペナルティ（復活 v1.0）──────────────────────────────────
# 着順ベースになったことでコアスコアに不振が反映されるが、
# 「直近の勢い」は加重平均より明示的なペナルティで補強する。
RECENT_FORM_PENALTY = [
    (8.0, 3.0),   # 加重平均着順 ≥ 8.0 → +3.0ポイント
    (6.0, 1.5),   # ≥ 6.0 → +1.5
    (4.5, 0.5),   # ≥ 4.5 → +0.5
]

# ── 着順ボーナス（スコアから引く、小さいほど良評価）────────────────
FINISH_BONUS = {
    1:  8.0,
    2:  5.0,
    3:  3.0,
    4:  1.5,
    5:  0.5,
    # 6着以下: 0.0
}
FINISH_BONUS_DEFAULT = 0.0

# ── 着差ボーナス（1着タイムとの差、秒ベース）────────────────────────
# 0.3秒以内なら-2.0、0.5秒以内なら-1.0、0.5秒超なら0
MARGIN_BONUS_THRESHOLDS = [
    (0.3, 2.0),
    (0.5, 1.0),
    (9.9, 0.0),
]

WEIGHT_RECENT = [0.5, 0.3, 0.2]

# Phase2係数（着順のばらつきベース）
INSTABILITY_FACTOR   = 0.3   # 着順std × 0.3 をペナルティ
BEST_BONUS_FACTOR    = 0.5   # ベスト着順との乖離ボーナス（旧互換）

# 距離フィルター：今回レース距離 ± この値（m）以内の過去走のみ対象
DISTANCE_FILTER_MARGIN = 200

# ── 走数不足ペナルティ：廃止（v1.0）────────────────────────────────
# 走数が少ない馬はコアスコア自体が実績を正直に反映するため追加ペナルティは不要。
# 「実力があって走数が少ない馬」に二重罰を与えないよう完全廃止。
SPARSE_RUNS_PENALTY = {}   # 空dict（廃止）

# ── 距離適性ボーナステーブル（v0.7追加）──────────────────────────────
DIST_GOOD_FINISH_BONUS = {1: 1.2, 2: 0.9, 3: 0.6}   # 旧0.08→着順スケールに合わせて拡大

DIST_BONUS_MARGIN_THRESHOLDS = [
    (0.3, 1.0),   # 0.3秒以下：フルボーナス
    (0.5, 0.5),   # 0.3〜0.5秒：50%
    (9.9, 0.0),   # 0.5秒超：ゼロ（5馬身以上離された着順は好走とみなさない）
]
DIST_STAMINA_BONUS    = 0.5    # 旧0.03
DIST_NO_RECORD_PENALTY = 1.0   # 旧0.07

# ── 競馬場→回り方向テーブル（v1.0追加）────────────────────────────
# 左回り：東京・新潟・中京
# 右回り：中山・阪神・京都・小倉・福島・札幌・函館
VENUE_TURN_DIRECTION = {
    "東京": "左", "新潟": "左", "中京": "左",
    "中山": "右", "阪神": "右", "京都": "右",
    "小倉": "右", "福島": "右", "札幌": "右", "函館": "右",
}

# ── 条件適性ボーナス値（v1.0追加）──────────────────────────────────
# 馬場・回り・競馬場のボーナスはポイントスケール（小さいほど有利）
CONDITION_BONUS_TABLE = {
    # 馬場状態ボーナス（過去走で同馬場条件3着以内・着差0.5秒以内）
    "track_good":    0.8,   # 良馬場好走実績
    "track_bad":     1.2,   # 重・不良好走実績（道悪巧者はより希少価値）
    # 回り適性ボーナス（同回りで3着以内・着差0.5秒以内）
    "turn":          0.6,
    # 競馬場適性ボーナス（ポイントスケールに更新）
    "venue_avg3":    0.8,   # 同競馬場平均着順3以内
    "venue_avg5":    0.4,   # 同競馬場平均着順5以内
}

# 馬場状態グループ分け
TRACK_GOOD  = {"良"}
TRACK_BAD   = {"重", "不良", "稍重"}   # 稍重も道悪寄りとして扱う

# 昇級判定用クラス順序（数値が大きいほど格上）
CLASS_ORDER = [
    "新馬", "未勝利", "500万下", "1勝クラス", "1000万下",
    "2勝クラス",
    "3勝クラス", "1600万下",
    "OP", "オープン", "L",
    "G3", "Jpn3",
    "G2", "Jpn2",
    "G1", "Jpn1",
]

# 騎手ランク補正テーブル（v0.6刷新）
# 算出基準: JRA年間勝率10%を±0とし、差分×0.30でスケール換算
# 過去走に騎手との実績がある場合はそちらを優先し、本テーブルはフォールバック
# キー: netkeiba表記の騎手名（完全一致優先、前方一致フォールバック）
JOCKEY_BONUS = {
    # ── Tier1: +1.5pt（≤-0.040）ルメール・モレイラ・ムーア・川田
    "J.モレイラ":   -0.058,
    "C.ルメール":   -0.058,
    "R.ムーア":     -0.050,
    "川田将雅":     -0.042,
    # ── Tier2: +0.8pt（≤-0.015）坂井・戸崎・横山武・デムーロ兄弟・レーン等外国人
    "坂井瑠星":     -0.019,
    "戸崎圭太":     -0.018,
    "横山武史":     -0.016,
    "C.デムーロ":   -0.016,
    "M.デムーロ":   -0.016,
    "T.レーン":     -0.016,
    "D.レーン":     -0.016,
    # ── Tier3: +0.4pt（≤-0.005）武豊のみ
    "武豊":         -0.009,
    # ── Tier4: 0pt（ボーナスなし）西村・岩田望・吉田・松山等
    "松山弘平":      0.000,
    "西村淳也":      0.000,
    "岩田望来":      0.000,
    "吉田隼人":      0.000,
    "菅原明良":      0.000,
    "浜中俊":        0.000,
    "田辺裕信":      0.000,
    "北村友一":      0.000,
    "幸英明":        0.000,
    "横山典弘":      0.000,
    "三浦皇成":      0.000,
}

# 騎手名の表記ゆれ対応マップ（netkeiba → 正規名）
JOCKEY_ALIAS = {
    # ── netkeiba出馬表は騎手名を最大4文字で切り捨てる ──
    # Tier1（+1.5pt）
    "ルメー":    "C.ルメール",   # ルメール → ルメー
    "C.ルメー":  "C.ルメール",
    "ルメール":  "C.ルメール",
    "Ｃ．ルメー": "C.ルメール",
    "モレイラ":  "J.モレイラ",
    "ムーア":    "R.ムーア",
    "川田":      "川田将雅",     # 川田将雅 → 川田
    "川田将":    "川田将雅",
    # Tier2（+0.8pt）
    "坂井":      "坂井瑠星",     # 坂井瑠星 → 坂井
    "坂井瑠":    "坂井瑠星",
    "戸崎圭":    "戸崎圭太",     # 戸崎圭太 → 戸崎圭
    "戸崎":      "戸崎圭太",
    "横山武":    "横山武史",     # 横山武史 → 横山武
    "M.デムー":  "M.デムーロ",
    "C.デムー":  "C.デムーロ",
    "レーン":    "T.レーン",     # 姓のみ表記
    "T.レーン":  "T.レーン",
    "D.レーン":  "D.レーン",
    "Ｔ．レーン": "T.レーン",
    # Tier3（+0.4pt）
    "武豊":      "武豊",
    # Tier4（0pt・表記ゆれのみ登録）
    "横山典":    "横山典弘",
    "松山":      "松山弘平",     # 松山弘平 → 松山
    "松山弘":    "松山弘平",
    "岩田望":    "岩田望来",
    "吉田隼":    "吉田隼人",
    "菅原明":    "菅原明良",
    "西村淳":    "西村淳也",
    "浜中":      "浜中俊",       # 浜中俊 → 浜中
    "浜中俊":    "浜中俊",
}


# ──────────────────────────────────────────────
# データクラス
# ──────────────────────────────────────────────

@dataclass
class Phase1Result:
    horse_name: str = ""
    horse_number: int = 0
    corrected_times: list = field(default_factory=list)
    ability_avg: float = 0.0
    best_time: float = 0.0
    phase1_score: float = 0.0
    valid_runs: int = 0
    filtered_runs: int = 0    # 距離フィルターで除外した走数
    note: str = ""


@dataclass
class Phase2Result:
    horse_name: str = ""
    horse_number: int = 0
    phase1_score: float = 0.0
    best_time: float = 0.0
    std_dev: float = 0.0
    best_bonus: float = 0.0
    instability_penalty: float = 0.0
    phase2_score: float = 0.0
    valid_runs: int = 0
    note: str = ""


@dataclass
class VenueJockeyStats:
    """競馬場・騎手・馬場・回り適性集計（過去走から動的計算）"""
    horse_name: str = ""
    horse_number: int = 0
    venue_runs: int = 0
    venue_avg_finish: float = 0.0
    venue_win_rate: float = 0.0
    jockey_runs: int = 0
    jockey_avg_finish: float = 0.0
    jockey_bonus: float = 0.0
    venue_bonus: float = 0.0
    track_bonus: float = 0.0    # 馬場状態適性ボーナス（v1.0追加）
    turn_bonus: float = 0.0     # 回り適性ボーナス（v1.0追加）


@dataclass
class Phase4Result:
    # 既存フィールド（後方互換維持）
    gap_1_3: float = 0.0
    std_top5: float = 0.0
    judgment: str = ""
    recommended_bet: str = ""
    # v0.6追加
    gap_1_2: float = 0.0
    gap_2_4: float = 0.0
    is_dominant: bool = False
    top3_horses: list = field(default_factory=list)
    rival_range: list = field(default_factory=list)


# ──────────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────────

def _normalize_jockey_name(name: str) -> str:
    """全角英数字・記号を半角に正規化する（netkeiba表記ゆれ対応）"""
    import unicodedata
    return unicodedata.normalize("NFKC", name).strip()


def _lookup_jockey_bonus(jockey_name: str) -> float:
    """
    騎手ランクテーブルから補正値を返す。
    優先順: ① 完全一致 → ② NFKC正規化後に完全一致
            → ③ エイリアス解決 → ④ 前方4文字一致
    """
    if not jockey_name:
        return 0.0

    # ① 完全一致
    if jockey_name in JOCKEY_BONUS:
        return JOCKEY_BONUS[jockey_name]

    # ② 全角→半角正規化後に再マッチ
    normalized = _normalize_jockey_name(jockey_name)
    if normalized in JOCKEY_BONUS:
        return JOCKEY_BONUS[normalized]

    # ③ エイリアス解決（元の名前・正規化名の両方で試みる）
    for alias, canonical in JOCKEY_ALIAS.items():
        if alias in jockey_name or alias in normalized:
            return JOCKEY_BONUS.get(canonical, 0.0)

    # ④ 前方4文字一致（正規化後）
    for key, bonus in JOCKEY_BONUS.items():
        if len(key) >= 4 and key[:4] in normalized:
            return bonus

    return 0.0


def get_class_bonus(race_class: str) -> float:
    rc = _normalize_grade(race_class.strip())
    for key, val in CLASS_BONUS.items():
        if key in rc:
            return val
    return 0.0


# ── 格ボーナス関連ユーティリティ（v0.5追加）─────────────────────────

def _get_class_order(race_class: str) -> int:
    """クラス文字列を順序整数に変換（大きいほど格上）"""
    rc = _normalize_grade(race_class)
    for i, c in enumerate(CLASS_ORDER):
        if c in rc:
            return i
    return 5  # 不明は2勝クラス相当


def _normalize_grade(race_class: str) -> str:
    """
    ローマ数字グレード表記をアラビア数字に統一する
    例: 'スプリングS(GII)' → 'スプリングS(G2)'
    順序重要: GIII→G3 を GII→G2 より先に処理
    """
    rc = race_class
    rc = rc.replace("GIII", "G3").replace("GII", "G2").replace("GI", "G1")
    rc = rc.replace("JpnIII", "Jpn3").replace("JpnII", "Jpn2").replace("JpnI", "Jpn1")
    return rc


def _detect_grade_key(race_class: str) -> str:
    """
    レース名/クラス文字列から格戦キーを返す（なければ空文字）。
    GRADE_BONUS_TABLEのキー直接一致 → KNOWNレース名テーブル の順で検索。
    """
    import unicodedata as _ud
    rc = _normalize_grade(race_class)
    rc_norm = _ud.normalize("NFKC", rc)

    # ① GRADE_BONUS_TABLEキーが含まれるか（G1/G2/L等の直接表記）
    for key in GRADE_BONUS_TABLE:
        if key in rc:
            return key

    # ② KNOWNレース名テーブルで判定（get_class_baseと同じロジック）
    KNOWN_G1_NAMES = {
        "日本ダービー", "天皇賞", "有馬記念", "ジャパンC", "宝塚記念",
        "菊花賞", "オークス", "桜花賞", "皐月賞", "スプリンターズS",
        "マイルCS", "安田記念", "ヴィクトリアマイル", "エリザベス女王杯",
        "阪神JF", "朝日杯FS", "ホープフルS", "フェブラリーS", "高松宮記念",
        "チャンピオンズC", "秋華賞",
    }
    KNOWN_G2_NAMES = {
        "阪神スポーツ杯2歳S", "東スポ2歳S", "ラジオNIKKEI賞", "共同通信杯",
        "弥生賞", "毎日杯", "スプリングS", "フローラS",
        "青葉賞", "NHKマイルC", "目黒記念", "鳴尾記念", "金鯱賞",
        "小倉大賞典", "中山記念", "京都記念", "産経大阪杯", "阪神大賞典",
        "日経賞", "日経新春杯", "AJCC", "アメリカJCC",
        "セントライト記念", "神戸新聞杯", "オールカマー", "毎日王冠",
        "府中牝馬S", "富士S", "スワンS", "アルゼンチン共和国杯",
        "福島記念", "京阪杯", "CBC賞", "函館スプリントS", "キーンランドC",
        "ダービー卿CT", "マーメイドS", "ニュージーランドT",
    }
    KNOWN_G3_NAMES = {
        "ファンタジーS", "デイリー杯2歳S", "京王杯2歳S", "いちょうS",
        "野路菊S", "サウジアラビアRC", "新潟2歳S",
        "チューリップ賞", "フィリーズレビュー", "アネモネS",
        "クイーンC", "フラワーC", "エルフィンS",
        "若葉S", "ファルコンS", "ニュージーランドT",
        "葵S", "安土城S", "東京スポーツ杯",
    }
    KNOWN_OP_NAMES = {
        "ポインセチアS", "ポインセチアステークス",
    }
    KNOWN_L_NAMES = {
        "忘れな草賞",   # G2に昇格前はL
    }
    # ※ 忘れな草賞は2024年よりG2昇格。過去走データに応じて適切に判定される
    for name in KNOWN_G1_NAMES:
        if name in rc_norm:
            return "G1"
    for name in KNOWN_G2_NAMES:
        if name in rc_norm:
            return "G2"
    for name in KNOWN_G3_NAMES:
        if name in rc_norm:
            return "G3"
    for name in KNOWN_OP_NAMES:
        if name in rc_norm:
            return "OP"

    # ④ scraper.pyが「レース名(グレード)」形式で保存している場合のフォールバック
    # 例：「谷川岳ステークス(L)」→ L、「忘れな草賞(G2)」→ G2（KNOWNで先にヒット済み）
    import re as _re
    grade_suffix = _re.search(r'\((G[123]|Jpn[123]|OP|L)\)$', rc_norm)
    if grade_suffix:
        return grade_suffix.group(1)

    # ③ JpnグレードはKNOWN_Jpnで判定
    KNOWN_JPN1 = {"JBCクラシック", "帝王賞", "東京大賞典", "川崎記念", "フェブラリーS"}
    KNOWN_JPN2 = {"JBC2歳優駿", "JBCレディスクラシック", "ジャパンダートダービー"}
    KNOWN_JPN3 = {"JBC2歳優駿", "ポインセチアS", "兵庫CS", "兵庫ジュニアGP", "北海道2歳優駿"}
    for name in KNOWN_JPN1:
        if name in rc_norm:
            return "Jpn1"
    for name in KNOWN_JPN2:
        if name in rc_norm:
            return "Jpn2"
    for name in KNOWN_JPN3:
        if name in rc_norm:
            return "Jpn3"

    return ""


def calc_grade_bonus(
    past_races: list,
    age_limited: bool = False,
    classic_distance: bool = False,
) -> float:
    """
    全過去走から格戦ボーナスを集計して返す。

    age_limited=True（馬齢限定戦モード）の場合（v1.1改訂）：
      OP/L/G3/G2/Jpn2/Jpn3 -> base=1.0 で横並び評価
      G1/Jpn1               -> base=1.2（G1だけ微差で別格）
      着順スケールは通常モードと共通（1着1.0 / 2着0.8 / 3着0.6 / 4着0.4）
      理由：2〜3歳馬はOP以上で走った実績の有無が重要だが、
            G1好走は他グレードと比べ価値がわずかに高い。
            OP/G2の差はこの時点では小さいため同値とする。

    classic_distance=True（秋華賞・菊花賞等）の場合：
      age_limitedに加え「3勝クラス」もOPと同格扱い。
    """
    # 馬齢限定戦モード：グレード -> base値マッピング（v1.1）
    AGE_LIMITED_BASE = {
        "G1":      1.2, "Jpn1": 1.2,
        "G2":      1.0, "Jpn2": 1.0,
        "G3":      1.0, "Jpn3": 1.0,
        "OP":      1.0, "オープン": 1.0,
        "L":       1.0,
    }

    best: dict[str, int] = {}
    for pr in past_races:
        if pr.finish <= 0 or pr.finish > 4:
            continue
        gkey = _detect_grade_key(pr.race_class)
        if not gkey:
            continue
        if gkey not in best or pr.finish < best[gkey]:
            best[gkey] = pr.finish

    total = 0.0
    for gkey, rank in best.items():
        if age_limited:
            base = AGE_LIMITED_BASE.get(gkey, 1.0)
        else:
            base = GRADE_BONUS_TABLE.get(gkey, 0.0)
        scale = GRADE_RANK_SCALE.get(rank, 0.4)
        total += base * scale
    return round(total, 4)


def calc_momentum_bonus(
    past_races: list,
    current_class: str,
) -> float:
    """
    昇級勢い指数（v1.0更新）。
    前走クラス < 今回クラス の場合に補正を返す。
    戻り値は「スコアから引くポイント数」（正値=ボーナス、負値=ペナルティ）。
    格下げにはペナルティを付与しない（格上からの降格は力量上位のため）。
    """
    if not past_races:
        return 0.0
    prev = past_races[0]
    if prev.finish <= 0:
        return 0.0

    prev_order = _get_class_order(prev.race_class)
    curr_order = _get_class_order(current_class)

    if curr_order > prev_order:          # 昇級
        margin_sec = prev.margin         # 秒差
        if margin_sec <= 0.1:
            return  1.5   # 僅差昇級：ボーナス（スコアから引く）
        elif margin_sec <= 0.3:
            return  0.75
        else:
            return -0.75  # 大差負けで昇級：ペナルティ（スコアに加わる）
    # 格下げ・同クラスはペナルティなし
    return 0.0


def calc_recent_form_penalty(
    past_races: list,
    target_distance: int = 0,
    target_surface: str = "",
    age_limited: bool = False,   # v1.1追加：馬齢限定戦モード
) -> tuple[float, str]:
    """
    近3走の加重平均着順からペナルティを計算（v1.1更新）。
    weights: 前走×0.5、2走前×0.3、3走前×0.2

    距離・芝ダ重み評価：
    今回距離と一定距離超乖離、または今回と芝ダが異なる走は重みを半減。
    「別条件で不振→今回条件で好走」するケースの過剰ペナルティを防ぐ。
    距離・芝ダ両方該当なら25%（0.5×0.5）に減衰。

    age_limited=True（馬齢限定戦）の場合：
    距離半減閾値を400m超→600m超に拡大。
    3歳馬は2400m戦でも1800mが現実的な距離経験となるため。
    例：1800m→2400m（差600m）はフル重み、1600m→2400m（差800m）は半減。
    """
    valid = [pr for pr in past_races[:3] if pr.finish > 0]
    if not valid:
        return (0.0, "")

    ws = WEIGHT_RECENT[: len(valid)]

    # 距離・芝ダ重み：今回と条件が異なる走は着順の影響を半減
    # ・今回距離と閾値超乖離 → 半減（通常:400m超、馬齢限定戦:600m超）
    # ・今回芝ダと異なる → 半減
    # ・両方該当 → 25%（0.5×0.5）
    dist_threshold = 600 if age_limited else 400
    if target_distance > 0 or target_surface:
        dist_weights = []
        for pr in valid:
            w = 1.0
            if target_distance > 0 and pr.distance > 0:
                if abs(pr.distance - target_distance) > dist_threshold:
                    w *= 0.5
            if target_surface and pr.surface and pr.surface != target_surface:
                w *= 0.5
            dist_weights.append(w)
        effective_ws = [w * dw for w, dw in zip(ws, dist_weights)]
    else:
        effective_ws = ws

    total_w = sum(effective_ws)
    if total_w == 0:
        return (0.0, "")

    avg_finish = sum(pr.finish * w for pr, w in zip(valid, effective_ws)) / total_w

    penalty = 0.0
    for threshold, pen in RECENT_FORM_PENALTY:
        if avg_finish >= threshold:
            penalty = pen
            break

    if penalty == 0.0:
        return (0.0, "")

    label = f"近走不振(avg{avg_finish:.1f}着):+{penalty:.1f}"
    return (round(penalty, 3), label)


def calc_distance_aptitude_bonus(
    past_races: list,
    target_distance: int,
    was_fallback: bool = False,   # 引数互換維持（内部では未使用）
    target_surface: str = "",     # 芝ダ違いペナルティ用（v1.0追加）
) -> tuple[float, str]:
    """
    距離適性ボーナスを計算して返す（v1.0改訂）。
    ペナルティなし・ボーナスのみ設計。

    ① ±400m以内の好走（3着以内・着差0.5秒以内）→ フルボーナス
    ② 400〜800m以内の好走 → 半額ボーナス
    ③ 800m超乖離 → ボーナスなし（距離評価対象外）
    ④ スタミナ証明（今回距離+400m超の完走実績）
    ⑤ 芝ダ違いペナルティ（今回と異なる芝ダの走しかない場合）
    """
    if target_distance <= 0 or not past_races:
        return (0.0, "")

    # ── ① ② 距離帯好走実績
    good_finish_bonus = 0.0
    best_finish_label = ""

    # ±800m以内の好走走を収集
    near_good = [
        pr for pr in past_races
        if abs(pr.distance - target_distance) < 800   # 800m未満（800mは対象外）
        and 1 <= pr.finish <= 3
    ]
    if near_good:
        best_pr = min(near_good, key=lambda pr: (pr.finish, abs(pr.distance - target_distance)))
        base = DIST_GOOD_FINISH_BONUS.get(best_pr.finish, 0.0)

        dist_diff = abs(best_pr.distance - target_distance)
        # 距離近さ係数
        if dist_diff <= 400:
            closeness = 1.0   # ①フルボーナス
        else:
            closeness = 0.5   # ②半額（400〜800m）

        # 着差フィルター
        if best_pr.winner_time_sec > 0 and best_pr.time_sec > best_pr.winner_time_sec:
            gap_from_winner = best_pr.time_sec - best_pr.winner_time_sec
        elif best_pr.finish == 1:
            gap_from_winner = 0.0
        else:
            gap_from_winner = best_pr.margin

        margin_scale = 0.0
        for threshold, scale in DIST_BONUS_MARGIN_THRESHOLDS:
            if gap_from_winner <= threshold:
                margin_scale = scale
                break

        good_finish_bonus = round(base * closeness * margin_scale, 3)
        if good_finish_bonus > 0:
            best_finish_label = f"距離好走{best_pr.finish}着:{good_finish_bonus:+.3f}"
        elif margin_scale == 0.0 and base > 0:
            best_finish_label = f"距離{best_pr.finish}着(着差大無効)"

    # ── ④ スタミナ証明（今回距離+400m超の完走実績）
    stamina_bonus = 0.0
    stamina_label = ""
    stamina_races = [
        pr for pr in past_races
        if pr.distance > target_distance + 400 and pr.finish > 0
    ]
    if stamina_races:
        stamina_bonus = DIST_STAMINA_BONUS
        max_dist = max(pr.distance for pr in stamina_races)
        stamina_label = f"スタミナ証明({max_dist}m):{stamina_bonus:+.2f}"

    # ── ⑤ 芝ダ転向ペナルティ/ボーナス（v1.1改訂）
    # 初挑戦（同じ芝ダの過去走ゼロ）: -1.5pt（ペナルティ）
    # 同じ芝ダで好走（3着以内）あり : +1.2pt（ボーナス）
    # 同じ芝ダで過去走あり・好走なし: 0pt
    surface_penalty = 0.0
    surface_label = ""
    if target_surface and past_races:
        same_surf      = [pr for pr in past_races if pr.surface == target_surface]
        same_surf_good = [pr for pr in same_surf  if 1 <= pr.finish <= 3]
        other_surf = "ダ" if target_surface == "芝" else "芝"
        if not same_surf:
            # 初挑戦
            surface_penalty = -1.5
            surface_label = f"{other_surf}→{target_surface}初挑戦:-1.5"
        elif same_surf_good:
            # 同じ芝ダで好走実績あり
            surface_penalty = 1.2
            surface_label = f"{target_surface}好走実績:+1.2"

    # ── ⑥ 距離ミスマッチペナルティ（v1.1追加）
    # 今回距離との乖離が大きく、かつ近距離帯の好走実績がない場合にペナルティ
    # ただし「その距離帯での成績が悪い（avg≥6.0）」場合は距離変更歓迎とみなして免除
    dist_mismatch_penalty = 0.0
    dist_mismatch_label = ""
    if target_distance > 0 and past_races:
        # 今回±400m以内の好走実績（3着以内）
        near_400 = [pr for pr in past_races
                    if abs(pr.distance - target_distance) <= 400 and 1 <= pr.finish <= 3]
        # 今回±800m以内の好走実績
        near_800 = [pr for pr in past_races
                    if abs(pr.distance - target_distance) < 800 and 1 <= pr.finish <= 3]
        # 最も今回距離に近い過去走の距離差
        if past_races:
            min_diff = min(abs(pr.distance - target_distance) for pr in past_races if pr.distance > 0)
        else:
            min_diff = 9999

        # ── 距離変更歓迎チェック
        # 今回距離より400m以上離れた距離帯での平均着順（苦手距離の判定）
        longer_dist_races  = [pr for pr in past_races
                              if pr.distance - target_distance >= 400 and pr.finish > 0]
        shorter_dist_races = [pr for pr in past_races
                              if target_distance - pr.distance >= 400 and pr.finish > 0]
        avg_longer  = (sum(pr.finish for pr in longer_dist_races)  / len(longer_dist_races)
                       if longer_dist_races  else 0.0)
        avg_shorter = (sum(pr.finish for pr in shorter_dist_races) / len(shorter_dist_races)
                       if shorter_dist_races else 0.0)

        # 距離変更歓迎条件：
        #   ±400m以内の好走実績なし（距離転換）
        #   かつ 従来の距離帯（400m以上離れた方）でavg≥6.0（苦手だった）
        #   かつ 今回距離が最近走距離より短縮 or 延長になっている
        is_shortening = (longer_dist_races  and not shorter_dist_races and avg_longer  >= 6.0)
        is_extending  = (shorter_dist_races and not longer_dist_races  and avg_shorter >= 6.0)
        avg_prev = avg_longer if is_shortening else avg_shorter if is_extending else 0.0
        is_welcome = not near_400 and (is_shortening or is_extending)

        if is_welcome:
            # 距離変更歓迎ボーナス：苦手距離から適性距離への転換
            dist_mismatch_penalty = 0.5   # スコアを下げる（有利方向）
            dist_mismatch_label = f"距離変更歓迎(avg{avg_prev:.1f}着):-0.5"

        elif not near_400 and min_diff >= 800:
            # ±400m以内好走実績なし、かつ800m超乖離（歓迎条件非該当）
            # 距離変更歓迎チェック用：±400m超の全距離帯での平均着順
            prev_dist_races = [pr for pr in past_races
                               if abs(pr.distance - target_distance) > 400 and pr.finish > 0]
            avg_finish_prev = (sum(pr.finish for pr in prev_dist_races) / len(prev_dist_races)
                               if prev_dist_races else 0.0)
            if avg_finish_prev >= 6.0:
                # 距離変更歓迎（広義）：ペナルティ免除＋軽微ボーナス
                dist_mismatch_penalty = 0.5
                dist_mismatch_label = f"距離変更歓迎(avg{avg_finish_prev:.1f}着):-0.5"
            elif not near_800 and min_diff >= 1200:
                # 重度ミスマッチ
                dist_mismatch_penalty = -3.0
                dist_mismatch_label = f"距離ミスマッチ({min_diff}m差):-3.0"
            else:
                # 中程度ミスマッチ
                dist_mismatch_penalty = -1.5
                dist_mismatch_label = f"距離ミスマッチ({min_diff}m差):-1.5"

    total_bonus = good_finish_bonus + stamina_bonus + surface_penalty + dist_mismatch_penalty

    if total_bonus == 0.0 and not best_finish_label and not stamina_label and not surface_label and not dist_mismatch_label:
        return (0.0, "")

    parts = [s for s in [best_finish_label, stamina_label, surface_label, dist_mismatch_label] if s]
    note = " ".join(parts)
    return (round(total_bonus, 3), note)


def filter_by_distance(past_races: list, target_distance: int, margin: int = DISTANCE_FILTER_MARGIN) -> tuple[list, int]:
    """
    今回レース距離に近い過去走だけを返す
    戻り値: (フィルター後のリスト, 除外件数)
    """
    if target_distance <= 0:
        return past_races, 0

    filtered = [pr for pr in past_races if abs(pr.distance - target_distance) <= margin]
    excluded = len(past_races) - len(filtered)
    return filtered, excluded


# ──────────────────────────────────────────────
# Phase1：能力コアスコア
# ──────────────────────────────────────────────

def get_class_base(race_class: str) -> float:
    """クラス基準値を返す（v1.0）"""
    rc = _normalize_grade(race_class)
    # 完全一致
    if rc in CLASS_BASE:
        return CLASS_BASE[rc]
    # 部分一致（レース名にクラス文字列が含まれる場合）
    for key, val in CLASS_BASE.items():
        if key in rc:
            return val
    # 著名レース名からグレードを推定
    import unicodedata as _ud
    rc_norm = _ud.normalize("NFKC", rc)
    KNOWN_G1 = {
        "日本ダービー", "天皇賞", "有馬記念", "ジャパンC", "宝塚記念",
        "菊花賞", "オークス", "桜花賞", "皐月賞", "スプリンターズS",
        "マイルCS", "安田記念", "ヴィクトリアマイル", "エリザベス女王杯",
        "阪神JF", "朝日杯FS", "ホープフルS", "フェブラリーS", "高松宮記念",
        "チャンピオンズC", "秋華賞",
    }
    KNOWN_G2 = {
        "阪神スポーツ杯2歳S", "東スポ2歳S", "ラジオNIKKEI賞", "共同通信杯",
        "弥生賞", "毎日杯", "スプリングS", "フローラS", "忘れな草賞",
        "青葉賞", "NHKマイルC", "目黒記念", "鳴尾記念", "金鯱賞",
        "小倉大賞典", "中山記念", "京都記念", "産経大阪杯", "阪神大賞典",
        "日経賞", "日経新春杯", "AJCC", "アメリカJCC",
        "セントライト記念", "神戸新聞杯", "オールカマー", "毎日王冠",
        "府中牝馬S", "富士S", "スワンS", "アルゼンチン共和国杯",
        "福島記念", "京阪杯", "CBC賞", "函館スプリントS", "キーンランドC",
        "ダービー卿CT", "マーメイドS", "ニュージーランドT",
    }
    KNOWN_G3 = {
        "ファンタジーS", "デイリー杯2歳S", "京王杯2歳S", "いちょうS",
        "野路菊S", "サウジアラビアRC", "新潟2歳S",
        "チューリップ賞", "フィリーズレビュー", "アネモネS",
        "クイーンC", "フラワーC", "エルフィンS",
        "若葉S", "ファルコンS", "ニュージーランドT",
        "葵S", "安土城S", "東京スポーツ杯",
    }
    for name in KNOWN_G1:
        if name in rc_norm:
            return CLASS_BASE["G1"]
    for name in KNOWN_G2:
        if name in rc_norm:
            return CLASS_BASE["G2"]
    for name in KNOWN_G3:
        if name in rc_norm:
            return CLASS_BASE["G3"]
    # クラス名から推定
    if "新馬" in rc_norm:
        return CLASS_BASE["新馬"]
    if "未勝利" in rc_norm:
        return CLASS_BASE["未勝利"]
    if "1勝" in rc_norm or "500万" in rc_norm:
        return CLASS_BASE["1勝クラス"]
    if "2勝" in rc_norm or "1000万" in rc_norm:
        return CLASS_BASE["2勝クラス"]
    if "3勝" in rc_norm or "1600万" in rc_norm:
        return CLASS_BASE["3勝クラス"]
    if "オープン" in rc_norm:
        return CLASS_BASE["OP"]
    return CLASS_BASE_DEFAULT


# 大差負けペナルティ（6着以下・着差ベース）
# margin（秒差）が大きいほどペナルティ加算
LARGE_MARGIN_PENALTY = [
    (3.0, 3.0),   # 3秒超 → +3.0pt
    (2.0, 2.0),   # 2秒超 → +2.0pt
    (1.0, 1.0),   # 1秒超 → +1.0pt
]

# 相対着順ペナルティ（着順÷頭数）
# 頭数が分かる場合のみ適用
RELATIVE_FINISH_PENALTY = [
    (0.90, 2.0),  # 下位10%（例：9頭立て9着、18頭立て17着） → +2.0pt
    (0.75, 1.0),  # 下位25% → +1.0pt
]


def calc_race_point(
    finish: int,
    margin: float,         # 1着との差（秒）。1着なら0
    race_class: str,
    weight_carried: float = 55.0,
    field_size: int = 0,   # v1.1追加：出走頭数（相対着順ペナルティ用）
) -> Optional[float]:
    """
    1走分のポイントを計算する（v1.1 着順ベース）。
    ポイント = クラス基準値 - 着順ボーナス - 着差ボーナス + 大差負けペナルティ
             + 相対着順ペナルティ - 斤量補正
    小さいほど高評価。

    v1.1追加：
    - 大差負けペナルティ：6着以下かつmargin>1秒で加算
    - 相対着順ペナルティ：頭数が分かる場合、着順÷頭数が大きいほど加算
    """
    if finish <= 0:
        return None

    base        = get_class_base(race_class)
    fin_bonus   = FINISH_BONUS.get(finish, FINISH_BONUS_DEFAULT)

    # 着差ボーナス（好走時のみ）
    margin_bonus = 0.0
    for threshold, bonus in MARGIN_BONUS_THRESHOLDS:
        if margin <= threshold:
            margin_bonus = bonus
            break

    # 大差負けペナルティ（6着以下かつ着差が大きい場合）
    large_margin_pen = 0.0
    if finish >= 6 and margin > 1.0:
        for threshold, pen in LARGE_MARGIN_PENALTY:
            if margin > threshold:
                large_margin_pen = pen
                break

    # 相対着順ペナルティ（頭数が分かる場合のみ）
    # 着差データがない海外レース等で最下位付近を正しく評価
    relative_pen = 0.0
    if finish >= 6 and field_size >= 6:
        relative_ratio = finish / field_size
        for threshold, pen in RELATIVE_FINISH_PENALTY:
            if relative_ratio >= threshold:
                relative_pen = pen
                break

    # 斤量補正（55kg基準、1kgあたり±0.5ポイント）
    weight_correction = (BASE_WEIGHT - weight_carried) * 0.5

    point = base - fin_bonus - margin_bonus + large_margin_pen + relative_pen + weight_correction
    return round(point, 3)


def calc_phase1(
    horse_name: str,
    horse_number: int,
    past_races: list,
    target_distance: int = 0,
    target_surface: str = "",
    current_class: str = "",
    use_grade_bonus: bool = True,
    use_momentum: bool = True,
    use_dist_aptitude: bool = True,
    age_limited: bool = False,        # 馬齢限定戦モード（v1.0追加）
    classic_distance: bool = False,   # 秋華賞・菊花賞等（v1.0追加）
    race_date: str = "",              # 今回レース日（v1.1追加：出走間隔計算用）
) -> Phase1Result:
    """
    Phase1スコアを計算する（v1.1 着順・着差・クラスベース）
    target_distance > 0 の場合、距離フィルターを適用
    target_surface が指定された場合、芝ダフィルターを適用
    race_date が指定された場合、前走からの出走間隔補正を適用
    """
    result = Phase1Result(horse_name=horse_name, horse_number=horse_number)

    past_races_all = list(past_races)

    # ── 芝ダフィルター（v1.0維持）
    if target_surface:
        races_surf = [pr for pr in past_races if pr.surface == target_surface]
        if races_surf:
            past_races = races_surf

    # ── 距離フィルター廃止（v1.0）
    # 距離適性はボーナス/ペナルティで対応するため、全走を常に使用する。
    # 1600m専門馬が2000mに出走する場合など、フィルターで全走除外される問題を解消。
    dist_fallback = False  # 距離適性ボーナス関数との互換性のためFalseで維持

    # ── 地方走除外（v0.9）
    central_races = [pr for pr in past_races if not pr.is_local]
    local_excluded = len(past_races) - len(central_races)
    if central_races:
        if local_excluded > 0:
            past_races = central_races
            result.note = (result.note + f" [地方走除外{local_excluded}走]").strip()

    # ── 障害転向処理（v1.1追加）
    # 直近の連続した障害走のみを使い、その前の平地走は除外する
    # 障害走はタイム比較が無意味なため gap=0 で着順のみ評価
    def _is_hurdle(rc: str) -> bool:
        rc_n = rc.replace("　", " ").replace("　", " ")
        return "障" in rc_n or "障害" in rc_n or "hurdle" in rc_n.lower() or "steeplechase" in rc_n.lower()

    if past_races and any(_is_hurdle(pr.race_class) for pr in past_races):
        # 先頭から連続する障害走を取り出し、最初の平地走以降を除外
        hurdle_streak = []
        for pr in past_races:
            if _is_hurdle(pr.race_class):
                hurdle_streak.append(pr)
            else:
                break  # 平地走が出たら打ち切り
        if hurdle_streak:
            past_races = hurdle_streak
            result.note = (result.note + f" [障害走のみ使用({len(hurdle_streak)}走)]").strip()
            # 障害走はtaimetとgapを0にして着順のみで評価させる
            for pr in past_races:
                pr.time_sec = 0.0
                pr.winner_time_sec = 0.0
                pr.margin = 0.0

    # ── 格上挑戦除外（v1.1追加）
    # 今回クラスより格上のレースに出走して大敗した走は参考外として除外
    # 「今回クラスのCLASS_BASE + 5.0以上の格上レース かつ 着順6着以下」
    if current_class and past_races:
        current_base = get_class_base(current_class)
        non_overclass = []
        overclass_excluded = 0
        for pr in past_races:
            pr_base = get_class_base(pr.race_class)
            # 格上 = pr_baseがcurrent_baseより4.0以上小さい（より強いクラス）
            # 例: 未勝利(95)に対して1勝クラス(92)は格上 → 95-92=3.0 < 4.0 なので除外しない
            #     未勝利(95)に対してOP(80)は格上 → 95-80=15.0 ≥ 4.0 → 6着以下なら除外
            if (current_base - pr_base) >= 2.0 and pr.finish >= 6:
                # 格上レースで大敗 → 除外
                overclass_excluded += 1
            else:
                non_overclass.append(pr)
        if overclass_excluded > 0 and non_overclass:
            past_races = non_overclass
            result.note = (result.note + f" [格上挑戦除外{overclass_excluded}走]").strip()

    # ── 各走のポイント計算（最大3走）
    targets = past_races[:3]
    race_points = []
    finish_list = []
    penalty_notes = []  # 大差負け・相対着順ペナルティのnote用
    for pr in targets:
        # 1着との差：winner_time_secが取れていれば使用、なければmarginで代替
        if pr.finish == 1:
            gap = 0.0
        elif pr.winner_time_sec > 0 and pr.time_sec > pr.winner_time_sec:
            gap = round(pr.time_sec - pr.winner_time_sec, 3)
        else:
            gap = pr.margin  # 直前馬差（秒）でフォールバック

        fs = getattr(pr, "field_size", 0)
        pt = calc_race_point(pr.finish, gap, pr.race_class, pr.weight_carried, fs)
        if pt is not None:
            race_points.append(pt)
            finish_list.append(pr.finish)

            # 大差負けペナルティ検知（noteへ追記用）
            if pr.finish >= 6 and gap > 1.0:
                for threshold, pen in LARGE_MARGIN_PENALTY:
                    if gap > threshold:
                        penalty_notes.append(f"大差負け({gap:.1f}秒):+{pen:.1f}")
                        break

            # 相対着順ペナルティ検知（noteへ追記用）
            if pr.finish >= 6 and fs >= 6:
                ratio = pr.finish / fs
                for threshold, pen in RELATIVE_FINISH_PENALTY:
                    if ratio >= threshold:
                        penalty_notes.append(f"最下位圏({pr.finish}/{fs}頭):+{pen:.1f}")
                        break

    result.corrected_times = race_points   # 互換性のためcorrected_timesに格納
    result.valid_runs = len(race_points)

    # 大差負け・相対着順ペナルティをnoteに追記
    if penalty_notes:
        result.note = (result.note + " [" + "/".join(penalty_notes) + "]").strip()

    if result.valid_runs == 0:
        result.note = (result.note + " 有効な走行データなし").strip()
        result.phase1_score = 9999.0
        return result

    weights = WEIGHT_RECENT[: result.valid_runs]
    total_w = sum(weights)
    ability_avg = sum(p * w for p, w in zip(race_points, weights)) / total_w
    result.ability_avg  = round(ability_avg, 3)
    result.best_time    = round(min(race_points), 3)   # ベストポイント（最小値）
    result.phase1_score = result.ability_avg

    # 走数が少ない場合はnoteに表示のみ（ペナルティなし・v1.0廃止）
    if result.valid_runs < 3 and not result.note:
        result.note = f"有効走数{result.valid_runs}走"

    # ── 近走不振ペナルティ（v1.1：age_limited対応・二重カウント防止）
    # past_races（障害転向除外・格上挑戦除外済み）を使用
    form_pen, form_label = calc_recent_form_penalty(
        past_races, target_distance, target_surface,
        age_limited=age_limited,
    )
    if form_pen > 0:
        # 最下位圏・大差負けペナルティ合計を計算（二重カウント防止）
        # 「最下位圏/大差負け」+ 「近走不振」の合計が3.0ptを超えないようにキャップ
        PENALTY_CAP = 3.0
        heavy_pen_total = 0.0
        for note_item in penalty_notes:
            # "最下位圏(13/15頭):+1.0" や "大差負け(1.5秒):+2.0" から値を抽出
            import re as _re_pen
            m = _re_pen.search(r":(\+[\d.]+)", note_item)
            if m:
                heavy_pen_total += float(m.group(1))
        # キャップ適用：合計がPENALTY_CAPを超えない範囲で近走不振ペナルティを加算
        form_pen_capped = max(0.0, min(form_pen, PENALTY_CAP - heavy_pen_total))
        if form_pen_capped > 0:
            result.phase1_score = round(result.phase1_score + form_pen_capped, 3)
            result.ability_avg  = round(result.ability_avg  + form_pen_capped, 3)
            label_suffix = f"(cap:{form_pen_capped:.1f})" if form_pen_capped < form_pen else ""
            result.note = (result.note + f" [{form_label}{label_suffix}]").strip()
        elif form_pen > 0:
            # キャップにより0になった場合もnoteには記録
            result.note = (result.note + f" [{form_label}→cap済]").strip()

    # ── 出走間隔補正（v1.1追加）
    # 今回レース日と前走日付の差から休養期間を算出
    # 70〜112日（10〜16週）: 適度な休養 → -0.5pt（ボーナス）
    # 112日超（16週超）    : 長期休養   → +2.0pt（ペナルティ）
    if race_date and past_races_all:
        from datetime import datetime as _dt
        try:
            # race_dateは "2026年6月7日" 形式、past_races.dateは "2026/06/14" 形式
            import re as _re
            _rd = race_date.strip()
            _m = _re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", _rd)
            if _m:
                _today = _dt(int(_m.group(1)), int(_m.group(2)), int(_m.group(3)))
            else:
                _today = _dt.strptime(_rd, "%Y/%m/%d")
            _last_str = past_races_all[0].date
            if _last_str:
                _last = _dt.strptime(_last_str.strip(), "%Y/%m/%d")
                _days = (_today - _last).days
                if 70 <= _days <= 112:
                    result.phase1_score = round(result.phase1_score - 0.5, 3)
                    result.ability_avg  = round(result.ability_avg  - 0.5, 3)
                    result.note = (result.note + f" [適度な休養({_days}日):-0.5]").strip()
                elif _days > 112:
                    result.phase1_score = round(result.phase1_score + 2.0, 3)
                    result.ability_avg  = round(result.ability_avg  + 2.0, 3)
                    result.note = (result.note + f" [長期休養({_days}日):+2.0]").strip()
        except Exception:
            pass  # 日付パース失敗時は無視

    # ── 格ボーナス（v1.0復活、全過去走対象）
    if use_grade_bonus:
        grade_b = calc_grade_bonus(past_races_all, age_limited=age_limited, classic_distance=classic_distance)
        if grade_b > 0:
            result.phase1_score = round(result.phase1_score - grade_b, 3)
            result.ability_avg  = round(result.ability_avg  - grade_b, 3)
            result.note = (result.note + f" [格B:-{grade_b:.1f}]").strip()

    # ── 昇級勢い
    if use_momentum and current_class:
        momentum_pt = calc_momentum_bonus(past_races_all, current_class)
        result.phase1_score = round(result.phase1_score - momentum_pt, 3)
        result.ability_avg  = round(result.ability_avg  - momentum_pt, 3)
        if momentum_pt != 0:
            result.note = (result.note + f" [昇降:{momentum_pt:+.2f}pt]").strip()

    # ── 距離適性ボーナス
    if use_dist_aptitude and target_distance > 0:
        dist_bonus, dist_label = calc_distance_aptitude_bonus(
            past_races_all, target_distance,
            target_surface=target_surface,
        )
        result.phase1_score = round(result.phase1_score - dist_bonus, 3)
        result.ability_avg  = round(result.ability_avg  - dist_bonus, 3)
        if dist_label:
            result.note = (result.note + f" [{dist_label}]").strip()

    return result



# ──────────────────────────────────────────────
# Phase2：安定性 vs 爆発力
# ──────────────────────────────────────────────

def calc_phase2(phase1: Phase1Result) -> Phase2Result:
    """
    Phase2スコア（v1.0）
    有効走数3走以上の場合のみ爆発力・不安定補正を適用。
    ポイントスケールに合わせてstd_devはポイントの標準偏差を使用。
    """
    r = Phase2Result(
        horse_name=phase1.horse_name,
        horse_number=phase1.horse_number,
        phase1_score=phase1.phase1_score,
        best_time=phase1.best_time,
        valid_runs=phase1.valid_runs,
        note=phase1.note,
    )

    if phase1.phase1_score >= 9000 or phase1.valid_runs == 0:
        r.phase2_score = phase1.phase1_score
        return r

    # 有効走数3走未満はPhase2補正なし（v0.9）
    if phase1.valid_runs < 3:
        r.phase2_score = phase1.phase1_score
        r.std_dev = 0.0
        return r

    r.std_dev = round(statistics.stdev(phase1.corrected_times), 3)

    best_gap              = phase1.ability_avg - phase1.best_time
    r.best_bonus          = round(best_gap * BEST_BONUS_FACTOR, 3)
    r.instability_penalty = round(r.std_dev * INSTABILITY_FACTOR, 3)
    r.phase2_score = round(
        phase1.phase1_score - r.best_bonus + r.instability_penalty, 3
    )
    return r


def calc_phase2_all(phase1_results: list) -> list:
    return [calc_phase2(r) for r in phase1_results]


def build_ranking_phase2(phase2_results: list) -> list:
    return sorted(phase2_results, key=lambda x: x.phase2_score)


# ──────────────────────────────────────────────
# Phase3：競馬場・騎手適性（過去走から動的集計）
# ──────────────────────────────────────────────

def calc_venue_jockey_stats(
    horse_name: str,
    horse_number: int,
    past_races: list,
    target_venue: str,
    current_jockey: str,
    target_track_cond: str = "",   # 今回馬場状態（良/稍重/重/不良）
) -> VenueJockeyStats:
    """
    過去走から競馬場・馬場・回り・騎手適性を集計する（v1.0全面改訂）
    優先度：馬場適性 > 競馬場適性 > 回り適性
    全てスコアから「引く」方向（正値=ボーナス=有利）
    """
    stats = VenueJockeyStats(horse_name=horse_name, horse_number=horse_number)

    # ── ① 馬場状態適性ボーナス（道悪のみ評価、良馬場は対象外）
    if target_track_cond and past_races:
        is_bad_track = target_track_cond in TRACK_BAD

        if is_bad_track:
            # 今回が道悪 → 道悪好走実績でボーナス、道悪凡走実績でペナルティ
            bad_runs = [pr for pr in past_races if pr.condition in TRACK_BAD and pr.finish > 0]
            if bad_runs:
                good_in_bad = [pr for pr in bad_runs if pr.finish <= 3 and pr.margin <= 0.5]
                bad_in_bad  = [pr for pr in bad_runs if pr.finish >= 6]
                if good_in_bad and not bad_in_bad:
                    # 道悪好走実績のみ → ボーナス
                    stats.track_bonus = CONDITION_BONUS_TABLE["track_bad"]   # +1.2pt
                elif bad_in_bad and not good_in_bad:
                    # 道悪凡走実績のみ → ペナルティ
                    stats.track_bonus = -CONDITION_BONUS_TABLE["track_bad"]  # -1.2pt
                else:
                    # 好走・凡走の両方あり → 未知数（0pt）
                    stats.track_bonus = 0.0
            # 道悪実績なし → ボーナスもペナルティもなし（未知数）

    # ── ② 競馬場適性ボーナス（ポイントスケール）
    # 最低2走以上の実績がある場合のみ付与（1走だけでは信頼性が低い）
    venue_races = [pr for pr in past_races if pr.venue == target_venue and pr.finish > 0]
    if len(venue_races) >= 2:
        stats.venue_runs = len(venue_races)
        stats.venue_avg_finish = round(
            sum(pr.finish for pr in venue_races) / len(venue_races), 2
        )
        if stats.venue_avg_finish <= 3.0:
            stats.venue_bonus = CONDITION_BONUS_TABLE["venue_avg3"]
        elif stats.venue_avg_finish <= 5.0:
            stats.venue_bonus = CONDITION_BONUS_TABLE["venue_avg5"]
        else:
            stats.venue_bonus = 0.0
    else:
        stats.venue_bonus = 0.0

    # ── ③ 回り適性ボーナス
    target_turn = VENUE_TURN_DIRECTION.get(target_venue, "")
    if target_turn:
        # 同回りの競馬場を特定
        same_turn_venues = {v for v, t in VENUE_TURN_DIRECTION.items() if t == target_turn}
        turn_good_runs = [
            pr for pr in past_races
            if pr.venue in same_turn_venues
            and 1 <= pr.finish <= 3
            and pr.margin <= 0.5
        ]
        if turn_good_runs:
            stats.turn_bonus = CONDITION_BONUS_TABLE["turn"]

    # ── ④ 騎手ボーナス（v1.1簡素化）
    # 今回のレースで乗る騎手のランクテーブルのみで判定。
    # 過去走との実績マッチングは廃止。
    # 段階テーブル：
    #   +1.5pt: ルメール・モレイラ・ムーア・川田 等（_lookup_jockey_bonus <= -0.040）
    #   +0.8pt: 坂井・戸崎・横山武・デムーロ兄弟・レーン等（<= -0.015）
    #   +0.4pt: 武豊のみ（<= -0.005）
    #   0pt   : それ以外
    raw = _lookup_jockey_bonus(current_jockey)
    if raw <= -0.040:
        stats.jockey_bonus = 1.5
    elif raw <= -0.015:
        stats.jockey_bonus = 0.8
    elif raw <= -0.005:
        stats.jockey_bonus = 0.4
    else:
        stats.jockey_bonus = 0.0

    return stats


def apply_venue_jockey_bonus(
    phase2_results: list,
    horses: list,
    target_venue: str,
    all_past_races: dict,
    target_track_cond: str = "",   # v1.0追加
) -> list:
    """
    Phase2スコアに競馬場・馬場・回り・騎手適性補正を加算する（v1.0全面改訂）
    """
    adjusted = []
    horse_map = {h.number: h for h in horses}

    for r in phase2_results:
        new_r = copy.copy(r)
        h = horse_map.get(r.horse_number)
        past = all_past_races.get(r.horse_number, [])

        if h and target_venue:
            stats = calc_venue_jockey_stats(
                r.horse_name, r.horse_number, past,
                target_venue, h.jockey,
                target_track_cond=target_track_cond,
            )
            # 全ボーナスをスコアから引く（正値=有利）
            total_bonus = stats.track_bonus + stats.venue_bonus + stats.turn_bonus + stats.jockey_bonus
            new_r.phase2_score = round(r.phase2_score - total_bonus, 3)

            # note表示
            parts = []
            if stats.track_bonus != 0:
                parts.append(f"馬場{stats.track_bonus:+.1f}")
            if stats.venue_bonus != 0:
                parts.append(f"会場{stats.venue_bonus:+.1f}")
            if stats.turn_bonus != 0:
                parts.append(f"回り{stats.turn_bonus:+.1f}")
            if stats.jockey_bonus != 0:
                parts.append(f"騎手{stats.jockey_bonus:+.2f}")
            if parts:
                new_r.note = (r.note + f" [{'/'.join(parts)}]").strip()

        adjusted.append(new_r)

    return sorted(adjusted, key=lambda x: x.phase2_score)


# ──────────────────────────────────────────────
# Phase4：レース解像度指数
# ──────────────────────────────────────────────

def calc_phase4(results: list) -> Phase4Result:
    """
    Phase4：レース解像度指数（v0.6）
    判定パターン:
      1強混戦   : 1位が飛び抜け、2位以下は団子   → 軸固定＋相手BOX
      1強準混戦 : 1位が飛び抜け、2位以下はやや分散 → 軸固定＋相手3〜4頭流し
      1強明確   : 1位が飛び抜け、2位以下も分散    → 軸固定＋相手2〜3頭流し
      明確型    : 1強なし、上位3頭が分離          → 軸1頭流し
      準混戦    : 上位3頭が接近                  → 軸1頭流し（馬連）
      混戦      : 上位5頭が接近                  → BOX
      超混戦    : 全体が団子                     → 見送り候補
    """
    result = Phase4Result()

    def get_score(r):
        return r.phase2_score if hasattr(r, "phase2_score") else r.phase1_score

    valid = sorted(
        [r for r in results if get_score(r) < 9000],
        key=get_score,
    )

    if len(valid) < 3:
        result.judgment = "データ不足"
        result.recommended_bet = "判定不可"
        return result

    scores = [get_score(r) for r in valid]
    numbers = [r.horse_number for r in valid]

    # ── 基本指標
    result.gap_1_3 = round(scores[2] - scores[0], 3)
    result.gap_1_2 = round(scores[1] - scores[0], 3)
    result.gap_2_4 = round(scores[3] - scores[1], 3) if len(scores) >= 4 else result.gap_1_3

    top5 = scores[:5]
    result.std_top5 = round(statistics.stdev(top5) if len(top5) >= 2 else 0.0, 3)

    # 2〜5位の密集度（相手絞りやすさの指標）
    s2_5 = scores[1:5]
    std_2_5 = statistics.stdev(s2_5) if len(s2_5) >= 2 else 0.0
    max_diff_2_5 = (max(s2_5) - min(s2_5)) if s2_5 else 0.0

    # ── 1強判定（2位に対して0.3秒以上離れている）
    result.is_dominant = result.gap_1_2 >= 0.30

    # ── 判定ロジック
    if result.is_dominant:
        result.top3_horses = [numbers[0]]   # 軸は1位のみ
        # 相手の絞りやすさ: 2〜5位の最大差で判定
        if max_diff_2_5 >= 1.0:
            result.judgment        = "1強明確"
            result.recommended_bet = "軸固定＋相手2〜3頭流し（馬連・ワイド）"
            result.rival_range     = numbers[1:4]
        elif max_diff_2_5 >= 0.30:
            result.judgment        = "1強準混戦"
            result.recommended_bet = "軸固定＋相手3〜4頭流し（馬連）"
            result.rival_range     = numbers[1:5]
        else:
            result.judgment        = "1強混戦"
            result.recommended_bet = "軸固定＋相手BOX（2〜5番手）"
            result.rival_range     = numbers[1:6]
    else:
        result.top3_horses = numbers[:3]
        result.rival_range = numbers[:4]
        if result.gap_1_3 >= 0.30:
            result.judgment        = "明確型"
            result.recommended_bet = "上位3頭が分離、軸1頭流し"
        elif result.gap_1_3 >= 0.15:
            result.judgment        = "準混戦"
            result.recommended_bet = "軸1頭流し（馬連）"
        elif result.gap_1_3 >= 0.08:
            result.judgment        = "混戦"
            result.recommended_bet = "馬連BOX（上位3〜4頭）"
        else:
            result.judgment        = "超混戦"
            result.recommended_bet = "見送り候補"

    return result


# ──────────────────────────────────────────────
# Phase1用ランキング（後方互換）
# ──────────────────────────────────────────────

def build_ranking(phase1_results: list) -> list:
    return sorted(phase1_results, key=lambda x: x.phase1_score)


# ──────────────────────────────────────────────
# Phase5：人間確認スコア補正
# ──────────────────────────────────────────────

PADDOCK_BONUS = {
    # スコアから引く値（正値=有利）。◎±2.0pt、○±1.0pt
    "◎": +2.0, "○": +1.0, "△": 0.0, "×": -2.0, "—": 0.0,
}
TRACK_BIAS_BONUS = {
    "内有利":   {"内": -0.05, "外":  0.05},
    "外有利":   {"内":  0.05, "外": -0.05},
    "フラット": {"内":  0.00, "外":  0.00},
}
# 重馬場適性手動入力ボーナス（スコアから引く値）
MUDDY_TRACK_BONUS = {
    "得意": +2.0, "不得意": -2.0, "—": 0.0,
}


def apply_phase5(
    results: list,
    paddock_ratings: dict,
    track_bias: str = "フラット",
    frame_positions: dict = None,
    muddy_ratings: dict = None,   # v1.1追加：重馬場適性手動入力
) -> list:
    if frame_positions is None:
        frame_positions = {}
    if muddy_ratings is None:
        muddy_ratings = {}

    adjusted = []
    for r in results:
        new_r = copy.copy(r)
        use_phase2 = hasattr(r, "phase2_score")
        base_score = r.phase2_score if use_phase2 else r.phase1_score
        note_parts = []

        # パドック補正
        paddock = paddock_ratings.get(r.horse_number, "—")
        pad_bonus = PADDOCK_BONUS.get(paddock, 0.0)
        adjusted_score = base_score - pad_bonus
        if pad_bonus != 0.0:
            note_parts.append(f"パドック{paddock}:{pad_bonus:+.1f}")

        # 枠位置バイアス補正
        position = frame_positions.get(r.horse_number, "")
        bias_map = TRACK_BIAS_BONUS.get(track_bias, {})
        bias_bonus = bias_map.get(position, 0.0)
        adjusted_score -= bias_bonus
        if bias_bonus != 0.0:
            note_parts.append(f"枠{position}:{bias_bonus:+.1f}")

        # 重馬場適性手動補正（v1.1）
        muddy = muddy_ratings.get(r.horse_number, "—")
        muddy_bonus = MUDDY_TRACK_BONUS.get(muddy, 0.0)
        adjusted_score -= muddy_bonus
        if muddy_bonus != 0.0:
            note_parts.append(f"重馬場{muddy}:{muddy_bonus:+.1f}")

        if use_phase2:
            new_r.phase2_score = round(adjusted_score, 3)
        else:
            new_r.phase1_score = round(adjusted_score, 3)

        if note_parts:
            new_r.note = (r.note + f" [P5:{'/'.join(note_parts)}]").strip()

        adjusted.append(new_r)

    if adjusted and hasattr(adjusted[0], "phase2_score"):
        return sorted(adjusted, key=lambda x: x.phase2_score)
    return sorted(adjusted, key=lambda x: x.phase1_score)

# ──────────────────────────────────────────────
# 展開・トラックバイアスロジック（v1.1追加）
# ──────────────────────────────────────────────

# ── ボーナス/ペナルティ値テーブル（後から微調整可能）──
PACE_BIAS_VALUES = {
    "large":  1.5,   # 強ボーナス/ペナルティ
    "medium": 0.8,   # 中ボーナス/ペナルティ
    "small":  0.4,   # 小ボーナス/ペナルティ
}

# 脚質カテゴリ
RUNNING_STYLE_FRONT  = {"逃げ", "先行"}
RUNNING_STYLE_BEHIND = {"差し", "追い込み"}

# 競馬場・距離条件テーブル
VENUE_DISTANCE_BIAS = {
    # (venue, surface, distance_range) → {脚質: (符号, size)}
    ("中山", "芝", (1600, 1600)): {
        "inner_front":  ("bonus",   "large"),   # 内枠逃げ先行
        "outer_behind": ("penalty", "large"),   # 外枠差追
    },
    ("東京", "芝", (1800, 2000)): {
        "outer_front":  ("penalty", "medium"),  # 外枠逃げ先行
    },
    ("京都", "芝", None): {          # 外回り限定（direction="右"で外回り判定）
        "behind":       ("bonus",   "medium"),
    },
    ("中京", "芝", (1200, 1200)): {
        "behind":       ("bonus",   "medium"),
        "nige":         ("penalty", "medium"),
    },
    ("阪神", "芝", None): {          # 内回り限定
        "senkou":       ("bonus",   "medium"),
    },
}

LOCAL_VENUES_PACE = {"福島", "小倉", "函館", "札幌"}   # ローカル先行有利

# 芝重馬場で直線が長い会場（差し有利）
LONG_STRAIGHT_VENUES = {"東京", "阪神"}   # 阪神は外回りのみ


def judge_running_style(corner_pos: str, field_size: int) -> str:
    """
    コーナー通過順位（例："10-9", "3-3-4-4"）と頭数から脚質を判定する。
    戻り値: "逃げ" / "先行" / "差し" / "追い込み" / ""（判定不能）

    判定基準（頭数に対する相対位置）：
      最終コーナー順位 / 頭数
      ≤ 1/頭数（1番手） → 逃げ
      ≤ 0.30            → 先行
      ≤ 0.60            → 差し
      > 0.60            → 追い込み
    """
    if not corner_pos or not field_size:
        return ""
    # 最終コーナー（末尾の数字）を取得
    parts = corner_pos.replace("=", "-").split("-")
    nums = []
    for p in parts:
        try:
            nums.append(int(p.strip()))
        except ValueError:
            pass
    if not nums:
        return ""
    last = nums[-1]
    ratio = last / field_size
    if last == 1:
        return "逃げ"
    elif ratio <= 0.30:
        return "先行"
    elif ratio <= 0.60:
        return "差し"
    else:
        return "追い込み"


def calc_running_style(past_races: list) -> str:
    """
    過去走（最大5走）の脚質判定結果を集計して代表脚質を返す。
    各走でjudge_running_styleを呼び、最頻値を採用。
    """
    from collections import Counter
    styles = []
    for pr in past_races[:5]:
        fs = getattr(pr, "field_size", 0)
        cp = getattr(pr, "corner_pos", "")
        s = judge_running_style(cp, fs)
        if s:
            styles.append(s)
    if not styles:
        return ""
    # 最頻値
    counter = Counter(styles)
    return counter.most_common(1)[0][0]


def calc_pace_bias_bonus(
    horse_name: str,
    horse_number: int,
    frame_number: int,
    running_style: str,     # "逃げ"/"先行"/"差し"/"追い込み"
    field_size: int,        # 出走頭数
    all_styles: list,       # [(horse_number, running_style), ...] 全出走馬
    race_venue: str,        # "東京"/"阪神" など
    race_surface: str,      # "芝"/"ダ"
    race_distance: int,
    race_direction: str,    # "右"/"左"/"右外"/"左外" など
    track_cond: str,        # "良"/"稍重"/"重"/"不良"
    race_week: int,         # 開催週（1=開幕週, 4以上=荒れ馬場）
    course_change: bool,    # コース替わり初週フラグ
) -> tuple[float, str]:
    """
    展開・トラックバイアスによるボーナス/ペナルティを計算する（v1.1）。
    戻り値: (補正値, noteラベル)  ※スコアから引く（正値=有利）
    """
    if not running_style:
        return (0.0, "")

    bonus = 0.0
    notes = []
    V = PACE_BIAS_VALUES

    is_front  = running_style in RUNNING_STYLE_FRONT
    is_behind = running_style in RUNNING_STYLE_BEHIND
    is_nige   = running_style == "逃げ"
    is_senkou = running_style == "先行"
    is_oikomi = running_style == "追い込み"

    # ── 1. レース展開バイアス ──────────────────────────────
    front_count  = sum(1 for _, s in all_styles if s in RUNNING_STYLE_FRONT)
    behind_count = sum(1 for _, s in all_styles if s in RUNNING_STYLE_BEHIND)
    half = field_size / 2

    if front_count >= half and is_behind:
        bonus += V["medium"]
        notes.append(f"前有利展開({front_count}頭):差追有利")
    elif behind_count >= half and is_front:
        bonus += V["medium"]
        notes.append(f"後有利展開({behind_count}頭):逃先有利")

    # ── 2. 競馬場・距離・枠順バイアス ──────────────────────
    is_inner = frame_number <= 4
    is_outer_frame = frame_number >= 12
    is_outer_13 = frame_number >= 13
    is_outer_12 = frame_number >= 12
    is_inner_course = "内" in race_direction or race_direction in ("右", "左")
    is_outer_course = "外" in race_direction

    if race_venue == "中山" and race_surface == "芝" and race_distance == 1600:
        if is_inner and is_front:
            bonus += V["large"]
            notes.append("中山マイル内枠逃先:有利")
        if is_outer_13 and is_behind:
            bonus -= V["large"]
            notes.append("中山マイル外枠差追:不利")

    if race_venue == "東京" and race_surface == "芝" and 1800 <= race_distance <= 2000:
        if is_outer_12 and is_front:
            bonus -= V["medium"]
            notes.append("東京外枠逃先:不利")

    if race_venue == "京都" and race_surface == "芝" and is_outer_course:
        if is_behind:
            bonus += V["medium"]
            notes.append("京都外回り差追:有利")

    if race_venue == "中京" and race_surface == "芝" and race_distance == 1200:
        if is_behind:
            bonus += V["medium"]
            notes.append("中京1200差追:有利")
        if is_nige:
            bonus -= V["medium"]
            notes.append("中京1200逃げ:不利")

    if race_venue == "阪神" and race_surface == "芝" and is_inner_course:
        if is_senkou:
            bonus += V["medium"]
            notes.append("阪神内回り先行:有利")

    if race_venue in LOCAL_VENUES_PACE:
        if is_front:
            bonus += V["medium"]
            notes.append(f"{race_venue}逃先:有利")
        if is_oikomi:
            bonus -= V["medium"]
            notes.append(f"{race_venue}追込:不利")

    # ── 3. 馬場状態バイアス ────────────────────────────────
    is_heavy = track_cond in ("重", "不良")
    is_long_straight = (
        race_venue == "東京" or
        (race_venue == "阪神" and is_outer_course)
    )

    if race_surface == "ダ" and is_heavy:
        if is_front:
            bonus += V["large"]
            notes.append("ダート道悪逃先:有利")

    if race_surface == "芝" and is_heavy:
        if is_long_straight:
            if is_behind:
                bonus += V["medium"]
                notes.append("芝道悪長直線差追:有利")
        else:
            if is_front:
                bonus += V["small"]
                notes.append("芝道悪小回り逃先:有利")

    # ── 4. 開催段階・コース替わりバイアス（芝限定）──────────
    if race_surface == "芝":
        is_opening = (race_week == 1) or course_change
        is_worn    = (race_week >= 4)

        if is_opening and is_front:
            bonus += V["medium"]
            notes.append("開幕週/コース替逃先:有利")
        if is_worn:
            if is_behind:
                bonus += V["small"]
                notes.append("馬場荒れ差追:有利")
            if is_nige:
                bonus -= V["small"]
                notes.append("馬場荒れ逃げ:不利")

    label = "/".join(notes) if notes else ""
    return (round(bonus, 3), label)
