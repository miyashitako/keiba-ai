"""
NAR版 本番相当テストスクリプト（v0.1）

実際のnetkeiba.comから出走表・過去走を取得し、Phase1〜4まで
一気通貫で実行して最終ランキングを表示する。

test_nar.py（①〜④の個別動作確認）とは違い、こちらは
「実際に今日・明日の地方競馬レースを予想する」実運用に近い形。

Phase5（パドック・枠バイアス・重馬場適性の人間確認）は
現地でパドックを見ながら入力する前提のため、このスクリプトでは
Phase4までの自動計算結果を表示するところまでとする
（引き継ぎプロンプトの設計通り、Phase5は人間確認ステップ）。

使い方：
1. 下の「★設定値」を実際に開催があるレースに書き換える
2. python run_nar_prediction.py を実行
3. 事前にNAR_VENUE_JOCKEY_LEADINGへ対象場のリーディング上位騎手を
   登録しておくと、騎手ボーナスがより正確に働く（未登録でも動作はする）
"""

import datetime

import calculator_nar as cn
from scraper_nar import fetch_all_horses_nar, build_nar_race_id


# ──────────────────────────────────────────────
# ★設定値（実際に開催があるレースに書き換える）
# ──────────────────────────────────────────────
VENUE = "大井"
RACE_DATE = datetime.date(2026, 7, 13)
RACE_NO = 1
PAST_LIMIT = 5   # 過去走取得数（最大3走まで計算に使用するが、休養期間判定等のため多めに取得）

# ★診断用：ここに馬番を入れると、その馬の過去走データを生表示する
# （スコアが直感と合わない馬がいた場合に、元データが正しいか確認するため）
DEBUG_HORSE_NUMBERS = []


def main():
    race_id = build_nar_race_id(VENUE, RACE_DATE, RACE_NO)
    print("=" * 70)
    print(f"対象レース: {VENUE} {RACE_DATE} {RACE_NO}R (race_id={race_id})")
    print("=" * 70)

    print("\n出走表・過去走を取得中...（頭数が多いと数十秒かかります）")
    race_info, horses = fetch_all_horses_nar(
        VENUE, RACE_DATE, RACE_NO, past_limit=PAST_LIMIT
    )

    print(f"\nレース名   : {race_info.race_name}")
    print(f"競馬場     : {race_info.venue}")
    print(f"距離/馬場  : {race_info.surface}{race_info.distance}m ({race_info.direction})")
    print(f"馬場状態   : {race_info.track_cond}")
    print(f"クラス     : {race_info.race_class}")
    print(f"出走頭数   : {len(horses)}")

    no_past_data = [h.name for h in horses if not h.past_races]
    if no_past_data:
        print(f"\n[WARN] 過去走データが0件の馬: {no_past_data}")
        print("       （新馬・地方転入直後等の可能性。有効走数0としてPhase1で処理されます）")

    if DEBUG_HORSE_NUMBERS:
        print("\n" + "=" * 70)
        print("■ 診断：指定馬番の過去走生データ")
        print("=" * 70)
        for h in horses:
            if h.number in DEBUG_HORSE_NUMBERS:
                print(f"\n  {h.number}番 {h.name}（過去{len(h.past_races)}走）")
                for pr in h.past_races:
                    print(
                        f"    {pr.date} {pr.venue} {pr.race_class} "
                        f"{pr.surface}{pr.distance} {pr.finish}着/{pr.field_size}頭 "
                        f"タイム={pr.time_sec} 勝馬タイム={pr.winner_time_sec} "
                        f"着差={pr.margin} 斤量={pr.weight_carried} 馬場={pr.condition}"
                    )

    print("\n" + "=" * 70)
    print("Phase1〜2 計算中...")
    print("=" * 70)
    phase1_results = cn.calc_phase1_all_nar(
        horses,
        target_distance=race_info.distance,
        target_surface=race_info.surface,
        current_class=race_info.race_class,
        target_venue=VENUE,
        race_date=race_info.race_date,
    )
    phase2_results = cn.calc_phase2_all(phase1_results)

    print("\n" + "=" * 70)
    print("Phase3（競馬場・馬場・騎手適性）計算中...")
    print("=" * 70)
    if VENUE not in cn.NAR_VENUE_JOCKEY_LEADING:
        print(f"[INFO] {VENUE}のリーディング表が未登録です。騎手ボーナスは0で計算されます。")
        print(f"       事前に cn.NAR_VENUE_JOCKEY_LEADING['{VENUE}'] = {{...}} を設定すると反映されます。")

    all_past_races = {h.number: h.past_races for h in horses}
    adjusted = cn.apply_venue_jockey_bonus_nar(
        phase2_results, horses, VENUE, all_past_races,
        target_track_cond=race_info.track_cond,
    )

    print("\n" + "=" * 70)
    print("Phase4（レース解像度指数）計算中...")
    print("=" * 70)
    phase4 = cn.calc_phase4(adjusted)

    print("\n" + "=" * 70)
    print("■ 最終ランキング（Phase1〜3統合スコア／小さいほど高評価）")
    print("=" * 70)
    for i, r in enumerate(adjusted, 1):
        h = next((h for h in horses if h.number == r.horse_number), None)
        jockey = h.jockey if h else "?"
        print(f"  {i:2d}位 {r.horse_number:2d}番 {r.horse_name:12s} "
              f"({jockey:8s}) score={r.phase2_score:6.2f}")
        if r.note:
            print(f"        note: {r.note}")

    print("\n" + "=" * 70)
    print("■ Phase4：レース解像度指数")
    print("=" * 70)
    print(f"  判定       : {phase4.judgment}")
    print(f"  推奨買い目 : {phase4.recommended_bet}")
    print(f"  1-3位差    : {phase4.gap_1_3}")
    print(f"  1強か      : {phase4.is_dominant}")
    print(f"  上位3頭    : {phase4.top3_horses}")
    print(f"  相手候補   : {phase4.rival_range}")

    print("\n" + "=" * 70)
    print("テスト完了。")
    print("Phase5（パドック評価・枠バイアス・重馬場適性の人間確認）は")
    print("現地/映像でのパドックチェック後、別途 apply_phase5() で実施してください。")
    print("=" * 70)


if __name__ == "__main__":
    main()
