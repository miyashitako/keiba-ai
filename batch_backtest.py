"""
batch_backtest.py（v0.1新規作成）

日付範囲・競馬場（JRA/NAR）を指定して、過去の確定済みレースをまとめて
Phase1〜3で予想計算し、実際の着順と突き合わせてGoogle Sheetsに記録する
バッチスクリプト。app.py・app_nar.pyの「答え合わせモード」を、Streamlitの
ボタン操作なしで複数レース分まとめて自動実行するためのもの。

────────────────────────────────────────────
■ 前提
────────────────────────────────────────────
- scraper.py（v1.8）でfetch_past_racesがlimit=None（生涯全走取得）に対応し、
  filter_past_races_before()で「対象レースの日付より前」だけに絞り込める
  ようになったため、直近の1走に限らず、任意の過去日付のレースを正しく
  （データリークなしで）検証できる。
- results_logger.py（v0.2）はStreamlit外の素のスクリプトからも呼び出せる
  よう、.streamlit/secrets.tomlを直接読む経路を持っている。実行前に
  results_logger.py冒頭のセットアップ手順（Google Cloud側の準備）と、
  .streamlit/secrets.tomlの作成を済ませておくこと。

────────────────────────────────────────────
■ 使い方
────────────────────────────────────────────
  # JRA：2026年7月1日〜7月31日、東京・中山のみ
  python batch_backtest.py --system jra --start 2026-07-01 --end 2026-07-31 \\
      --venues 東京,中山

  # NAR：2026年8月1日〜8月13日、大井のみ
  python batch_backtest.py --system nar --start 2026-08-01 --end 2026-08-13 \\
      --venues 大井

  # まずは書き込まずに件数だけ確認したい場合
  python batch_backtest.py --system nar --start 2026-08-01 --end 2026-08-13 \\
      --venues 大井 --dry-run

  # 途中で中断した場合、同じコマンドを再実行すれば
  # （--resume-file で指定したチェックポイントファイルにより）
  # 処理済みのrace_idは自動的にスキップされ、続きから再開される
  # （デフォルト: .batch_backtest_progress_{system}.txt）

────────────────────────────────────────────
■ スコープの簡略化について
────────────────────────────────────────────
app.py・app_nar.pyの通常のインタラクティブ予想は Phase1→2→3→（展開・
トラックバイアス）→Phase5（パドック等・人力）まで一通り適用しているが、
本バッチではPhase1→2→3（競馬場・騎手適性）までとしている。
- Phase5（パドック評価等）は人間の目視が前提のため自動化不可。
- 展開・トラックバイアス補正は開催週次計算等が絡みやや複雑なため、
  今回のスコープからは省略した（統計的キャリブレーションの主目的である
  「Phase1〜2の各ボーナス定数の妥当性検証」には現状のスコープで十分と
  判断。必要になったら拡張する）。

★このサンドボックス環境からはnetkeiba・Google SheetsのAPIエンドポイントに
ネットワーク到達できないため、実際の動作は未検証。まずは1日・1会場など
小さい範囲で --dry-run から試し、件数・内容が妥当か確認してから本格実行
してください。
"""

import argparse
import datetime
import os
import sys
import time

from scraper import (
    fetch_all_horses_backtest,
    fetch_jra_race_ids_for_date,
)
from calculator import calc_phase1, calc_phase2_all, apply_venue_jockey_bonus

from scraper_nar import (
    fetch_all_horses_nar_backtest,
    fetch_nar_race_ids_for_date,
)
import calculator_nar as cn

from results_logger import log_backtest_results


JRA_VENUES = ["東京", "中山", "阪神", "京都", "中京", "小倉", "新潟", "福島", "札幌", "函館"]
NAR_VENUES = [
    "盛岡", "水沢", "浦和", "船橋", "大井", "川崎", "笠松", "名古屋",
    "園田", "姫路", "高知", "佐賀", "門別", "金沢",
]


def _daterange(start: datetime.date, end: datetime.date):
    d = start
    while d <= end:
        yield d
        d += datetime.timedelta(days=1)


def _load_progress(path: str) -> set:
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def _mark_done(path: str, race_id: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{race_id}\n")


def _process_jra_race(race_id: str, past_limit: int, horse_cache: dict, sleep_sec: float):
    """1レース分：取得→Phase1〜3計算→戻り値は(race_info, ranking, horses)"""
    race_info, horses = fetch_all_horses_backtest(
        race_id, past_limit=past_limit, horse_cache=horse_cache, sleep_sec=sleep_sec,
    )
    if len(horses) < 3:
        return None

    p1_results = [
        calc_phase1(
            h.name, h.number, h.past_races,
            target_distance=race_info.distance,
            target_surface=race_info.surface,
            current_class=race_info.race_class,
            use_grade_bonus=True,
            use_momentum=True,
            use_dist_aptitude=True,
            age_limited=race_info.is_age_limited,
            classic_distance=race_info.is_classic_distance,
            race_date=race_info.race_date or "",
            horse_sex=h.sex,
            is_female_only_race=getattr(race_info, "is_female_only", False),
            race_name=race_info.race_name,
        )
        for h in horses
    ]
    p2_results = calc_phase2_all(p1_results)

    ranking = p2_results
    if race_info.venue:
        all_past = {h.number: h.past_races for h in horses}
        ranking = apply_venue_jockey_bonus(
            p2_results, horses, race_info.venue, all_past,
            target_track_cond=race_info.track_cond or "",
        )
    ranking = sorted(ranking, key=lambda r: r.phase2_score)
    return race_info, ranking, horses


def _process_nar_race(venue: str, race_date: datetime.date, race_no: int,
                       past_limit: int, horse_cache: dict, sleep_sec: float):
    race_info, horses = fetch_all_horses_nar_backtest(
        venue, race_date, race_no, past_limit=past_limit,
        horse_cache=horse_cache, sleep_sec=sleep_sec,
    )
    if len(horses) < 3:
        return None

    p1_results = cn.calc_phase1_all_nar(
        horses,
        target_distance=race_info.distance,
        target_surface=race_info.surface,
        current_class=race_info.race_class,
        target_venue=venue,
        race_date=race_info.race_date,
    )
    p2_results = cn.calc_phase2_all_nar(p1_results)

    all_past = {h.number: h.past_races for h in horses}
    ranking = cn.apply_venue_jockey_bonus_nar(
        p2_results, horses, venue, all_past,
        target_track_cond=race_info.track_cond,
    )
    return race_info, ranking, horses


def run_jra(start: datetime.date, end: datetime.date, venues: "list | None",
            past_limit: int, sleep_sec: float, dry_run: bool, progress_path: str):
    done = _load_progress(progress_path)
    horse_cache: dict = {}
    total_logged = 0

    for d in _daterange(start, end):
        try:
            race_ids = fetch_jra_race_ids_for_date(d)
        except Exception as e:
            print(f"[{d}] 日程取得に失敗: {e}")
            continue
        if not race_ids:
            print(f"[{d}] 開催なし（またはページ構造不一致の可能性）")
            continue
        print(f"[{d}] {len(race_ids)}レース検出")

        for race_id in race_ids:
            if race_id in done:
                continue
            try:
                result = _process_jra_race(race_id, past_limit, horse_cache, sleep_sec)
            except Exception as e:
                print(f"  race_id={race_id} 処理失敗: {e}")
                continue
            if result is None:
                _mark_done(progress_path, race_id)
                continue
            race_info, ranking, horses = result

            # venue絞り込み（レース情報取得後でないと分からないため事後フィルタ）
            if venues and race_info.venue not in venues:
                _mark_done(progress_path, race_id)
                continue

            if not dry_run:
                ok, msg = log_backtest_results(
                    system="JRA", race_info=race_info, horses=horses,
                    display_results=ranking, phase5_applied=False,
                )
                print(f"  {race_info.venue} {race_info.race_name}: {msg}")
            else:
                print(f"  [dry-run] {race_info.venue} {race_info.race_name}"
                      f"（{len(ranking)}頭）を記録予定")

            total_logged += 1
            _mark_done(progress_path, race_id)
            time.sleep(sleep_sec)

    print(f"完了：{total_logged}レース処理しました。")


def run_nar(start: datetime.date, end: datetime.date, venues: "list | None",
            past_limit: int, sleep_sec: float, dry_run: bool, progress_path: str):
    done = _load_progress(progress_path)
    horse_cache: dict = {}
    total_logged = 0
    target_venues = venues if venues else NAR_VENUES

    for d in _daterange(start, end):
        for venue in target_venues:
            candidate_ids = fetch_nar_race_ids_for_date(venue, d)
            for race_id in candidate_ids:
                if race_id in done:
                    continue
                race_no = int(race_id[-2:])
                try:
                    result = _process_nar_race(
                        venue, d, race_no, past_limit, horse_cache, sleep_sec,
                    )
                except Exception as e:
                    # 開催がない場合もここに来る（result.htmlが取得できない等）ため
                    # 通常はWARNログのみ、race_idは「処理済み」として記録し
                    # 次回以降スキップする
                    _mark_done(progress_path, race_id)
                    continue
                if result is None:
                    _mark_done(progress_path, race_id)
                    continue
                race_info, ranking, horses = result

                if not dry_run:
                    ok, msg = log_backtest_results(
                        system="NAR", race_info=race_info, horses=horses,
                        display_results=ranking, phase5_applied=False,
                    )
                    print(f"  [{d}] {venue} {race_info.race_name}: {msg}")
                else:
                    print(f"  [dry-run][{d}] {venue} {race_info.race_name}"
                          f"（{len(ranking)}頭）を記録予定")

                total_logged += 1
                _mark_done(progress_path, race_id)
                time.sleep(sleep_sec)

    print(f"完了：{total_logged}レース処理しました。")


def main():
    parser = argparse.ArgumentParser(description="過去レース一括バックテスト")
    parser.add_argument("--system", choices=["jra", "nar"], required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--venues", default="", help="カンマ区切り（例：東京,中山）。省略で全会場")
    parser.add_argument("--past-limit", type=int, default=None,
                         help="Phase1に渡す過去走数（省略時 JRA=5 / NAR=3）")
    parser.add_argument("--sleep", type=float, default=1.0, help="リクエスト間隔（秒）")
    parser.add_argument("--dry-run", action="store_true", help="Sheetsに書き込まず件数のみ確認")
    parser.add_argument("--resume-file", default=None, help="チェックポイントファイルのパス")
    args = parser.parse_args()

    start = datetime.datetime.strptime(args.start, "%Y-%m-%d").date()
    end = datetime.datetime.strptime(args.end, "%Y-%m-%d").date()
    venues = [v.strip() for v in args.venues.split(",") if v.strip()] or None
    progress_path = args.resume_file or f".batch_backtest_progress_{args.system}.txt"

    if args.system == "jra":
        past_limit = args.past_limit or 5
        run_jra(start, end, venues, past_limit, args.sleep, args.dry_run, progress_path)
    else:
        past_limit = args.past_limit or 3
        run_nar(start, end, venues, past_limit, args.sleep, args.dry_run, progress_path)


if __name__ == "__main__":
    sys.exit(main())
