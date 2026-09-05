"""
競馬AI予想システム - 地方競馬(NAR)向け計算エンジン v0.1（骨格）

設計方針（ユーザー確認済み）：
- 対象：帯広（ばんえい）を除く全地方競馬場。南関東・岩手・その他地区を問わない。
- 場ごとのクラス格差（大井A1 vs 佐賀A1等）は絶対値として調整しない。
  地方の馬は基本的に同一地区内（南関東同士、岩手・水沢同士等）でしか
  対戦しないため、出走馬同士の「相対評価」が合っていれば実用上問題ない、
  という前提（一律換算でシンプルに）。
- calculator.py（JRA用）とは別ファイルとして独立させる。
  Phase1のクラス基準値まわりだけ差し替え、それ以外の汎用ロジック
  （距離適性・近走不振ペナルティ・出走間隔補正・格ボーナス等）は
  calculator.pyの関数をそのままimportして再利用する。

calculator.pyのcalc_phase1()との主な違い：
- 「地方走除外→中央実績ゼロなら地方フォールバック」という中央システムの
  ロジックは廃止。NARでは地方走が除外対象ではなく本体そのものなので、
  全過去走をそのまま評価対象にする。
- クラス基準値はCLASS_BASE（JRA）ではなくCLASS_BASE_NAR（本ファイル定義）
  を使う。ただしJpn1〜3（交流重賞）はJRA側のCLASS_BASEをそのまま使う
  （calc_grade_bonus等が既にJpn1〜3の著名レース名テーブルを持っているため）。
- 障害転向処理・馬齢限定OP読み替え・牝馬混合好走ボーナス・
  JRA固有のレース名ペナルティ（クラシック・フェブラリー等）は
  NARには馴染まないため v1 では実装しない（将来必要になれば追加）。

未検証・要注意：
- CLASS_BASE_NARの数値（OP=80/A=84/B=88/C=92）は「一律換算」の暫定値。
  実際の予想結果と照合しながら調整が必要。
- 過去走のrace_classには「C2三　四」のような地方特有の表記ゆれが
  混在することを確認済み（NAR公式サイトの表記に準拠しているため正常）。
  正規表現 \bA[0-9]*\b 等で対応済みだが、まれに誤判定の可能性あり。
"""

import re
import unicodedata
import statistics
from typing import Optional

# バージョン識別用（お手元のファイルが最新か確認する用途）
__version__ = "3.4-recalibration_v7_penalty_cap_bugfix"

# ── v3.4 再キャリブレーション反映（2026/9/5・7巡目）─────────────────
# 重大バグ修正：近走不振ペナルティの適用箇所に、NAR_FORM_PENALTY_CAPとは
# 別のハードコードされた合算上限（3.0固定）が残っており、v2.9で
# NAR_FORM_PENALTY_CAPが3.0を超えて以降、増額分が実質無効化されていた
# （詳細はcalc_phase1_nar内のコメント参照）。NAR_COMBINED_PENALTY_CAPとして
# NAR_FORM_PENALTY_CAPに連動する形に修正。
#
# 同じ--calc-version（v3.3）で再検証した結果、距離好走1・2着・近走不振・
# 昇級(前走非勝利)・中央転入は引き続き有意・同方向で半分反映を継続。
# 距離好走3着はimplied値が現行値とほぼ一致（差0.10pt）＝4回連続で
# 「現行値の方が高い」系の結果が続いていたが、ここでようやく収束と判断し
# 凍結。近走不振×低走数の交互作用が2期間連続で有意となったため新規実装。

# ── v3.3 再キャリブレーション反映（2026/9/4・6巡目・過去最大サンプル）──
# キャッシュ済み全期間（4/24〜8/24、約3521レース・36,026頭）を合算した、
# これまでで最大のサンプルで再検証。大サンプルで得られた推定値を最も
# 信頼できるものとして扱い、これまで「複数の小さい検証期間で方向が
# 矛盾していたため保留」にしていたタグ（距離好走2着・昇級(前走非勝利)）
# も含めて整理し直す：
#   - 距離好走1着・近走不振・中央転入：引き続き有意・同方向 → 増額継続
#   - 距離好走2着：implied値が現行値とほぼ一致（ギャップ解消）→ 凍結解除・
#     小幅増額
#   - 距離好走3着：3回目の検証でも現行値の方がimpliedより高い → さらに減額
#   - 昇級(前走非勝利)：大サンプルで明確に有意・ギャップも大きい → 凍結解除・
#     増額
#   - 距離好走3着×低走数：3回連続で有意 → 追加ボーナスをさらに増額
#   - 昇級(前走非勝利)×低走数・距離好走2着×低走数：新たに有意 → 新規実装
#   - 近走不振×低走数：新たに有意だが階層式ペナルティへの組み込みが複雑
#     なため、今回は実装を見送り要検討事項としてメモ
#   - 格B：引き続き有意（implied+4.17pt）だが、個別グレードへの分解が
#     済んでいないため引き続き未対応

# ── v3.2 再キャリブレーション反映（2026/9/3・5巡目・別の未使用期間での検証）──
# 4/24〜6/23（これも未使用期間）で再検証した結果：
#   - 距離好走1着・近走不振・中央転入：引き続き有意・同方向 → 半分反映を継続
#   - 距離好走3着：2回連続の未使用期間検証（6/24-7/23, 4/24-6/23）とも
#     implied値が現行値を下回った → 増額しすぎと判断し、初めて減額に転じる
#   - 距離好走2着・昇級(前走非勝利)：2回の未使用期間検証で必要量の方向が
#     矛盾（サンプルノイズの影響が大きいとみられる）→ 変更を一旦停止
#   - 距離好走3着×低走数の交互作用：2回連続で有意 → 新規実装
#   - 格Bは引き続き非有意〜要分解のため未対応

# ── v3.1 再キャリブレーション反映（2026/9/1・4巡目・未使用期間での検証）──
# これまで3巡連続で同じ期間（7/24〜8/24）を使い回していたため、「本当に
# 収束に近づいているのか、同じデータへの過剰適合なのか」を区別できない
# 懸念があった。そこで一度も使っていない期間（6/24〜7/23）で検証した結果：
#   - 距離好走1着・距離好走2着・近走不振・中央転入：新データでも引き続き
#     有意・同方向 → 半分反映を継続
#   - 距離好走3着・昇級(前走非勝利)：新データでは非有意になり、しかも
#     implied値が現行値より低い（つまり「もう増額不要かもしれない」方向）
#     → 過剰適合していた可能性が高いと判断し、これ以上の増額を停止
#     （減額は根拠不十分なため見送り、現状維持で次回また検証）
# 格B・昇級(僅差勝ち)は引き続き非有意のため変更なし。

# ── v3.0 再キャリブレーション反映（2026/9/1・3巡目）───────────────
# v2.9投入後の新データでrecalibrate.pyを再実行し、距離好走・近走不振・
# 昇級(前走非勝利)いずれもギャップが継続的に縮小（24〜25%）していることを
# 確認。加えて今回初めて--interact-low-runs 2を付けて中央転入を再検証した
# 結果、3走以上の馬向けの必要ボーナスが+10.22pt相当（従来の想定+7.46ptより
# 大幅に高い）と判明した一方、低走数馬向けの割引後実効値は現行(5.1pt)と
# implied(4.63pt)がほぼ一致しており、低走数側は概ね適正、3走以上側が
# まだ大きく不足という内訳が明らかになった。

# ── v2.9 再キャリブレーション反映（2026/8/31・2巡目）─────────────
# v2.8投入後に新規収集したNARデータ（2026/7/24〜8/24、calc_version=2.8で
# 再収集）でrecalibrate.pyを再実行した結果、距離好走・近走不振・
# 昇級(前走非勝利)・中央転入のいずれも「現行値とimplied値のギャップ」が
# 縮小（15〜67%）していることを確認。半分反映のアプローチが正しい方向に
# 効いていることが裏付けられたため、同じ考え方（現行値とimplied値の中間）
# でもう一段階反映する。格B・昇級(僅差勝ち)は引き続き非有意のため変更なし。

# ── v2.8 再キャリブレーション反映（2026/8/28）─────────────────────
# recalibrate.py（NARデータ約1100レース・約10,900頭、2026/7/24〜8/24）による
# ConditionalLogit分析の結果を、以下の各定数に反映した。
#
# 方針（ユーザー確認済み）：
# - implied値をそのまま反映せず、まず半分程度だけ動かす（現行値とimplied値の
#   中間）。データを追加収集後にrecalibrate.pyを再実行し、残差係数が0に
#   近づいているか確認しながら漸進的に調整する。
# - 距離好走ボーナス（DIST_GOOD_FINISH_BONUS）はcalculator.py（JRA用）と
#   共有の定数だが、今回の分析はNARデータのみに基づくため、JRA側の挙動に
#   影響を与えないようcalculator.py側は変更せず、NAR専用の値
#   （NAR_DIST_GOOD_FINISH_BONUS）をcalc_distance_aptitude_bonus()の
#   bonus_table引数として渡す方式にした（v1.3でJRA側に追加した後方互換
#   引数。bonus_table省略時はJRA側は従来通りDIST_GOOD_FINISH_BONUSを使う
#   ため、JRA側の挙動は完全に不変）。
# - 格B・昇級(僅差勝ち)は非有意だったため変更なし。
# - NAR版・馬齢限定戦トグル、システム予想vs実際の人気の直接比較検証は
#   別途保留中（引き継ぎプロンプト参照）。

# calculator.py の汎用ロジック・データクラスをそのまま再利用
from calculator import (
    Phase1Result,
    Phase2Result,
    Phase4Result,
    FINISH_BONUS,
    FINISH_BONUS_DEFAULT,
    MARGIN_BONUS_THRESHOLDS,
    RELATIVE_FINISH_PENALTY,
    WEIGHT_RECENT,
    BASE_WEIGHT,
    BEST_BONUS_FACTOR,
    INSTABILITY_FACTOR,
    CLASS_BASE,             # Jpn1〜3等、JRA側の基準値を交流重賞判定に流用
    calc_distance_aptitude_bonus,
    calc_grade_bonus,
    _detect_grade_key,
    _normalize_grade,
    # Phase2〜4・Phase5はJRA/NARで差がないためそのまま流用可能
    calc_phase2,
    calc_phase2_all,
    build_ranking_phase2,
    calc_venue_jockey_stats,
    apply_venue_jockey_bonus,
    calc_phase4,
    build_ranking,
    apply_phase5,
    PADDOCK_BONUS,
    TRACK_BIAS_BONUS,
    MUDDY_TRACK_BONUS,
    judge_running_style,
    calc_running_style,
    calc_pace_bias_bonus,
    VenueJockeyStats,
    TRACK_BAD,
    CONDITION_BONUS_TABLE,
)
import copy

# ──────────────────────────────────────────────
# Phase5（人間確認：パドック・馬場バイアス・重馬場適性）はJRA/NARで
# ロジックの差がないため、calculator.pyのapply_phase5をそのまま流用する
# （上のimportブロックで再エクスポート済み。calculator_nar.apply_phase5
# として呼び出し可能）。NAR固有の値テーブルは不要。
# ──────────────────────────────────────────────


# ──────────────────────────────────────────────
# NAR用クラス基準値（v0.1・一律換算・要検証）
# ──────────────────────────────────────────────
# JRA同様「小さいほど格が高い」設計。OPをJRA側OP(80.0)に合わせ、
# A/B/Cを4pt刻みで下に積む（JRAの4pt刻みラダーと連続性を持たせる）。
CLASS_BASE_NAR = {
    "OP":   80.0,   # 重賞・オープン
    "A":    84.0,
    "B":    88.0,
    "C":    92.0,
    "若齢戦": 92.0,   # 2歳・3歳等、A/B/Cが付かず馬齢のみで組分けされるレース
                     # （世代内賞金順の輪切りで、C級相当の入門レースと位置づけ・要継続検証）
    "チャレンジ": 92.0,  # 未勝利戦相当。新馬〜複数走しても勝てなかった馬の
                       # 「残留組」であり、フレッシュより格下と位置づける
    "フレッシュ": 90.0,  # 新馬戦相当。未知数の資質馬も含む全馬の初出走の場で、
                       # 平均的な粒はチャレンジ（未勝利＝勝てないと分かった
                       # 馬の集まり）より上とみなし、若干格上（数値を小さく）に設定
}
CLASS_BASE_NAR_DEFAULT = 95.0   # クラス不明時のフォールバック値

# ── 地区グルーピング（ユーザー提案） ──────────────────────────
# 地方競馬は場ごとに実力差があるが、実際に馬が転戦するのは主に
# 同一地区内（岩手なら盛岡⇄水沢、南関東なら浦和⇄船橋⇄大井⇄川崎等）。
# 地区をまたぐ転厩は稀だが起きた場合、特に「南関東→他地区」の移動は
# 全国的に南関東の層の厚さ・レベルの高さが際立って知られているため、
# 南関東で通用しなかった馬が他地区に移ると好走するケース
# （逆に他地区→南関東は苦戦するケース）が経験則としてよく見られる。
NAR_REGION_GROUPS = {
    "盛岡": "岩手", "水沢": "岩手",
    "浦和": "南関東", "船橋": "南関東", "大井": "南関東", "川崎": "南関東",
    "姫路": "兵庫", "園田": "兵庫",
    "名古屋": "東海", "笠松": "東海",
    "門別": "北海道",
    "金沢": "北陸",
    "高知": "高知",
    "佐賀": "佐賀",
}
# 南関東は他地区より基本的にレベルが高いとされる（暫定・経験則ベース）
TOUGHER_REGIONS_NAR = {"南関東"}

# v1.9修正：地区転入時の評価方法を「末尾で一律少額を割り引く」方式から
# 「南関東（格上地区）で記録した大差負け・最下位圏ペナルティをレース単位で
# 免除する」方式に変更。
# 理由：大差負け(NAR_LARGE_MARGIN_PENALTY)・最下位圏(RELATIVE_FINISH_PENALTY)は
# calc_race_point_nar()内で各走の得点そのものに焼き込まれるため、南関東で
# 2走大敗しているだけで簡単に+9pt前後の重いペナルティが蓄積する。これに対し
# 旧方式の一律-3.0pt補正では焼け石に水で、「南関東の大敗は参考にしない」という
# 意図が実効的に反映されていなかった（盛岡2026-07-21 10R・レーザースペックル
# の事後検証で発覚：南関東2走大敗ペナルティ+9.0ptに対し旧補正はわずか-3.0pt）。
# TOUGHER_REGION_PENALTY_DISCOUNT: 1.0=全額免除、0.5=半額免除、0.0=免除なし。
# 免除なし側（南関東→他地区）は罰則を設けない方針は維持。
TOUGHER_REGION_PENALTY_DISCOUNT = 1.0

# 地区間移動ボーナス：末尾での一律加点は廃止（上記の per-race 免除方式に統合）。
# 定数は後方互換のため残すが、v1.9以降は使用しない。
REGION_TRANSFER_BONUS_PER_RACE = 1.5
REGION_TRANSFER_BONUS_MAX = 3.0

# 中央(JRA)からの転入ボーナス：
# 中央未勝利・下級条件で大敗していても、地方（特にCクラス）に転入すれば
# 即通用するケースが非常に多い（特に秋に多く見られる、JRAを見切った馬の
# 地方転入パターン）。中央⇔地方のレベル差は南関東⇔他地区の差よりも
# さらに大きいと考えられるため、地区転入ボーナスより大きめの値を設定する。
#
# v2.8：recalibrate.py（NARデータのみ）で、3走以上の馬にはimplied+7.72pt
# （現行max+6.0pt）と過小評価が示された一方、有効走数2走以下の馬では
# 逆にimplied-4.22pt相当（過大評価）と示されたため、低走数馬向けに
# ボーナスを割り引く仕組みを追加した。半分反映の方針に基づき：
#   - MAX: 6.0 → 6.9（implied 7.72との中間）、PER_RACEも同倍率で調整
#   - 低走数馬（有効走数<=CENTRAL_TRANSFER_LOW_RUNS_THRESHOLD）は、
#     算出したボーナスからCENTRAL_TRANSFER_LOW_RUNS_DISCOUNTを差し引く
#     （implied-4.22pt相当の半分＝-2.1pt。0未満にはならないようフロアあり）
#
# v2.9（2巡目）：v2.8投入後の新データで再検証したところ、implied値は
# +7.46pt（現行6.9pt）とギャップが縮小（1.72→0.56pt）していたため、
# 半分反映の方針を継続してMAX/PER_RACEをさらに引き上げる。
# ただしこの再検証は--interact-low-runsを付けずに実行したため、低走数
# 馬との交互作用（CENTRAL_TRANSFER_LOW_RUNS_DISCOUNT）は今回未検証。
# 次回は--interact-low-runs 2を付けて再検証してから、この値も見直すこと。
#
# v3.0（3巡目）：--interact-low-runs 2で初めて交互作用を再検証した結果、
# 3走以上の馬向けの必要ボーナスがimplied+10.22pt相当（従来の想定+7.46pt
# より大幅に高い＝これまでの「低走数込みの合算」は3走以上側の本当の
# 必要量を過小評価していたことが判明）。一方、低走数馬向けの割引後
# 実効値はimplied+4.63pt相当で、現行の実効値（MAX-DISCOUNT=7.2-2.1=5.1pt）
# とほぼ一致（低走数側は概ね適正）。
# 半分反映の方針に基づき、MAX/PER_RACEを3走以上側のimplied値に向けて
# 引き上げ、DISCOUNTも低走数側の実効値をimplied(4.63)に近づける方向で
# 調整する（新MAX-新DISCOUNT ≈ 4.63になるよう半分反映で計算）。
#
# v3.2（5巡目・別の未使用期間4/24〜6/23での検証）：3走以上側は今回も
# implied+11.97pt相当と有意に高いまま（現行9.7pt、ギャップ2.27pt）で、
# 2期間連続で本物の効果と確認できた。低走数側の実効値もimplied+5.44pt相当
# ・現行実効5.2pt（9.7-4.5）とほぼ一致し続けている。半分反映の方針で
# MAX/PER_RACEをさらに引き上げ、DISCOUNTも実効値をimplied寄りに微調整する。
#
# v3.3（6巡目・4/24〜8/24合算の過去最大サンプル）：3走以上側は今回も
# implied+12.12pt相当と有意に高いまま（現行10.8pt、ギャップ1.32pt）で、
# 引き続き半分反映を継続。低走数側の実効値もimplied+5.40pt相当・現行実効
# 5.3pt（10.8-5.5）とほぼ一致し続けている（ギャップ0.10pt、ほぼ収束）。
#
# v3.4（7巡目・同一--calc-versionでの再検証）：3走以上側は今回も
# implied+12.70pt相当と有意に高いまま（現行11.5pt、ギャップ1.20pt）で、
# 引き続き半分反映を継続。低走数側の実効値もimplied+5.66pt相当・現行実効
# 5.3pt（11.5-6.2）とほぼ一致し続けている。
CENTRAL_TRANSFER_BONUS_PER_RACE = 5.0
CENTRAL_TRANSFER_BONUS_MAX = 12.1
CENTRAL_TRANSFER_LOW_RUNS_THRESHOLD = 2
CENTRAL_TRANSFER_LOW_RUNS_DISCOUNT = 6.6

# ── NAR距離好走ボーナス（v2.8追加：calculator.pyのDIST_GOOD_FINISH_BONUSを
# NAR専用の値で上書き。JRA側（calculator.py）はDIST_GOOD_FINISH_BONUS={1:1.2,
# 2:0.9, 3:0.6}のまま変更なし）。
# recalibrate.py（NARデータのみ）で implied 値：1着+4.52 / 2着+3.56 / 3着+2.91
# （現行1.2/0.9/0.6は大幅な過小評価）と示されたため、半分反映の方針に基づき
# 現行値とimplied値の中間に設定する。
#
# v2.9（2巡目）：v2.8投入後の新データでimplied値は+5.73/+4.45/+3.60pt
# （現行2.9/2.2/1.8）と、ギャップが縮小（15〜22%）していたため、
# 同じ考え方でもう一段階引き上げる。
#
# v3.0（3巡目）：v2.9投入後の新データでimplied値は+6.45/+5.01/+4.05pt
# （現行4.3/3.3/2.7）と、ギャップがさらに縮小（24〜25%）していたため、
# 同じ考え方でもう一段階引き上げる。
#
# v3.1（4巡目・未使用期間6/24〜7/23での検証）：1着・2着は新データでも
# 引き続き有意（implied+7.10/+6.02pt、現行5.4/4.2pt）で本物の効果と
# 確認できたため、同じ考え方でもう一段階引き上げる。
# 一方、3着は新データで非有意（p=0.058）になり、implied値(+1.88pt)が
# 現行(3.4pt)を下回った＝これまでの増額が同じ7/24〜8/24データへの
# 過剰適合だった可能性が高いと判断し、3着だけ今回は増額を停止（現状維持）。
# 減額するには根拠が弱いため見送り、次回また有意性を確認する。
#
# v3.2（5巡目・別の未使用期間4/24〜6/23での検証）：
#   - 1着：引き続き有意（implied+7.04pt、現行6.3pt）→ 半分反映を継続
#   - 2着：有意だがimplied(+4.50pt)が現行(5.1pt)を下回った。前回
#     （v3.1検証時）はimplied+6.02ptと逆方向だったため、期間による
#     ブレが大きいと判断し、今回は変更を停止（現状維持、増減どちらも
#     見送り）。より大きなサンプル（複数期間合算）での再検証待ち。
#   - 3着：今回もimplied(+2.26pt)が現行(3.4pt)を下回った。前回
#     （非有意、implied+1.88pt）と合わせて2回連続で「現行値の方が高い」
#     という結果になったため、初めて減額に転じる（半分反映＝現行値と
#     2回の実測値の平均implied(約2.07pt)の中間）。
#
# v3.3（6巡目・4/24〜8/24合算の過去最大サンプル）：
#   - 1着：引き続き有意（implied+7.44pt、現行6.7pt）→ 半分反映を継続
#   - 2着：implied(+5.27pt)が現行(5.1pt)とほぼ一致（ギャップ0.17pt）＝
#     保留にしていた矛盾が解消し、ほぼ収束していたと判明。凍結解除の
#     うえ小幅増額。
#   - 3着：3回目の検証でもimplied(+2.29pt)が現行(2.7pt)を下回り続けた
#     ため、さらに減額する。
#
# v3.4（7巡目・同一--calc-versionでの再検証）：
#   - 1着：引き続き有意（implied+7.80pt、現行7.1pt）→ 半分反映を継続
#   - 2着：引き続き有意（implied+5.53pt、現行5.2pt）→ 半分反映を継続
#   - 3着：implied(+2.40pt)が現行(2.5pt)とほぼ一致（ギャップ0.10pt）＝
#     4回連続で「現行値の方が高い/近い」結果が続いたため、ここで収束と
#     判断し凍結（変更なし）。
NAR_DIST_GOOD_FINISH_BONUS = {1: 7.5, 2: 5.4, 3: 2.5}
# 距離好走1着については、有効走数が少ない馬でさらに強い効果（implied
# 追加+1.89pt）が確認されたため、該当馬にのみ追加ボーナスを加える
# （半分反映＝+0.9pt）。2着・3着については有意な低走数交互作用は
# 確認されていないため対象外。
# v2.9：この交互作用項もv2.8以降未検証（--interact-low-runsを付けての
# 再検証待ち）のため、値は据え置き。
#
# v3.1（4巡目）：未使用期間6/24〜7/23での検証で、今回初めて「距離好走3着
# ×低走数」の交互作用が有意（+6.00pt相当）と出た。ただし初出であり、
# かつ3着本体は同じ検証で非有意化した（過剰適合疑い）ばかりなので、
# 1回の結果だけでは実在の効果か偶然かの判断がつかない。次回以降も
# 継続して有意なら、その時点で対応を検討する（現時点では未実装のまま
# 据え置き）。
#
# v3.2（5巡目）：別の未使用期間4/24〜6/23でも「距離好走3着×低走数」が
# 再び有意（+3.61pt相当）となり、2期間連続で再現したため実在の効果と
# 判断。新たにNAR_DIST_GOOD_FINISH_LOW_RUNS_EXTRA_3RDとして実装する
# （半分反映＝2回のimplied平均(4.81pt)の半分≈2.4pt）。
#
# v3.3（6巡目）：4/24〜8/24合算の過去最大サンプルでも「距離好走3着×
# 低走数」が3回連続で有意（implied+4.61pt、現行2.4pt）→ さらに増額。
# 一方「距離好走1着×低走数」（EXTRA_1ST）は今回implied+1.10ptとなり
# 非有意（p=0.173）。round3では有意だったが、round4・round5・今回と
# 3回連続で非有意という結果が続いている。implied値自体は現行(0.9)と
# 近いため、無理に変更せず据え置くが、この交互作用の実在性は今後も
# 注視する。
#
# v3.4（7巡目）：同一--calc-versionでの再検証でも「距離好走3着×低走数」
# implied+4.83pt（現行3.5pt）→ さらに増額。「距離好走2着×低走数」も
# implied+2.24pt（現行1.1pt）→ 増額。「距離好走1着×低走数」は今回も
# 非有意（p=0.173、round3以降4回連続で非有意）のため据え置き。
NAR_DIST_LOW_RUNS_THRESHOLD = 2
NAR_DIST_GOOD_FINISH_LOW_RUNS_EXTRA_1ST = 0.9
NAR_DIST_GOOD_FINISH_LOW_RUNS_EXTRA_3RD = 4.2
NAR_DIST_GOOD_FINISH_LOW_RUNS_EXTRA_2ND = 1.7

# v3.3新規実装：昇級(前走非勝利)×低走数の交互作用が今回有意
# （implied+1.62pt）となったため、半分反映（+0.8pt）で新規実装する。
# 低走数閾値はNAR_DIST_LOW_RUNS_THRESHOLDと共通のNAR_FORM系の枠組みでは
# なく、こちらも同じ「有効走数<=2」の定義を流用する。
# v3.4：同一--calc-versionでの再検証でも引き続き有意（implied+1.70pt、
# 現行0.8pt）→ 半分反映を継続。
NAR_MOMENTUM_LOW_RUNS_EXTRA_PENALTY = 1.3


def get_region_nar(venue: str) -> str:
    """競馬場名から地区グループ名を返す。未登録の場は単独グループ（場名そのもの）として扱う。"""
    return NAR_REGION_GROUPS.get(venue, venue)

# ── 「組」（クラス内の実力別グループ）補正 ──────────────────────
# 地方競馬では同じクラス（例：C2）内でも、獲得ポイント順に「一組」〜「十組」等の
# 細分化グループに分けられ、組の数字（漢数字）が小さいほど格上（＝そのクラスの中でも
# 実力上位で、昇級が近い）、大きいほど格下（＝そのクラスでは苦戦している）とされる。
# 例：C2七組で勝った実績があっても、C2二組では通用しないことが多い
#     （逆に、C2二組で通用していた馬がC2七組に「格下げ」で回ってくると
#      あっさり勝つ「格下げのヤリ」と呼ばれる現象が起きる）。
#
# KUMI_STEP_NAR：組1つ分の格差をポイント換算した値。
# A/B/Cクラス間の格差が4.0pt/tierで、1クラス内におおよそ10組前後あることが多いため、
# 4.0/10 ≒ 0.4pt/組 を目安に設定（暫定値・要検証）。
KUMI_STEP_NAR = 0.4

_KANJI_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}


def kanji_to_int(s: str) -> Optional[int]:
    """
    漢数字（一〜三十程度）を整数に変換する。
    地方競馬の「組」表記（一組〜十組、まれに十一組以上）を想定した簡易実装。
    例：'二' → 2, '十' → 10, '十一' → 11, '二十' → 20
    変換できない場合はNoneを返す。
    """
    if not s:
        return None
    if "十" not in s:
        # 単純な一桁（一〜九）
        return _KANJI_DIGITS.get(s)

    # "十"を含むケース：十／十X／X十／X十Y
    parts = s.split("十")
    if len(parts) != 2:
        return None
    left, right = parts
    tens = 1 if left == "" else _KANJI_DIGITS.get(left)
    ones = 0 if right == "" else _KANJI_DIGITS.get(right)
    if tens is None or ones is None:
        return None
    return tens * 10 + ones


# ── カタカナ組番号：2つの体系がある ──────────────────────────
# 複数の情報源で確認：地方競馬の組番号表記には「カタカナ＝アイウエオ順
# （ア＞イ＞ウ＞エ、五十音順）」と「イロハ＝いろは順（イ＞ロ＞ハ＞ニ）」の
# 2つの別々の体系がある。両者は同じカタカナ文字を使い回すため、1文字だけ
# 見ても機械的にどちらの体系か判別できない。
# 当初、実データで見つけた「エ」「ア」を古典いろは歌の並び（い・ろ・は・
# に・ほ・へ・と…）で解釈し、エ=34番目・ア=36番目という値を採用しかけたが、
# これまで確認してきた組番号の規模感（数字・漢数字とも最大14程度）と比べて
# 明らかに大きすぎ、不自然だった。複数の情報源で「カタカナ＝アイウエオ順」
# が明記されていること、"特選（イ）"（＝厳選された上位グループ）という
# 用例とも整合しやすいことから、五十音順を既定の解釈として採用する
# （ア=1,イ=2,ウ=3,エ=4,オ=5,カ=6...の五十音表の並び）。
# 実際に「イロハ順」を使っている競馬場が見つかった場合は、その場だけ
# 個別に切り替えられるよう、いろは順テーブルも別途残しておく（現状未使用）。
GOJUON_KATAKANA_ORDER = (
    "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン"
)
IROHA_ORDER = "イロハニホヘトチリヌルヲワカヨタレソツネナラムウヰノオクヤマケフコエテアサキユメミシヱヒモセス"


def iroha_to_int(ch: str) -> Optional[int]:
    """
    カタカナ1文字を組番号（1始まり）に変換する。
    既定では五十音順（GOJUON_KATAKANA_ORDER）を使用する（該当なしはNone）。
    """
    idx = GOJUON_KATAKANA_ORDER.find(ch)
    return idx + 1 if idx >= 0 else None


def _resolve_group_token(content: str) -> list:
    """
    括弧内・トークン文字列から組番号（複数の場合あり）を抽出する共通処理。
    算用数字→漢数字→いろは順の順に試し、いずれも変換できない場合は
    1文字ずつ独立した組番号として解釈するフォールバックを行う。
    """
    content = content.strip()
    if not content:
        return []
    if content.isdigit():
        return [int(content)]
    n = kanji_to_int(content)
    if n is not None:
        return [n]
    n = iroha_to_int(content)
    if n is not None:
        return [n]
    nums = []
    for ch in content:
        n2 = kanji_to_int(ch)
        if n2 is None:
            n2 = iroha_to_int(ch)
        if n2 is not None:
            nums.append(n2)
    return nums


def extract_kumi(race_class: str) -> Optional[int]:
    """
    race_class文字列から「組」の番号を抽出する（岩手・東海・金沢スタイル限定）。
    例："C2七組" → 7、"C2 十組" → 10、"本庄宿賞(C3)" → None（組表記なし）
    """
    if not race_class:
        return None
    rc = unicodedata.normalize("NFKC", race_class)
    m = re.search(r"([一二三四五六七八九十]{1,3})組", rc)
    if not m:
        return None
    return kanji_to_int(m.group(1))


def extract_fine_tier(race_class: str) -> Optional[float]:
    """
    地方競馬のクラス内細分化表記（「組」相当）を、地区ごとの表記差を吸収して
    横断的に抽出する。ユーザー調査に基づき、以下の4パターンに対応：

      ① 岩手・東海・金沢スタイル："C2七組"（単一漢数字+組） → 7
      ② 南関東スタイル："C2三　四"（複数の漢数字を並記、組番号を複数列挙）
         → 該当レースは複数組の合併戦のため、列挙された数字の平均を採用
         　（組数が二桁に達すると"C2十一　十二"ではなく"11　12"のように
         　  算用数字表記に切り替わる場合があり、それにも同じロジックで対応。
         　  漢数字と算用数字が混在する"九　十11"のようなケースも列挙値として
         　  まとめて平均する）
      ③ 兵庫・高知スタイル："C3一"（漢数字がクラスに直接くっつく固定クラス）
         → 1（組ではなく固定クラスだが、数字が小さいほど格上という
           序列自体は共通のため、同じ計算式で扱って問題ない）
      ④ 北海道・佐賀スタイル："C4-3"（ハイフン+算用数字） → 3
         （佐賀では末尾にさらに「組」が付く"C1-14組"のような表記もあるが、
           ハイフン直後の数字だけを見るため影響しない）
      ⑤ 名古屋スタイル："B10組"（区切り文字なしで算用数字がクラス文字に
         直接くっつき、その後ろに"組"が続く） → 10
         （実データ調査で確認。クラス文字直後の数字がクラスの下位区分
           （A1/B2等）なのか組番号そのものなのか区別できないため、
           "組"が続く場合は数字部分を丸ごと組番号として扱う）

    加えて、A/B/Cが付かず馬齢のみで組分けされる2歳・3歳戦（例："2歳ー3組"
    "3歳-10" "3歳五組"）にも対応する。この場合の「クラス記号」はA/B/Cの
    代わりに"[2-9]歳"とみなし、以降の抽出ロジックは共通で扱う
    （数字が小さいほど格上、という序列は年齢限定戦でも変わらないため）。

    括弧内の重複クラス表記（例："本庄宿賞(C2三)"、"C2四　五六　ウ(C2五)"）は
    誤検出防止のため、最初の"("より前の部分のみを対象にする。
    漢数字の連結（例："五六"＝5・6が連結）がkanji_to_intで変換できない場合は、
    1文字ずつ独立した数字として解釈するフォールバックを行う
    （南関東スタイルのスペース欠落・スクレイピング時の空白圧縮対策）。

    見つからない場合はNoneを返す（組・固定クラス表記のないレースと判断）。
    """
    if not race_class:
        return None
    rc = unicodedata.normalize("NFKC", race_class)

    # クラス記号：A/B/C+数字、2〜9歳（馬齢限定戦）、またはフレッシュ/チャレンジ
    # （馬齢限定戦の呼称違い。組・数字を伴わない場合が多いが、念のため
    #  同じ抽出ロジックの対象に含めておく）
    # 注意：「N歳以上」（例："3歳以上"）は馬齢限定戦ではなく全馬齢混合を
    # 意味するため、クラス記号としてマッチさせない（(?!以上)で除外）。
    # 除外しないと、後続の本当のクラス表記（例："3歳以上C3-2 C4-1"の
    # "C3"）を無視して「3歳」を起点に組数字を探してしまい、クラス数字
    # 自体（3・4）まで組番号と誤認して平均が狂う（門別の事後検証で発覚）。
    CLASS_MARKER = r'(?:[ABC][0-9]{0,2}|[2-9]歳(?!以上)|フレッシュ|チャレンジ)'

    # ④ 北海道・佐賀スタイル："C4-3"（ASCIIハイフン）または"C4ー2"
    # （門別の実データで確認：長音符ー＝U+30FCや全角マイナス－＝U+FF0D、
    #  さらに全角マイナス記号−＝U+2212が区切りに使われるケースがあり、
    #  NFKC正規化でもASCIIハイフンには統一されないため、明示的に対応する）
    # 門別スタイル："3歳以上C3-2 C4-1"のように、クラス+ハイフン+組数字が
    # 複数個スペース区切りで並ぶ複数クラス混合表記にも対応する（1つしか
    # 拾えないと片方のクラスの組情報が無視されてしまうため、findallで
    # 全件拾って平均する）。
    m_hyphen_all = re.findall(CLASS_MARKER + r'[-ー－−](\d{1,2})', rc)
    if m_hyphen_all:
        nums = [float(x) for x in m_hyphen_all]
        return sum(nums) / len(nums)

    # ⑤ 名古屋スタイル："B10組"（ハイフンも空白も無く、算用数字が直接
    # クラス文字にくっつき、その後ろに"組"が続く）。実データ調査で確認済み。
    # クラス文字直後の数字は「クラスの下位区分（A1/B2等）」を表す場合と
    # 「組番号そのもの」を表す場合が区別できないため、"組"が続く場合は
    # 数字部分を丸ごと組番号として扱う（非貪欲マッチにより、可能な限り
    # 多くの桁を組番号側に割り当てる）。
    m_attached = re.search(r'[ABC][0-9]{0,2}?(\d{1,2})組', rc)
    if m_attached:
        return float(m_attached.group(1))

    # クラス記号（A/B/C+数字 または N歳）より後ろの部分だけを対象にする
    # （前方にある可能性のあるレース名部分からの誤検出を防ぐ）
    m_letter = re.search(CLASS_MARKER, rc)
    tail = rc[m_letter.end():] if m_letter else rc

    # 括弧より前の部分（誤検出防止：末尾の重複クラス表記括弧を除く用途）
    tail_before_paren = tail.split("(")[0].split("（")[0]

    if tail_before_paren.strip():
        # 通常ケース：括弧より前に組情報がある（例："C2四　五六　ウ(C2五)"
        # のような末尾の重複クラス表記括弧を除去する、既存の想定ケース）
        tail_main = tail_before_paren
        raw_tokens = re.findall(r'[一二三四五六七八九十]{1,3}|\d{1,2}', tail_main)
        nums = []
        for tok in raw_tokens:
            nums.extend(_resolve_group_token(tok))
        if nums:
            return sum(nums) / len(nums)

        # フォールバック：括弧より前に組情報が無かった場合、括弧内を確認する
        # （例："Ｃ２特選（イ）"＝いろは順カタカナが括弧内にのみ存在し、
        # 括弧より前は"特選"という説明文でしかないケース。笠松の実データで
        # 発覚。誤検出防止のため、通常は括弧内を無視する設計だが、括弧より
        # 前に組情報が本当に無い場合に限り、最後の手段として括弧内を見る）
        paren_contents_fb = re.findall(r'[\(（]([^)）]+)[\)）]', tail)
        nums_fb = []
        for content in paren_contents_fb:
            nums_fb.extend(_resolve_group_token(content))
        if nums_fb:
            return sum(nums_fb) / len(nums_fb)
        return None

    # ⑥ 南関東スタイル（丸括弧直付け型）："C3(二)(三)"・"3歳(十一)(十二)"・
    # "C2(エ)C3(ア)"（いろは順カタカナ版）
    # クラス記号の直後に丸括弧で組番号を1つずつ並べる表記（大井・川崎等の
    # 実出走表で確認）。上記tail_before_paren（括弧より前の部分）が空になる
    # ため、既存ロジックのままだと組情報が丸ごと消えて未調整（kumi_adjust=0）
    # になっていた（引き継ぎメモの「南関東の混在パターン」調査中に発覚）。
    # 括弧を1つずつ個別のトークンとして扱い、それぞれを数値化する（複数括弧を
    # 連結してから正規表現でトークン化すると、"(十一)(十二)"のような二桁の
    # 漢数字同士が連結してしまい、誤って別の数値に化けるため、括弧単位で
    # 個別に処理する必要がある）。
    paren_contents = re.findall(r'[\(（]([^)）]+)[\)）]', tail)
    if paren_contents:
        nums = []
        for content in paren_contents:
            nums.extend(_resolve_group_token(content))
        if nums:
            return sum(nums) / len(nums)

    return None


def get_class_base_nar(race_class: str) -> float:
    """
    NAR用クラス基準値を返す。
    優先順位：
      ① JRA側CLASS_BASEに完全一致するキーがあればそちらを優先
         （Jpn1〜3等の交流重賞、または稀にJRA施設で行われるレース対応）
      ② OP/重賞/オープン/Jpn を検出 → CLASS_BASE_NAR["OP"]
      ③ A/B/C + 数字（例："A1", "B7組", "C212"）を正規表現で検出
      ④ A/B/C・組番号を伴わない馬齢限定戦表記：
         "フレッシュ"（新馬相当）→ CLASS_BASE_NAR["フレッシュ"]（90.0）
         "2歳ー3組"等・"チャレンジ"（未勝利相当）→ CLASS_BASE_NAR["若齢戦"]（92.0）
         ※新馬（未知数の資質馬も含む）のほうが未勝利（勝てないと分かった
           残留組）よりやや格上とみなし、フレッシュ<チャレンジの数値関係にする
           （いずれも暫定値。検証結果を見ながら調整する）
      ⑤ いずれにも該当しなければ CLASS_BASE_NAR_DEFAULT
    さらに、③④でクラス記号を検出できた場合、「組」表記があれば
    KUMI_STEP_NARを使って組の格差をさらに細かく反映する
    （組の数字が大きいほど＝格下グループほど基準値を高く＝格下げする）。
    """
    rc = _normalize_grade(race_class)
    rc_norm = unicodedata.normalize("NFKC", rc)

    # ① JRA側の完全一致（Jpn1〜3・G1〜G3等、交流重賞やJRA施行レース対応）
    if rc in CLASS_BASE:
        return CLASS_BASE[rc]
    for key, val in CLASS_BASE.items():
        if key in rc:
            return val

    # ② OP・重賞判定
    if re.search(r'OP|オープン|重賞|Jpn', rc_norm, re.IGNORECASE):
        return CLASS_BASE_NAR["OP"]

    kumi = extract_fine_tier(rc_norm)
    kumi_adjust = (kumi - 1) * KUMI_STEP_NAR if kumi else 0.0

    # ③ 複数クラス混走判定（例："AB混合"）
    # 高知の実データで確認。"A"と"B"が隣接するため、後段の_match_local_class
    # （前後に英数字が来ないことを要求する境界チェック）ではどちらの
    # クラスにも一致せず、クラス不明（新馬相当）に落ちてしまうバグがあった。
    # 該当する各クラスの基準値の平均を採用する（要継続検証）。
    m_mixed = re.search(r'([ABC])([ABC])混合', rc_norm)
    if m_mixed:
        b1 = CLASS_BASE_NAR[m_mixed.group(1)]
        b2 = CLASS_BASE_NAR[m_mixed.group(2)]
        return (b1 + b2) / 2 + kumi_adjust

    # ③' 複数クラス混走判定（区切りなし連結型：例 "C1C2"＝C1・C2混合、
    # "B3C1"＝B3〜C1混合）。南関東（大井・川崎等）でよく見られる、
    # クラス文字+組数字を区切りなしで2つ連結する表記。"AB混合"と異なり
    # "混合"の文字が付かないため、後段の_match_local_class（前後に英数字が
    # 来ないことを要求する境界チェック）では両方のクラス文字ともその境界判定
    # に阻まれ、どちらにもマッチせずクラス不明（95.0）に落ちてしまっていた
    # （盛岡2026-07-21 10R・レーザースペックルの事後検証で発覚。過去3走中
    # 2走がC1C2/B3C1で共に不明扱いとなっており、地区転入によるペナルティ
    # 免除を実施してもなおスコアが改善しきらない一因になっていた）。
    # 該当する各クラスの基準値の平均を採用する（要継続検証）。
    m_concat = re.search(r'([ABC])[1-9]([ABC])[1-9]', rc_norm)
    if m_concat:
        b1 = CLASS_BASE_NAR[m_concat.group(1)]
        b2 = CLASS_BASE_NAR[m_concat.group(2)]
        return (b1 + b2) / 2 + kumi_adjust

    # ④ A/B/Cクラス判定（地方特有の"A1""B7組""C212""サラ系C3"等の表記に対応）
    # 注意：Pythonの\bは漢字等のUnicode文字も「単語文字」とみなすため、
    # "サラ系C3"や"C2三　四"のようにクラス表記へ直接漢字がくっつく
    # ケースで\bベースの境界判定が機能しない（前後がASCII文字か否かで
    # 判定する必要がある）。ASCII英数字のみを対象にした否定先読み・
    # 否定後読みに置き換えて対応する。
    def _match_local_class(letter: str) -> bool:
        pattern = rf'(?<![A-Za-z0-9]){letter}[0-9]{{0,3}}(?![A-Za-z0-9])'
        return bool(re.search(pattern, rc_norm, re.IGNORECASE)) or f"{letter}級" in rc_norm

    if _match_local_class("A"):
        return CLASS_BASE_NAR["A"] + kumi_adjust
    if _match_local_class("B"):
        return CLASS_BASE_NAR["B"] + kumi_adjust
    if _match_local_class("C"):
        return CLASS_BASE_NAR["C"] + kumi_adjust

    # ④ 馬齢限定戦判定：
    #   - "フレッシュ"（新馬戦相当）→ 未勝利より実績が乏しいため個別に高めの基準値
    #   - "2歳ー3組"のようなA/B/Cが付かず組番号のみのパターン、
    #     "チャレンジ"（未勝利戦相当）→ 若齢戦（Cクラス相当）
    # いずれもオープン以外は暫定値であり、検証結果を見ながら個別に調整する
    # （ユーザー判断・要継続検証）。
    if re.search(r'フレッシュ', rc_norm):
        return CLASS_BASE_NAR["フレッシュ"] + kumi_adjust
    if re.search(r'[2-9]歳|チャレンジ', rc_norm):
        return CLASS_BASE_NAR["若齢戦"] + kumi_adjust

    return CLASS_BASE_NAR_DEFAULT


# ── NAR独自の大差負けペナルティ（着差ベース・要継続検証） ────────────────
# JRA側のLARGE_MARGIN_PENALTYをそのまま流用していたが、ユーザー判断
# （門別3R・ノーブルフェスタ vs セトノダイヤモンドの事後検証で発覚）
# により、NAR独自の閾値に切り替える。中央では着差1秒程度でも「大差」と
# されるが、地方競馬では同程度の着差の着外が珍しくなく、次走であっさり
# 持ち直すことも多いという経験則に基づき、中央基準よりペナルティ発動の
# 着差閾値を緩める。
# なお、相対順位ペナルティ（RELATIVE_FINISH_PENALTY・着順/頭数の比率）は
# 着差ではなく「頭数に対してどれだけ後方だったか」という別軸の指標であり、
# 今回の指摘（着差を見ずに大敗と決めつける問題）とは性質が異なるため、
# JRA側の値をそのまま維持する（変更対象外）。
NAR_LARGE_MARGIN_TRIGGER = 1.5   # この着差を超えなければ「大差負け」扱いしない
# v2.5：大差負けペナルティを無効化（0.0に設定）。
# 地方競馬では「着差だけ見ると大敗」でも、展開・馬場・格上挑戦等の事情で
# 実力を反映していないケースが多く、このペナルティで評価を下げた結果、
# 実際にはその馬が普通に好走・勝利してしまうというケースが実運用で
# 何度も確認された（こうすけさんの実戦知見に基づく判断）。
# 閾値・テーブルの構造自体は将来の再調整に備えて残しておく（値を0にする
# ことで無効化する形にし、必要になれば数値を戻すだけで復活できるように
# している）。
# なお、相対順位ペナルティ（RELATIVE_FINISH_PENALTY・着順/頭数の比率）は
# 着差を見ない別軸の指標のため、今回の対象外（変更しない）。
NAR_LARGE_MARGIN_PENALTY = [     # (着差の下限, ペナルティ) ※大きい閾値から判定
    (5.0, 0.0),
    (3.0, 0.0),
    (1.5, 0.0),
]

# ── NAR独自の近走不振ペナルティ（着差ベース・複数走傾向でのみ発動） ────────
# JRA側のcalc_recent_form_penalty()（平均着順ベース、着差を見ない）をそのまま
# 流用していたが、以下2点の理由でNAR独自ロジックに切り替える：
#   ① 着差を考慮する：「1.1秒差の8着」のような、着順は悪いが実際は僅差
#      だったケースまで大敗と同列に扱わない（NAR_FORM_MARGIN_OK以内なら
#      着外でも「不振」に数えない）。
#   ② 複数走での傾向としてのみ発動：地方は中央と違い、1走の大敗があっても
#      次走であっさり持ち直すケースが多いというユーザーの経験則に基づき、
#      直近走（最大3走）のうち「不振」該当がNAR_FORM_MIN_POOR_RACES走に
#      満たない場合は発動しない。
# v2.8：recalibrate.py（NARデータのみ）で「近走不振」タグ全体としての
# implied値が-3.45pt相当（現行はCAP=2.0が上限）と、ペナルティが弱すぎる
# 可能性が示された。半分反映の方針に基づき、各tierとCAPを同倍率
# （約1.36倍＝現行2.0とimplied3.45の中間である2.7への倍率）で引き上げる。
#
# v2.9（2巡目）：v2.8投入後の新データで必要な加算量はimplied+3.62pt
# （現行2.7pt）と、ギャップが縮小（1.45→0.92pt、37%減）していたため、
# 同じ考え方でCAPを2.7→3.2（倍率1.185）に引き上げ、各tierも同倍率で
# スケールする。
#
# v3.0（3巡目）：v2.9投入後の新データで必要な加算量はimplied+4.07pt
# （現行3.2pt）と、ギャップは微減（0.92→0.87pt、5%減）にとどまった
# （伸びが鈍化＝収束に近づいている可能性）。まだ有意なため、同じ考え方で
# もう一段階CAPを3.2→3.6（倍率1.125）に引き上げる。
#
# v3.1（4巡目・未使用期間6/24〜7/23での検証）：新データでも引き続き有意
# （implied+4.40pt、現行3.6pt、ギャップ0.80pt）で、過剰適合ではなく本物の
# 効果と確認できたため、同じ考え方でCAPを3.6→4.0（倍率1.111）に引き上げる。
#
# v3.2（5巡目・別の未使用期間4/24〜6/23での検証）：今回もimplied+5.02pt
# （現行4.0pt、ギャップ1.02pt）と2期間連続で有意・同方向のため、
# 同じ考え方でCAPを4.0→4.5（倍率1.125）に引き上げる。
#
# v3.3（6巡目・4/24〜8/24合算の過去最大サンプル）：今回もimplied+5.05pt
# （現行4.5pt、ギャップ0.55pt）と有意・同方向で、ギャップも縮小してきた
# ため、同じ考え方でCAPを4.5→4.8（倍率1.067）に引き上げる。
# なお今回「近走不振×低走数」の交互作用も新たに有意（implied+2.32pt）
# となったが、この関数は階層式（着差ベースの複数tier合算）のため、
# 単純な加算1本では組み込みにくい。実装は見送り、次回以降の再現性を
# 確認してから改めて設計を検討する（TODO）。
#
# v3.4（7巡目）：同じ--calc-versionで再検証してもimplied+5.29pt（現行
# 4.8pt、ギャップ0.49pt）と有意・同方向のため、同じ考え方でCAPを
# 4.8→5.0（倍率1.042）に引き上げる。
# また、calc_phase1_nar側に「NAR_FORM_PENALTY_CAPとは別のハードコード
# された合算上限3.0」が残っており、v2.9でNAR_FORM_PENALTY_CAPが3.0を
# 超えて以降、この増額分が実質無効化されていたバグを発見・修正した
# （NAR_COMBINED_PENALTY_CAPとして、他ペナルティ源とのスタッキング分の
# 余裕を見込みつつNAR_FORM_PENALTY_CAPに連動させる）。
# 近走不振×低走数の交互作用は2期間連続で有意（round6:+2.32pt、
# round7:+2.43pt）となったため、NAR_FORM_LOW_RUNS_EXTRA_PENALTYとして
# 新規実装する（半分反映＝2回のimplied平均(2.375pt)の半分≈1.2pt）。
NAR_FORM_MARGIN_OK = 0.5        # この着差以内なら着外でも「不振」扱いしない
NAR_FORM_PENALTY_TIERS = [      # (着差の上限, その走の不振ポイント) ※昇順で判定
    (1.5, 0.9),
    (3.0, 1.4),
    (999.0, 2.7),
]
NAR_FORM_PENALTY_CAP = 5.0      # 近走不振ペナルティ単体の上限（旧4.8）
NAR_FORM_MIN_POOR_RACES = 2     # この走数以上「不振」該当で初めて発動
# v3.4新規：他の生ペナルティ（大差負け・最下位圏）とのスタッキング分の
# 余裕（+2.0pt、最下位圏ペナルティの単発最大値相当）を見込んだ合算上限。
# NAR_FORM_PENALTY_CAPを今後調整しても自動的に連動する。
NAR_COMBINED_PENALTY_CAP = NAR_FORM_PENALTY_CAP + 2.0
# v3.4新規：近走不振×低走数の交互作用（半分反映）
NAR_FORM_LOW_RUNS_EXTRA_PENALTY = 1.2


# ── NAR版 昇級勢い（v2.7追加） ────────────────────────────────
# JRA版calc_momentum_bonus()の移植。これまで「地方競馬はクラス変動が
# JRAより頻繁（1着にならなくても昇級することが多い）ため、JRA版をそのまま
# 持ち込むと誤判定が増える」としてv1スコープ外・移植保留にしていたが、
# 2026/8/13大井3R・タツノロマンスの事後検証（新馬3着→C3八九十1着→
# [今回]C3六七八、単勝1.3倍の1番人気で実際に1着だったにも関わらず予想
# 8番手評価だった）で、「前走で現級（または上位の組）を勝ったばかりの
# 馬」を評価できていない実害が具体的に確認されたため実装する。
#
# JRAとの違い：
# - クラス順序の比較にはget_class_base_nar()を使う（数値が小さいほど
#   格上。JRAの_get_class_order（数値が大きいほど格上）とは大小が逆）。
# - get_class_base_nar()は「組」による細分化（例：同じC3でも六七八組と
#   八九十組で0.4pt/組の差がつく）まで織り込み済みのため、A/B/C文字
#   単位の比較よりも精密に「同一クラス内の組の昇格」まで拾える。
def calc_momentum_bonus_nar(
    past_races: list,
    current_class: str,
) -> tuple:
    """
    NAR用 昇級勢い指数。
    前走クラス（組補正込みの基準値）より今回のほうが格上の場合に補正を返す。
    戻り値は (ボーナス値, ラベル)。該当なしなら (0.0, "")。
    ボーナス値は「スコアから引くポイント数」（正値=ボーナス、負値=ペナルティ）。
    格下げにはペナルティを付与しない（格上からの降格は力量上位のため）。
    """
    if not past_races:
        return 0.0, ""
    prev = past_races[0]
    if prev.finish <= 0:
        return 0.0, ""

    prev_base = get_class_base_nar(prev.race_class)
    curr_base = get_class_base_nar(current_class)

    # 基準値が小さいほど格上。浮動小数誤差対策で微小な差は「同格」扱いにする。
    if curr_base < prev_base - 0.05:   # 昇級（組の格上げ含む）
        if prev.finish != 1:
            # 前走で勝っていない昇級（他馬の回避等の特殊ケース）→ ペナルティ
            # v2.8：recalibrate.py（NARデータのみ）でimplied-1.42pt相当と、
            # 現行-0.5ptは過小評価との結果。半分反映で-1.0ptに引き上げ
            # （旧-0.5pt）。
            # v2.9（2巡目）：v2.8投入後の新データで必要な加算量はimplied
            # +1.48pt（現行1.0pt）と、ギャップが縮小（0.92→0.48pt、48%減）
            # していたため、同じ考え方で-1.2ptに引き上げ（旧-1.0pt）。
            # v3.0（3巡目）：v2.9投入後の新データで必要な加算量はimplied
            # +1.66pt（現行1.2pt）と、ギャップはほぼ横ばい（0.48→0.46pt）
            # ＝収束が近いと見られるが、まだ有意なため-1.4ptに引き上げる
            # （旧-1.2pt）。
            # v3.1（4巡目・未使用期間6/24〜7/23での検証）：新データでは
            # 非有意（p=0.077）になり、implied値(+0.99pt)が現行(1.4pt)を
            # 下回った＝これまでの増額が同じ7/24〜8/24データへの過剰適合
            # だった可能性が高いと判断し、今回は増額を停止（現状-1.4pt
            # 維持）。減額するには根拠が弱いため見送り、次回また確認する。
            # v3.2（5巡目・別の未使用期間4/24〜6/23での検証）：今回は有意に
            # 戻り、implied値(+2.75pt)は逆に現行(1.4pt)を大きく上回った。
            # 2回の未使用期間検証で必要量の方向が矛盾（0.99pt vs 2.75pt）
            # しており、期間ごとのサンプルノイズの影響が大きいとみられる
            # ため、増減どちらも見送り現状維持（-1.4pt）。より大きな
            # サンプル（複数期間合算）での再検証を待つ。
            # v3.3（6巡目・4/24〜8/24合算の過去最大サンプル）：矛盾していた
            # 2期間を合算した大サンプルで再検証したところ、明確に有意
            # （implied+2.28pt、現行1.4pt、ギャップ0.88pt）となり、
            # サンプルサイズ不足によるブレだったと判明。凍結解除のうえ
            # 半分反映で-1.8ptに引き上げる（旧-1.4pt）。
            # v3.4（7巡目・同一--calc-versionでの再検証）：引き続き有意
            # （implied+2.39pt、現行1.8pt、ギャップ0.59pt）のため、
            # 同じ考え方で-2.1ptに引き上げる（旧-1.8pt）。
            return -2.1, "昇級(前走非勝利)"

        # 直近5走（取得できた分だけ）の通算勝利数による勢い判定
        recent5 = [pr for pr in past_races[:5] if pr.finish > 0]
        win_count = sum(1 for pr in recent5 if pr.finish == 1)
        if win_count >= 3:
            return 1.5, f"昇級勢い(通算{win_count}勝)"

        # 前走1着：margin（2着馬との着差）で勝ち方を判定
        if prev.margin >= 0.5:
            return 1.5, "昇級(圧勝)"
        elif prev.margin >= 0.2:
            return 0.5, "昇級(順当勝ち)"
        else:
            return -0.75, "昇級(僅差勝ち)"
    return 0.0, ""


def calc_recent_form_penalty_nar(targets: list) -> tuple:
    """
    直近走（calc_phase1_narで使うtargetsと同一集合、最大3走）から、
    着差込みでNAR独自の近走不振ペナルティを算出する。

    戻り値：(ペナルティ値, ラベル文字列)。該当なしなら(0.0, "")。
    """
    poor_races = []
    for pr in targets:
        finish = getattr(pr, "finish", 0)
        if not finish or finish < 6:
            continue
        gap = 0.0 if finish == 1 else getattr(pr, "margin", 0.0)
        if gap <= NAR_FORM_MARGIN_OK:
            continue  # 着外でも僅差なら「不振」に数えない
        for threshold, pen in NAR_FORM_PENALTY_TIERS:
            if gap <= threshold:
                poor_races.append(pen)
                break

    if len(poor_races) < NAR_FORM_MIN_POOR_RACES:
        return 0.0, ""

    total = min(sum(poor_races), NAR_FORM_PENALTY_CAP)
    label = f"近走不振(着外{len(poor_races)}走・着差考慮)"
    return round(total, 3), label


def calc_race_point_nar(
    finish: int,
    margin: float,
    race_class: str,
    weight_carried: float = 55.0,
    field_size: int = 0,
    penalty_discount: float = 0.0,
) -> Optional[float]:
    """
    calculator.pyのcalc_race_point()と同一の計算式で、
    クラス基準値だけget_class_base_nar()に差し替えたNAR版。

    大差負けペナルティはNAR_LARGE_MARGIN_PENALTY（本ファイル定義・着差ベース）
    を使用する。相対順位ペナルティ（RELATIVE_FINISH_PENALTY）はJRA側の値を
    そのまま流用する（変更対象外）。

    penalty_discount: 0.0〜1.0。大差負け・最下位圏ペナルティ（大差負けペナルティ・
    最下位圏ペナルティ）にのみ適用する割引率（1.0=全額免除）。地区転入時、
    格上地区（南関東等）での大敗を割り引くために使用する（v1.9〜）。
    フィニッシュボーナス・着差ボーナス・斤量補正には影響しない。
    """
    if finish <= 0:
        return None

    base = get_class_base_nar(race_class)
    fin_bonus = FINISH_BONUS.get(finish, FINISH_BONUS_DEFAULT)

    margin_bonus = 0.0
    for threshold, bonus in MARGIN_BONUS_THRESHOLDS:
        if margin <= threshold:
            margin_bonus = bonus
            break

    large_margin_pen = 0.0
    if finish >= 6 and margin > NAR_LARGE_MARGIN_TRIGGER:
        for threshold, pen in NAR_LARGE_MARGIN_PENALTY:
            if margin > threshold:
                large_margin_pen = pen
                break

    relative_pen = 0.0
    if finish >= 6 and field_size >= 6:
        relative_ratio = finish / field_size
        for threshold, pen in RELATIVE_FINISH_PENALTY:
            if relative_ratio >= threshold:
                relative_pen = pen
                break

    discount_factor = max(0.0, min(1.0, 1.0 - penalty_discount))
    large_margin_pen *= discount_factor
    relative_pen *= discount_factor

    weight_correction = (BASE_WEIGHT - weight_carried) * 0.5

    point = base - fin_bonus - margin_bonus + large_margin_pen + relative_pen + weight_correction
    return round(point, 3)


# ──────────────────────────────────────────────
# Phase1（NAR版）
# ──────────────────────────────────────────────

def calc_phase1_nar(
    horse_name: str,
    horse_number: int,
    past_races: list,
    target_distance: int = 0,
    target_surface: str = "",
    current_class: str = "",
    target_venue: str = "",
    use_grade_bonus: bool = True,
    use_dist_aptitude: bool = True,
    use_momentum: bool = True,
    race_date: str = "",
) -> Phase1Result:
    """
    NAR用Phase1スコア計算（v0.1）。

    calculator.pyのcalc_phase1()との違い：
    - 地方走除外ロジックなし（全過去走がそのまま評価対象）
    - クラス基準値はget_class_base_nar()を使用（「組」補正込み）
    - 地区間転厩ボーナス（南関東→他地区等）を追加
    - 昇級勢い（calc_momentum_bonus_nar）はv2.7で追加移植済み
      （get_class_base_nar()の「組」補正を利用した精密な昇級判定）
    - 障害転向・馬齢限定OP読み替え・牝馬混合好走ボーナス・
      JRA固有レース名ペナルティは実装しない（v1スコープ外）
    """
    result = Phase1Result(horse_name=horse_name, horse_number=horse_number)

    # ── winner_time_secの無害化
    # 実データ検証の結果、NAR(nar.netkeiba.com/db.netkeiba.com)の
    # 過去走スクレイピングでは勝ち馬タイム(winner_time_sec)が信頼できない
    # （例：ダ1400mで68.2秒のようなあり得ない値）ことが判明した。
    # 一方でmargin（着差）は実データ照合の結果、正しい値が入っている。
    # calc_distance_aptitude_bonus等、calculator.pyから流用している関数の
    # 内部にもtime_sec-winner_time_secで着差を再計算する分岐があるため、
    # ここで一括してwinner_time_secを0にし、marginを使う分岐に統一する。
    for _pr in past_races:
        _pr.winner_time_sec = 0.0

    # ── 競走除外・競走中止・出走取消（finish<=0）を過去走から除外
    # これらはレースとして成立していない（着順が付いていない）ため、
    # 「直近3走」の枠を消費させるべきではない。ここで除いておかないと、
    # targets = past_races[:3] が除外/中止を1枠として数えてしまい、
    # 実質2走以下でしか評価されなくなる（休養日数・地区転入判定等、
    # past_races_all[:3]を使う他の下流処理にも同様に影響するため、
    # ここで一括して除いておく）。
    past_races = [pr for pr in past_races if getattr(pr, "finish", 0) and pr.finish > 0]

    past_races_all = list(past_races)

    # ── 地区転入判定用に対象地区を先に確定しておく（過去走ループ内で使用）
    target_region_for_discount = get_region_nar(target_venue) if target_venue else ""

    # ── 芝ダフィルター
    if target_surface and past_races:
        races_surf = [pr for pr in past_races if pr.surface == target_surface]
        if races_surf:
            past_races = races_surf

    # ── 格上挑戦除外
    # NARのクラスラダーはOP/A/B/Cの4pt刻み。JRAの2.0pt閾値だと
    # 隣接クラス（例：A→B、4pt差）だけで即除外されてしまい厳しすぎるため、
    # NARでは閾値を1ラダー分（4.0pt）に緩める。
    OVERCLASS_THRESHOLD_NAR = 4.0
    if current_class and past_races:
        current_base = get_class_base_nar(current_class)
        non_overclass = []
        overclass_excluded = 0
        for pr in past_races:
            pr_base = get_class_base_nar(pr.race_class)
            is_overclass = (current_base - pr_base) >= OVERCLASS_THRESHOLD_NAR and pr.finish >= 6
            if is_overclass:
                overclass_excluded += 1
            else:
                non_overclass.append(pr)
        if overclass_excluded > 0 and non_overclass:
            past_races = non_overclass
            past_races_all = list(past_races)
            result.note = (result.note + f" [格上挑戦除外{overclass_excluded}走]").strip()

    # ── 各走のポイント計算（最大3走）
    # 注意：NARのdb.netkeiba.comページはwinner_time_sec（勝ち馬タイム）の
    # スクレイピングが信頼できないことが実データ検証で判明した
    # （例：ダ1400mで68.2秒のようなあり得ない値が入る）。
    # 一方でmargin（着差）フィールドは実データ照合の結果、正しい値が
    # 入っていることを確認済みのため、NAR版ではtime_sec-winner_time_sec
    # の再計算を行わず、marginを直接信頼する。
    # （JRA版calc_phase1のようにwinner_time_secから差分を再計算する
    #   ロジックは採用しない）
    targets = past_races[:3]
    race_points = []
    penalty_notes = []
    discounted_race_count = 0
    raw_pen_total = 0.0  # 近走不振キャップ判定用：割引前の生ペナルティ合計
    for pr in targets:
        gap = 0.0 if pr.finish == 1 else pr.margin

        fs = getattr(pr, "field_size", 0)

        # 格上地区（南関東）での過去走は、大差負け・最下位圏ペナルティを割り引く。
        # 対象地区自体が格上地区の場合（南関東内での評価）は割引しない。
        pr_region = get_region_nar(getattr(pr, "venue", ""))
        is_discounted = (
            target_region_for_discount
            and target_region_for_discount not in TOUGHER_REGIONS_NAR
            and pr_region in TOUGHER_REGIONS_NAR
            and pr_region != target_region_for_discount
        )
        discount = TOUGHER_REGION_PENALTY_DISCOUNT if is_discounted else 0.0

        pt = calc_race_point_nar(pr.finish, gap, pr.race_class, pr.weight_carried, fs, penalty_discount=discount)
        if pt is not None:
            race_points.append(pt)
            if is_discounted:
                discounted_race_count += 1

            discount_tag = "・南関東割引" if is_discounted and discount > 0 else ""
            if pr.finish >= 6 and gap > NAR_LARGE_MARGIN_TRIGGER:
                for threshold, pen in NAR_LARGE_MARGIN_PENALTY:
                    if gap > threshold:
                        raw_pen_total += pen
                        shown_pen = pen * (1.0 - discount)
                        penalty_notes.append(f"大差負け({gap:.1f}秒){discount_tag}:+{shown_pen:.1f}")
                        break

            if pr.finish >= 6 and fs >= 6:
                ratio = pr.finish / fs
                for threshold, pen in RELATIVE_FINISH_PENALTY:
                    if ratio >= threshold:
                        raw_pen_total += pen
                        shown_pen = pen * (1.0 - discount)
                        penalty_notes.append(f"最下位圏({pr.finish}/{fs}頭){discount_tag}:+{shown_pen:.1f}")
                        break

    result.corrected_times = race_points
    result.valid_runs = len(race_points)

    if penalty_notes:
        result.note = (result.note + " [" + "/".join(penalty_notes) + "]").strip()
    if discounted_race_count > 0:
        result.note = (result.note + f" [地区転入(南関東{discounted_race_count}走の大敗ペナルティ免除)]").strip()

    if result.valid_runs == 0:
        result.note = (result.note + " 有効な走行データなし").strip()
        result.phase1_score = 9999.0
        return result

    weights = WEIGHT_RECENT[: result.valid_runs]
    total_w = sum(weights)
    ability_avg = sum(p * w for p, w in zip(race_points, weights)) / total_w
    result.ability_avg  = round(ability_avg, 3)
    result.best_time    = round(min(race_points), 3)
    result.phase1_score = result.ability_avg

    if result.valid_runs < 3 and not result.note:
        result.note = f"有効走数{result.valid_runs}走"

    # ── 近走不振ペナルティ（NAR独自：着差ベース・複数走傾向でのみ発動）
    # キャップ判定はraw_pen_total（南関東割引適用前の生ペナルティ合計）を使う。
    # 表示用penalty_notesは割引後の値なので、これをそのまま使うと南関東割引が
    # 効いた分だけ近走不振ペナルティが未キャップで素通りしてしまい、
    # 個別ペナルティを免除した意味が近走不振側で相殺されてしまう
    # （v1.9で発覚：レーザースペックル再検証で96.2に悪化する逆効果を確認）。
    #
    # v3.4で発覚した重大バグ修正：ここに近走不振ペナルティ単体の上限
    # （NAR_FORM_PENALTY_CAP、再キャリブレーションでv2.8の2.7から段階的に
    # 4.8まで引き上げ済み）とは別に、「他の生ペナルティ込みの合算上限」
    # として`PENALTY_CAP = 3.0`がハードコードされたまま放置されていた。
    # 他の生ペナルティ（大差負け＝現在全て0.0、最下位圏＝最大2.0）が0の
    # 大半の馬では、form_pen_capped = min(form_pen, 3.0 - 0) = min(form_pen, 3.0)
    # となり、NAR_FORM_PENALTY_CAPをどれだけ引き上げても実際の適用値は
    # 3.0で頭打ちになっていた（v2.9で3.0を超えて以降、増額分が丸ごと
    # 無効化されていたことになる）。NAR_FORM_PENALTY_CAP自体に連動する
    # NAR_COMBINED_PENALTY_CAPとして定義し直し、今後定数を調整しても
    # 同じ問題が再発しないようにした。
    form_pen, form_label = calc_recent_form_penalty_nar(targets)
    # v3.4：昇級(前走非勝利)と同様、近走不振×低走数の交互作用が2期間連続
    # （round6: implied+2.32pt、round7: implied+2.43pt）で有意と確認できた
    # ため、該当馬には追加ペナルティを加算する（半分反映）。
    if (form_pen > 0 and result.valid_runs <= NAR_DIST_LOW_RUNS_THRESHOLD):
        form_pen += NAR_FORM_LOW_RUNS_EXTRA_PENALTY
    if form_pen > 0:
        form_pen_capped = max(0.0, min(form_pen, NAR_COMBINED_PENALTY_CAP - raw_pen_total))
        if form_pen_capped > 0:
            result.phase1_score = round(result.phase1_score + form_pen_capped, 3)
            result.ability_avg  = round(result.ability_avg  + form_pen_capped, 3)
            result.best_time    = round(result.best_time    + form_pen_capped, 3)
            label_suffix = f"(cap:{form_pen_capped:.1f})" if form_pen_capped < form_pen else ""
            result.note = (result.note + f" [{form_label}{label_suffix}]").strip()
        elif form_pen > 0:
            result.note = (result.note + f" [{form_label}→cap済]").strip()

    # ── 出走間隔補正
    if race_date and past_races_all:
        from datetime import datetime as _dt
        try:
            _rd = race_date.strip()
            _m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", _rd)
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
                    result.best_time    = round(result.best_time    - 0.5, 3)
                    result.note = (result.note + f" [適度な休養({_days}日):-0.5]").strip()
                elif _days > 112:
                    result.phase1_score = round(result.phase1_score + 2.0, 3)
                    result.ability_avg  = round(result.ability_avg  + 2.0, 3)
                    result.best_time    = round(result.best_time    + 2.0, 3)
                    result.note = (result.note + f" [長期休養({_days}日):+2.0]").strip()
        except Exception:
            pass

    # ── 格ボーナス（Jpn1〜3等の交流重賞実績。calculator.pyの関数をそのまま流用）
    if use_grade_bonus:
        grade_b = calc_grade_bonus(past_races_all, age_limited=False, classic_distance=False)
        if grade_b > 0:
            result.phase1_score = round(result.phase1_score - grade_b, 3)
            result.ability_avg  = round(result.ability_avg  - grade_b, 3)
            result.best_time    = round(result.best_time    - grade_b, 3)
            result.note = (result.note + f" [格B:-{grade_b:.1f}]").strip()

    # ── 昇級勢い（v2.7追加。get_class_base_nar()の「組」補正込みで判定）
    if use_momentum and current_class:
        momentum_pt, momentum_label = calc_momentum_bonus_nar(past_races_all, current_class)
        # v3.3：昇級(前走非勝利)×低走数の交互作用が有意と確認できたため、
        # 該当馬には追加ペナルティを加算する（半分反映）。
        if (momentum_label == "昇級(前走非勝利)"
                and result.valid_runs <= NAR_DIST_LOW_RUNS_THRESHOLD):
            momentum_pt -= NAR_MOMENTUM_LOW_RUNS_EXTRA_PENALTY   # ペナルティ強化＝スコアに加算する方向
            momentum_label = momentum_label + "+低走数加算"
        if momentum_pt != 0:
            result.phase1_score = round(result.phase1_score - momentum_pt, 3)
            result.ability_avg  = round(result.ability_avg  - momentum_pt, 3)
            result.best_time    = round(result.best_time    - momentum_pt, 3)
            result.note = (result.note + f" [{momentum_label}:{momentum_pt:+.2f}]").strip()

    # ── 距離適性ボーナス（calculator.pyの関数をそのまま流用。ただしv2.8で
    #    NAR専用のNAR_DIST_GOOD_FINISH_BONUSをbonus_table引数で渡すように
    #    変更。JRA側（calculator.py）の挙動には影響しない）
    if use_dist_aptitude and target_distance > 0:
        dist_bonus, dist_label = calc_distance_aptitude_bonus(
            past_races_all, target_distance,
            target_surface=target_surface,
            all_past_races=past_races_all,
            bonus_table=NAR_DIST_GOOD_FINISH_BONUS,
        )
        # v2.8：距離好走1着×低走数（有効走数<=NAR_DIST_LOW_RUNS_THRESHOLD）で
        # 追加の交互作用効果がrecalibrate.pyで確認されたため、該当馬には
        # 追加ボーナスを加算する（半分反映）。
        # v3.2：距離好走3着×低走数も2期間連続（6/24-7/23, 4/24-6/23）で
        # 有意と確認できたため、同様に追加ボーナスを加算する。
        # v3.3：距離好走2着×低走数も有意となったため、同様に追加する。
        if (dist_label.startswith("距離好走1着")
                and result.valid_runs <= NAR_DIST_LOW_RUNS_THRESHOLD):
            dist_bonus = round(dist_bonus + NAR_DIST_GOOD_FINISH_LOW_RUNS_EXTRA_1ST, 3)
            dist_label = dist_label + "+低走数加算"
        elif (dist_label.startswith("距離好走2着")
                and result.valid_runs <= NAR_DIST_LOW_RUNS_THRESHOLD):
            dist_bonus = round(dist_bonus + NAR_DIST_GOOD_FINISH_LOW_RUNS_EXTRA_2ND, 3)
            dist_label = dist_label + "+低走数加算"
        elif (dist_label.startswith("距離好走3着")
                and result.valid_runs <= NAR_DIST_LOW_RUNS_THRESHOLD):
            dist_bonus = round(dist_bonus + NAR_DIST_GOOD_FINISH_LOW_RUNS_EXTRA_3RD, 3)
            dist_label = dist_label + "+低走数加算"
        result.phase1_score = round(result.phase1_score - dist_bonus, 3)
        result.ability_avg  = round(result.ability_avg  - dist_bonus, 3)
        result.best_time    = round(result.best_time    - dist_bonus, 3)
        if dist_label:
            result.note = (result.note + f" [{dist_label}]").strip()

    # ── 地区転入ボーナスについて
    # v1.9でper-race免除方式（過去走ループ内でのTOUGHER_REGION_PENALTY_DISCOUNT
    # 適用）に統合済み。ここでの末尾一律加点は廃止（二重計上を避けるため）。
    # 免除が発動した場合のノートは過去走ループ内で
    # "[地区転入(南関東N走の大敗ペナルティ免除)]" として既に付与されている。

    # ── 中央(JRA)からの転入ボーナス
    # 直近3走のうちJRA(is_local=False)での出走が含まれる場合、
    # 中央での大敗は地方Cクラス等への適性を測る参考にならないことが多いため
    # （中央未勝利で大敗していても地方なら即通用するケースが多い）、
    # その分の評価を割り引く。地区転入ボーナスとは独立して加算可能。
    if past_races_all:
        jra_count = sum(1 for pr in past_races_all[:3] if not pr.is_local)
        if jra_count > 0:
            central_bonus = min(
                jra_count * CENTRAL_TRANSFER_BONUS_PER_RACE,
                CENTRAL_TRANSFER_BONUS_MAX,
            )
            central_label_extra = ""
            # v2.8：低走数馬ではrecalibrate.pyで過大評価（implied-4.22pt
            # 相当）が示されたため、該当馬はボーナスを割り引く（半分反映。
            # 0未満（ペナルティ化）にはしない＝フロア0）。
            if result.valid_runs <= CENTRAL_TRANSFER_LOW_RUNS_THRESHOLD:
                before = central_bonus
                central_bonus = max(0.0, central_bonus - CENTRAL_TRANSFER_LOW_RUNS_DISCOUNT)
                if central_bonus != before:
                    central_label_extra = "・低走数割引"
            result.phase1_score = round(result.phase1_score - central_bonus, 3)
            result.ability_avg  = round(result.ability_avg  - central_bonus, 3)
            result.best_time    = round(result.best_time    - central_bonus, 3)
            result.note = (result.note + f" [中央転入(JRA経験{jra_count}走{central_label_extra}):-{central_bonus:.1f}]").strip()

    return result


# ──────────────────────────────────────────────
# Phase1一括計算（全出走馬）
# ──────────────────────────────────────────────

def calc_phase1_all_nar(
    horses: list,
    target_distance: int = 0,
    target_surface: str = "",
    current_class: str = "",
    target_venue: str = "",
    race_date: str = "",
) -> list:
    """
    出走馬リスト（scraper_nar.Horse）全頭についてcalc_phase1_narを実行する。
    """
    results = []
    for h in horses:
        r = calc_phase1_nar(
            horse_name=h.name,
            horse_number=h.number,
            past_races=h.past_races,
            target_distance=target_distance,
            target_surface=target_surface,
            current_class=current_class,
            target_venue=target_venue,
            race_date=race_date,
        )
        results.append(r)
    return results


# ── NAR版 Phase2（v2.0〜）：単発好走への過大評価を防ぐ確認ゲート付き ──────
# JRA側calc_phase2()のbest_bonus（=(ability_avg-best_time)×BEST_BONUS_FACTOR）は
# 直近3走中「一番良かった1走」とのギャップだけを見るため、他2走が凡走でも
# 単発の好走だけで大きな加点が乗ってしまう。
# 事後検証で発覚：盛岡2026-07-21 10R・サンリットアワーズ（凡走2走+水沢での
# そこそこの好走1走）がbest_bonus=+2.6の加点を受け、実際は8人気7着の凡走
# だったにも関わらず、地区転入・クラス表記修正で地力どおりの評価に近づいた
# レーザースペックル（実際2人気1着・3走とも横並びの安定した数値）を予想
# 順位で上回ってしまっていた。
# 対応：直近3走のうちbest_timeから見てNAR_BEST_BONUS_CONFIRM_MARGIN以内に
# 収まっている走が2走以上なければ、単発の好走とみなしbest_bonusを発動させ
# ない（0にする）。2走以上で好走傾向が裏付けられている場合のみ、従来どおり
# ability_avgとbest_timeの差分から計算する。
# NAR_BEST_BONUS_CONFIRM_MARGIN・BEST_BONUS_FACTOR・INSTABILITY_FACTORは
# いずれも暫定値（要継続検証）。
NAR_BEST_BONUS_CONFIRM_MARGIN = 2.5  # この差以内なら「好走傾向を裏付ける1走」とみなす


def calc_phase2_nar(phase1) -> "Phase2Result":
    """
    calculator.pyのcalc_phase2()のNAR版。

    v2.1で「2走以上が一定水準以内でないとbest_bonusが発動しない」確認
    ゲートを追加したが、v2.5で撤廃した。地方競馬では「直近3走中2走が
    凡走・1走だけ好走」という単発好走馬が実際に好走・勝利するケースが
    実運用上ひんぱんに確認されたため（こうすけさんの実戦知見に基づく
    判断）。calc_phase2()と実質的に同じ計算になるが、将来的にNAR側で
    別の調整を入れる可能性に備えて、関数自体はNAR専用のまま残している。

    v2.7：有効走数の足切りラインを「3走未満は完全スキップ」から
    「2走未満は完全スキップ」に緩和。2026/8/13大井3R・タツノロマンス
    （有効走数2走・前走で現級の一段上の組を勝ったばかり・単勝1.3倍の
    1番人気が実際に1着）の事後検証で、有効走数2走の馬がbest_bonus
    （好走傾向の裏付けボーナス）を一切受けられず、走数の多い馬に比べて
    不利になっていた実害が確認されたため。standard deviation（std_dev）は
    データ点が2つあれば計算可能（statistics.stdevはn>=2で動作）なので、
    技術的な制約はない。データ点1つ（valid_runs==1）の場合は分散が
    定義できないため、引き続きPhase1スコアそのまま（Phase2調整なし）とする。
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

    if phase1.valid_runs < 2:
        r.phase2_score = phase1.phase1_score
        r.std_dev = 0.0
        return r

    r.std_dev = round(statistics.stdev(phase1.corrected_times), 3)

    best_gap = phase1.ability_avg - phase1.best_time
    r.best_bonus = round(best_gap * BEST_BONUS_FACTOR, 3)

    r.instability_penalty = round(r.std_dev * INSTABILITY_FACTOR, 3)
    r.phase2_score = round(
        phase1.phase1_score - r.best_bonus + r.instability_penalty, 3
    )
    return r


def calc_phase2_all_nar(phase1_results: list) -> list:
    return [calc_phase2_nar(r) for r in phase1_results]


# ──────────────────────────────────────────────
# NAR版 競馬場・騎手適性（Phase3相当）
# ──────────────────────────────────────────────
#
# 設計方針（ユーザー確認済み）：
# - 回り適性（左右）：NARでは無視する。地方の馬は基本的に同じ競馬場
#   （または同一地区内）でしか走らないため、出走馬全員がほぼ同一条件であり、
#   回り適性の差は無視して良い（交流重賞での他地区馬混走は例外だが、
#   JRAほど回り差の影響は大きくないと判断）。
#   → turn_bonusは常に0.0固定とする。
# - 騎手：JRA側の全国騎手ランクテーブル（_lookup_jockey_bonus）は
#   地方騎手をほぼカバーしないため使えない。代わりに「場ごとのリーディング
#   ランキング」を人手で登録するテーブル方式にする。
#
# NAR_VENUE_JOCKEY_LEADING の更新方法：
#   netkeiba地方騎手リーディング（https://db.netkeiba.com/?pid=jockey_leading_nar）
#   や南関競馬公式サイト（https://www.nankankeiba.com/leading_kis/...）等で
#   現在の上位騎手を確認し、以下の辞書に手動で追記する。
#   自動スクレイピングはリーディングページがJS絞り込み/AJAX形式のため
#   v1では見送り、手動更新運用とする。
#
# 順位帯とボーナス値（JRA版_lookup_jockey_bonusのテーブル感覚に合わせた暫定値）：
#   1〜3位   : +1.5pt
#   4〜10位  : +0.8pt
#   11〜20位 : +0.4pt
#   圏外/未登録: 0pt
NAR_VENUE_JOCKEY_LEADING: dict = {
    # netkeiba地方騎手リーディングより取得（2026年7月時点、機械的トップ10抽出）。
    # 注意：南関東・東海・兵庫の各場は地区内で騎乗が重複する騎手が多い
    #   （例：大井所属の笹川翼・矢野貴之が浦和/船橋/川崎の上位にも重複登場）。
    #   これは「その場での実際の騎乗成績」をそのまま採用した結果であり、
    #   意図的な仕様（会場ごとのリーディング＝所属地区に関わらずその場で
    #   よく勝っている騎手、という定義を採用）。
    "盛岡": {
        "山本聡哉": 1, "高松亮": 2, "高橋悠里": 3, "山本聡紀": 4, "山本政聡": 5,
        "村上忍": 6, "菅原辰徳": 7, "佐々木志音": 8, "鈴木祐": 9, "阿部英俊": 10,
    },
    "水沢": {
        "村上忍": 1, "山本聡哉": 2, "山本政聡": 3, "高橋悠里": 4, "高松亮": 5,
        "小林凌": 6, "菅原辰徳": 7, "塚本涼人": 8, "佐々木志音": 9, "坂井瑛音": 10,
    },
    "浦和": {
        "笹川翼": 1, "野畑凌": 2, "中山遥人": 3, "福原杏": 4, "町田直希": 5,
        "及川烈": 6, "岡村健司": 7, "見越彬央": 8, "和田譲治": 9, "室陽一朗": 10,
    },
    "大井": {
        "矢野貴之": 1, "笹川翼": 2, "安藤洋一": 3, "和田譲治": 4, "藤田凌": 5,
        "藤本現暉": 6, "西啓太": 7, "吉井章": 8, "達城龍次": 9, "御神本訓史": 10,
    },
    "船橋": {
        "本田正重": 1, "笹川翼": 2, "矢野貴之": 3, "篠谷葵": 4, "岡村健司": 5,
        "御神本訓史": 6, "本橋孝太": 7, "山中悠希": 8, "川島正太郎": 9, "野畑凌": 10,
    },
    "川崎": {
        "野畑凌": 1, "矢野貴之": 2, "笹川翼": 3, "新原周馬": 4, "町田直希": 5,
        "佐野遥久": 6, "御神本訓史": 7, "桜井光輔": 8, "古岡勇樹": 9, "本田正重": 10,
    },
    "笠松": {
        "渡辺竜也": 1, "筒井勇介": 2, "塚本征吾": 3, "明星晴大": 4, "望月洵輝": 5,
        "松本一心": 6, "東川慎": 7, "馬渕繁治": 8, "藤原幹生": 9, "高木健": 10,
    },
    "名古屋": {
        "塚本征吾": 1, "望月洵輝": 2, "今井貴大": 3, "加藤聡一": 4, "丸野勝虎": 5,
        "大畑慧悟": 6, "大畑雅章": 7, "渡辺竜也": 8, "木之前葵": 9, "細川智史": 10,
    },
    "園田": {
        "吉村智洋": 1, "小牧太": 2, "広瀬航": 3, "下原理": 4, "田野豊三": 5,
        "山本咲希到": 6, "佐々木世麗": 7, "杉浦健太": 8, "大山真吾": 9, "小谷哲平": 10,
    },
    "姫路": {
        "吉村智洋": 1, "小牧太": 2, "広瀬航": 3, "下原理": 4, "田野豊三": 5,
        "杉浦健太": 6, "小谷哲平": 7, "佐々木世麗": 8, "山本咲希到": 9, "笹田知宏": 10,
    },
    "門別": {
        "落合玄太": 1, "桑村真明": 2, "石川倭": 3, "小野楓馬": 4, "阿部龍": 5,
        "岩橋勇二": 6, "服部茂史": 7, "宮内勇樹": 8, "藤田凌駕": 9, "井上瑛太": 10,
    },
    "金沢": {
        "栗原大河": 1, "中島龍也": 2, "青柳正義": 3, "柴田勇真": 4, "加藤翔馬": 5,
        "吉原寛人": 6, "吉田晃浩": 7, "松戸政也": 8, "田知弘久": 9, "魚住謙心": 10,
    },
    "高知": {
        "宮川実": 1, "赤岡修次": 2, "永森大智": 3, "多田羅誠也": 4, "岡村卓弥": 5,
        "岡遼太郎": 6, "山崎雅由": 7, "吉原寛人": 8, "井上瑛太": 9, "城野慈尚": 10,
    },
    "佐賀": {
        "飛田愛斗": 1, "石川慎将": 2, "山口勲": 3, "長谷川蓮": 4, "出水拓人": 5,
        "竹吉徹": 6, "金山昇馬": 7, "山下裕貴": 8, "山田義貴": 9, "林悠翔": 10,
    },
    # 帯広（ばんえい競走）は対象外（方針確認済み）。
}

JOCKEY_LEADING_BONUS_TIERS = [
    (3, 1.5),
    (10, 0.8),
    (20, 0.4),
]


def get_jockey_leading_bonus_nar(venue: str, jockey_name: str) -> float:
    """
    NAR_VENUE_JOCKEY_LEADINGに登録された順位からボーナス値を返す。
    未登録の場・騎手の場合は0.0（ボーナスなし）。
    """
    venue_table = NAR_VENUE_JOCKEY_LEADING.get(venue)
    if not venue_table:
        return 0.0
    rank = venue_table.get(jockey_name)
    if rank is None:
        # 表記ゆれ対応（全角英数字統一）
        norm_name = unicodedata.normalize("NFKC", jockey_name).strip()
        for name, r in venue_table.items():
            if unicodedata.normalize("NFKC", name).strip() == norm_name:
                rank = r
                break
    if rank is None:
        return 0.0
    for threshold, bonus in JOCKEY_LEADING_BONUS_TIERS:
        if rank <= threshold:
            return bonus
    return 0.0


def calc_venue_jockey_stats_nar(
    horse_name: str,
    horse_number: int,
    past_races: list,
    target_venue: str,
    current_jockey: str,
    target_track_cond: str = "",
) -> VenueJockeyStats:
    """
    calculator.pyのcalc_venue_jockey_stats()のNAR版。
    - track_bonus（馬場適性）はcalculator.pyと同じロジック
      （過去走ベースの汎用計算のため、地方競馬でもそのまま成立する）
    - venue_bonus（競馬場実績）・turn_bonus（回り適性）は常に0固定
      （NARでは同一地区内対戦が基本のため、意味を持たない設計判断）
    - jockey_bonusはget_jockey_leading_bonus_nar()（場ごとのリーディング表）を使用
    """
    stats = VenueJockeyStats(horse_name=horse_name, horse_number=horse_number)

    # ① 馬場状態適性ボーナス（calculator.pyと同一ロジック）
    if target_track_cond and past_races:
        is_bad_track = target_track_cond in TRACK_BAD
        if is_bad_track:
            bad_runs = [pr for pr in past_races if pr.condition in TRACK_BAD and pr.finish > 0]
            if bad_runs:
                good_in_bad = [pr for pr in bad_runs if pr.finish <= 3 and pr.margin <= 0.5]
                bad_in_bad  = [pr for pr in bad_runs if pr.finish >= 6]
                if good_in_bad and not bad_in_bad:
                    stats.track_bonus = CONDITION_BONUS_TABLE["track_bad"]
                elif bad_in_bad and not good_in_bad:
                    stats.track_bonus = -CONDITION_BONUS_TABLE["track_bad"]
                else:
                    stats.track_bonus = 0.0

    # ② 競馬場適性ボーナス：NARでは無視（常に0固定）
    # 交流戦を除けば地方の馬は基本的に同一地区内（多くは自場）でしか
    # 対戦しないため、「特定の競馬場への適性」という切り口自体があまり
    # 意味を持たない（＝ほぼ全走がその馬の"地元"での実績になり、通算の
    # 実力評価と重複してしまう）というユーザー判断により撤廃する。
    # 回り適性（turn_bonus）と同じ理由・同じ扱い。
    stats.venue_bonus = 0.0

    # ③ 回り適性：NARでは無視（常に0固定）
    stats.turn_bonus = 0.0

    # ④ 騎手ボーナス：場ごとのリーディング表を参照
    stats.jockey_bonus = get_jockey_leading_bonus_nar(target_venue, current_jockey)

    return stats


def apply_venue_jockey_bonus_nar(
    phase2_results: list,
    horses: list,
    target_venue: str,
    all_past_races: dict,
    target_track_cond: str = "",
) -> list:
    """
    calculator.pyのapply_venue_jockey_bonus()のNAR版。
    calc_venue_jockey_stats_nar()を使う以外はロジック同一。
    """
    adjusted = []
    horse_map = {h.number: h for h in horses}

    for r in phase2_results:
        new_r = copy.copy(r)
        h = horse_map.get(r.horse_number)
        past = all_past_races.get(r.horse_number, [])

        if h and target_venue:
            stats = calc_venue_jockey_stats_nar(
                r.horse_name, r.horse_number, past,
                target_venue, h.jockey,
                target_track_cond=target_track_cond,
            )
            total_bonus = stats.track_bonus + stats.venue_bonus + stats.jockey_bonus  # turn_bonusは常に0
            new_r.phase2_score = round(r.phase2_score - total_bonus, 3)

            parts = []
            if stats.track_bonus != 0:
                parts.append(f"馬場{stats.track_bonus:+.1f}")
            if stats.venue_bonus != 0:
                parts.append(f"会場{stats.venue_bonus:+.1f}")
            if stats.jockey_bonus != 0:
                parts.append(f"リーディング{stats.jockey_bonus:+.2f}")
            if parts:
                new_r.note = (r.note + f" [{'/'.join(parts)}]").strip()

        adjusted.append(new_r)

    return sorted(adjusted, key=lambda x: x.phase2_score)
