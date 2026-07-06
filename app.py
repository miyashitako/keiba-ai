"""
競馬AI予想システム - Streamlit UI
Phase1〜Phase5 + 距離フィルター・競馬場・騎手適性対応
"""

import streamlit as st
import pandas as pd

from scraper import fetch_all_horses, RaceInfo
try:
    from calculator import (
        calc_phase1, calc_phase2, calc_phase2_all,
        calc_phase4, build_ranking, build_ranking_phase2,
        apply_phase5, apply_venue_jockey_bonus, MUDDY_TRACK_BONUS,
        calc_grade_bonus, calc_recent_form_penalty,
        calc_distance_aptitude_bonus,
        calc_running_style, calc_pace_bias_bonus,
    )
except ImportError as _e:
    import streamlit as _st
    _st.error(f"ImportError詳細: {_e}")
    raise

st.set_page_config(
    page_title="競馬AI予想",
    page_icon="🐎",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── フィールド相対斤量補正レート（v1.5追加）
# 1kg = このpt数だけスコアを増減。小さいほど良スコアなので
#   フィールド平均より軽い → スコア減（有利）
#   フィールド平均より重い → スコア増（不利）
# 現在は案B（全レース一律適用）で検証中。
# 将来的にハンデ戦判定が安定したら案A（ハンデ戦のみ）に切り替え可能。
WEIGHT_FIELD_RATE = 0.5  # pt/kg（v1.5検証：北九州記念で0.3では不足→0.5に変更）

st.title("🐎 競馬AI予想システム")
st.caption("Phase1〜Phase5 | 距離フィルター・競馬場・騎手適性対応")

# ──────────────────────────────────────────────
# セッション初期化
# ──────────────────────────────────────────────

for key, default in [
    ("horses", []),
    ("race_info", None),
    ("phase1_results", []),
    ("phase2_results", []),
    ("phase5_applied", False),
    ("phase3_results", None),    # Phase3（会場・騎手）適用済みキャッシュ
    ("running_styles", {}),      # {horse_number: running_style} 脚質キャッシュ
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ──────────────────────────────────────────────
# ① レース選択（プルダウン）
# ──────────────────────────────────────────────

st.header("① レース選択")

import datetime

# ── 競馬場テーブル ─────────────────────────────
VENUE_OPTIONS = [
    ("01", "札幌"),
    ("02", "函館"),
    ("03", "福島"),
    ("04", "新潟"),
    ("05", "東京"),
    ("06", "中山"),
    ("07", "中京"),
    ("08", "京都"),
    ("09", "阪神"),
    ("10", "小倉"),
]

# 各場の最大開催回数（JRA年間開催日割より）
VENUE_MAX_KAI = {
    "01": 2,  # 札幌
    "02": 1,  # 函館
    "03": 3,  # 福島
    "04": 4,  # 新潟
    "05": 5,  # 東京
    "06": 5,  # 中山
    "07": 4,  # 中京
    "08": 5,  # 京都
    "09": 5,  # 阪神
    "10": 2,  # 小倉
}

# 各場の1開催あたり最大日数
# 東京・京都は12日開催あり、他は8日
VENUE_MAX_NICHI = {
    "01": 8,   # 札幌
    "02": 8,   # 函館
    "03": 8,   # 福島
    "04": 8,   # 新潟
    "05": 12,  # 東京（12日開催あり）
    "06": 8,   # 中山
    "07": 8,   # 中京
    "08": 12,  # 京都（12日開催あり）
    "09": 8,   # 阪神
    "10": 8,   # 小倉
}

# ── デフォルト競馬場：今日の曜日から推定 ────────
# 土日は前週末から継続開催中の場を優先（東京をデフォルト）
_today = datetime.date.today()
_default_venue_idx = 4   # 東京

# ── プルダウン（スマホ考慮：2列×2行）────────────
col_v, col_k = st.columns([3, 2])
col_d, col_r = st.columns([2, 2])

with col_v:
    venue_label = st.selectbox(
        "競馬場",
        options=[name for _, name in VENUE_OPTIONS],
        index=_default_venue_idx,
    )
    venue_code = next(code for code, name in VENUE_OPTIONS if name == venue_label)

with col_k:
    max_kai = VENUE_MAX_KAI.get(venue_code, 5)
    kai = st.selectbox(
        "開催回",
        options=list(range(1, max_kai + 1)),
        format_func=lambda x: f"第{x}回",
        index=0,
    )

with col_d:
    max_nichi = VENUE_MAX_NICHI.get(venue_code, 8)
    nichime = st.selectbox(
        "開催日",
        options=list(range(1, max_nichi + 1)),
        format_func=lambda x: f"{x}日目",
        index=0,
    )

with col_r:
    race_no = st.selectbox(
        "レース番号",
        options=list(range(1, 13)),
        format_func=lambda x: f"{x}R",
        index=10,  # デフォルト：11R
    )

# ── race_id・URL 自動生成 ───────────────────────
year = _today.year
race_id = f"{year}{venue_code}{kai:02d}{nichime:02d}{race_no:02d}"
race_url_generated = f"https://race.netkeiba.com/race/shutuba.html?race_id={race_id}"

st.caption(f"🔗 `{race_id}`　{race_url_generated}")

# ── 検証モード & URL直接入力（折りたたみ） ───────
with st.expander("🔬 検証モード・URL直接入力（上級設定）", expanded=False):
    st.caption("**検証モード**：過去レースで検証する場合、ONにすると直近1走を除外してスコアを計算します。")
    _skip_toggle = st.checkbox("直近1走を除外する（検証モード）", value=False, key="skip_runs_slider")
    skip_runs = 1 if _skip_toggle else 0
    st.divider()
    st.caption("**URL直接入力**：URLを直接貼り付ける場合はこちら（プルダウン設定より優先）")
    race_url_manual = st.text_input(
        "netkeibaのレースURLを直接入力",
        placeholder="https://race.netkeiba.com/race/shutuba.html?race_id=202505040409",
        key="race_url_manual",
    )

# 手動URLが入力されていればそちらを優先
race_url = race_url_manual.strip() if st.session_state.get("race_url_manual", "").strip() else race_url_generated

fetch_btn = st.button("🔍 データ取得", type="primary")

if fetch_btn:
    with st.spinner("データ取得中... （各馬の過去走取得のため1〜2分かかります）"):
        try:
            race_info, horses = fetch_all_horses(race_url, past_limit=5)
            st.session_state.race_info    = race_info
            st.session_state.horses       = horses
            st.session_state.phase5_applied = False
            st.session_state.phase3_results = None
            st.session_state.running_styles = {
                h.number: calc_running_style(h.past_races)
                for h in horses
            }

            # 馬齢限定戦モード：自動判定結果をセッションに保存
            st.session_state["age_limited_auto"] = race_info.is_age_limited
            st.session_state["classic_distance_auto"] = race_info.is_classic_distance

            # Phase1（検証モード：先頭skip_runs走をスキップ）
            _skip = 1 if st.session_state.get("skip_runs_slider", False) else 0
            _age  = st.session_state.get("age_limited_toggle", race_info.is_age_limited)
            _cls  = race_info.is_classic_distance
            p1_results = [
                calc_phase1(
                    h.name, h.number, h.past_races[_skip:],
                    target_distance=race_info.distance,
                    target_surface=race_info.surface,
                    current_class=race_info.race_class,
                    use_grade_bonus=True,
                    use_momentum=True,
                    use_dist_aptitude=True,
                    age_limited=_age,
                    classic_distance=_cls,
                    race_date=race_info.race_date or "",
                    horse_sex=h.sex,
                    is_female_only_race=getattr(race_info, "is_female_only", False),
                    race_name=race_info.race_name,   # v1.2追加：特定レース条件ペナルティ用
                )
                for h in horses
            ]
            p2_results = calc_phase2_all(p1_results)

            st.session_state.phase1_results = p1_results
            st.session_state.phase2_results = p2_results

            st.success(f"✅ {len(horses)}頭のデータを取得しました")
            if race_info.is_age_limited:
                st.info(f"🐴 馬齢限定戦を自動検出しました（格ボーナスを統合評価）")

            # ── デバッグパネル（性別・列4の取得状況）──────────────────
            # 来週以降のエラー再現時に原因特定するための情報を常時記録
            sex_debug = [
                f"#{h.number} {h.name}：sex='{h.sex}'　jockey='{h.jockey}'　斤量={h.weight_carried}"
                for h in horses
            ]
            warnings = [
                f"⚠️ #{h.number} {h.name}：{h._sex_parse_warning}　col4_raw={getattr(h, '_col4_raw', '?')}"
                for h in horses if getattr(h, "_sex_parse_warning", None)
            ]
            # ── 障害レースデバッグパネル（race_class確認用）─────────────
            with st.expander("🔍 デバッグ：過去走race_class確認（障害調査用）", expanded=False):
                st.caption("障害レースのrace_classキーワード確認用。実装後は非表示にします。")
                for h in horses:
                    if h.past_races:
                        lines = [
                            f"  {i+1}走前: race_class='{pr.race_class}' finish={pr.finish}"
                            for i, pr in enumerate(h.past_races[:5])
                        ]
                        st.text(f"#{h.number} {h.name}")
                        for line in lines:
                            st.text(line)

            # ── 地方走race_classデバッグパネル（地方転入馬の換算確認用）──
            _local_debug_lines = []
            for _r in st.session_state.phase1_results:
                _debug = getattr(_r, "_local_rc_debug", None)
                if _debug:
                    _local_debug_lines.append(f"#{_r.horse_number} {_r.horse_name}")
                    for _d in _debug:
                        _local_debug_lines.append(f"  {_d}")
            if _local_debug_lines:
                with st.expander("🔍 デバッグ：地方走race_class確認（転入馬換算調査用）", expanded=False):
                    st.caption("地方走のみの馬のrace_class一覧。クラス判定が「不明」になっている場合は正規表現の拡張が必要です。")
                    for _line in _local_debug_lines:
                        st.text(_line)

            with st.expander("🔍 デバッグ：性別・騎手取得状況（エラー調査用）", expanded=bool(warnings)):
                st.caption("性別が空欄の馬がいる場合、土曜レース中のHTML構造変化が疑われます。")
                if warnings:
                    st.error("⚠️ 性別パース警告あり：来週エラー報告時にこの内容をコピーしてください")
                    for w in warnings:
                        st.text(w)
                    st.divider()
                for line in sex_debug:
                    color = "🔴" if "sex=''" in line else "🟢"
                    st.text(f"{color} {line}")
                raw_log = getattr(race_info, "_scrape_debug_log", None)
                if raw_log:
                    st.subheader("スクレイパー診断ログ")
                    st.code(raw_log, language="text")

        except Exception as e:
            import traceback
            st.error(f"❌ エラーが発生しました: {e}")
            # 詳細なトレースバックを展開パネルに表示（来週のデバッグ用）
            with st.expander("🔍 エラー詳細（デバッグ情報）", expanded=True):
                st.code(traceback.format_exc(), language="python")
                if "horses" in dir() and horses:
                    st.subheader("取得済み馬データ（エラー直前）")
                    for h in horses:
                        st.text(f"#{h.number} {h.name}：sex='{h.sex}'　jockey='{h.jockey}'")
            st.info("URLを確認するか、手動入力モードをお試しください。")

# ──────────────────────────────────────────────
# レース情報表示
# ──────────────────────────────────────────────

if st.session_state.race_info:
    ri: RaceInfo = st.session_state.race_info
    # race_idの末尾2桁がレース番号
    _race_no_disp = ""
    if ri.race_id:
        try:
            _race_no_disp = f"　{int(ri.race_id[-2:])}R"
        except Exception:
            pass
    st.subheader(f"📋 {ri.race_name}{_race_no_disp}　{ri.race_date}　{ri.venue}")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("距離",   f"{ri.surface}{ri.distance}m" if ri.distance else "—")
    col2.metric("競馬場", ri.venue or "—")
    col3.metric("馬場",   ri.track_cond or "—")
    col4.metric("クラス", ri.race_class or "—")

# ──────────────────────────────────────────────
# ② ランキング表示
# ──────────────────────────────────────────────

if st.session_state.phase2_results:
    st.header("② 予想ランキング")

    col_opt1, col_opt2 = st.columns(2)
    with col_opt1:
        use_phase2 = st.toggle("Phase2（安定性・爆発力補正）を使う", value=True)
    with col_opt2:
        use_venue_jockey = st.toggle("競馬場・騎手適性補正を使う", value=True)
    col_opt3, col_opt4 = st.columns(2)
    with col_opt3:
        use_momentum = st.toggle("昇級勢い補正を使う", value=True,
                                 help="前走より格上クラスへ昇級する馬にボーナス/ペナルティを付与")
    with col_opt4:
        use_dist_apt = st.toggle("距離適性補正を使う", value=True,
                                 help="±200m以内の好走実績・スタミナ証明でボーナス。距離実績なしはペナルティ")

    col_opt5, col_opt6 = st.columns(2)
    with col_opt5:
        use_pace_bias = st.toggle("展開・トラックバイアス補正を使う", value=True,
                                  help="脚質と展開・馬場・開催週から有利不利を補正")
    with col_opt6:
        pass

    # 馬齢限定戦トグル（自動判定を上書き可能）
    auto_age = st.session_state.get("age_limited_auto", False)
    age_limited_toggle = st.toggle(
        "🐴 馬齢限定戦モード（格ボーナスを統合評価）",
        value=auto_age,
        key="age_limited_toggle",
        help="ON: OP/L/G3/G2/G1を全て同格として評価。2〜3歳限定戦に使用。",
    )
    if auto_age and not age_limited_toggle:
        st.caption("⚠️ 自動検出された馬齢限定戦設定をOFFにしています")
    elif not auto_age and age_limited_toggle:
        st.caption("ℹ️ 手動で馬齢限定戦モードをONにしています")

    # ランキング元データ決定
    if use_phase2:
        ranking_base = st.session_state.phase2_results
    else:
        ranking_base = st.session_state.phase1_results

    # ── 距離適性トグルOFF時の打ち消し処理
    import copy as _copy
    ri_dist = st.session_state.race_info.distance if st.session_state.race_info else 0

    if not use_dist_apt and ri_dist:
        adjusted_base = []
        for r in ranking_base:
            horse_obj = next((h for h in st.session_state.horses if h.number == r.horse_number), None)
            new_r = _copy.copy(r)
            if horse_obj:
                db, _ = calc_distance_aptitude_bonus(
                    horse_obj.past_races, ri_dist,
                    target_surface=st.session_state.race_info.surface if st.session_state.race_info else "",
                )
                if use_phase2:
                    new_r.phase2_score = round(new_r.phase2_score + db, 3)
                else:
                    new_r.phase1_score = round(new_r.phase1_score + db, 3)
            adjusted_base.append(new_r)
        ranking_base = sorted(adjusted_base,
                              key=lambda x: x.phase2_score if use_phase2 else x.phase1_score)

    # 競馬場・騎手適性補正
    # phase3_resultsキャッシュが未作成の場合のみPhase3を適用してキャッシュ保存
    ri = st.session_state.race_info
    if st.session_state.phase3_results is None:
        if use_venue_jockey and ri and ri.venue:
            all_past = {h.number: h.past_races for h in st.session_state.horses}
            ranking_base = apply_venue_jockey_bonus(
                ranking_base,
                st.session_state.horses,
                ri.venue,
                all_past,
                target_track_cond=ri.track_cond or "",
            )
        st.session_state.phase3_results = list(ranking_base)  # Phase3済みをキャッシュ

    # ── 展開・トラックバイアス補正（Phase3の後に適用）
    if use_pace_bias and ri and not st.session_state.phase5_applied:
        _styles     = st.session_state.get("running_styles", {})
        _all_styles = [(n, s) for n, s in _styles.items() if s]
        _field_size = len(st.session_state.horses)

        # 開催週は開催日から自動計算（v1.2：手動入力廃止）
        # 1・2日目→1週目、3・4日目→2週目、…（ceil(nichime/2)）
        import math as _math
        _race_week = _math.ceil(nichime / 2)  # 1-2日目→1週目、3-4日目→2週目…
        with st.expander("📅 展開設定（コース替わり・連続開催）", expanded=False):
            st.caption(f"開催週：{_race_week}週目（{nichime}日目より自動計算）")
            _chg_cols = st.columns(2)
            _course_chg      = _chg_cols[0].checkbox("コース替わり初週", value=False, key="pace_course_change",
                                                      help="コース変更で芝回復→逃先有利。ONの場合は連続開催より優先。")
            _continuous_meet = _chg_cols[1].checkbox("連続開催", value=False, key="pace_continuous_meet",
                                                      help="前開催から継続使用の芝コース→週数に関係なく馬場荒れ扱い")
            st.caption("開幕週・コース替わり→逃先有利 / 連続開催または3週目以降→差追有利（コース替わり優先）")

        _adjusted = []
        import copy as _cp2
        # 障害レースは展開・競馬場バイアスをスキップ
        _is_hurdle_race = "障" in (ri.race_class or "")
        for r in st.session_state.phase3_results:
            if _is_hurdle_race:
                _adjusted.append(r)
                continue
            _h = next((h for h in st.session_state.horses if h.number == r.horse_number), None)
            _style = _styles.get(r.horse_number, "")
            _frame = _h.frame if _h else 0
            _pb, _plabel = calc_pace_bias_bonus(
                r.horse_name, r.horse_number, _frame,
                _style, _field_size, _all_styles,
                ri.venue or "", ri.surface or "", ri.distance or 0,
                ri.direction or "", ri.track_cond or "",
                int(_race_week), bool(_course_chg),
                bool(st.session_state.get("pace_continuous_meet", False)),
            )
            if _pb != 0.0:
                _nr = _cp2.copy(r)
                if use_phase2:
                    _nr.phase2_score = round(_nr.phase2_score - _pb, 3)
                else:
                    _nr.phase1_score = round(_nr.phase1_score - _pb, 3)
                _nr.note = (_nr.note + f" [展開:{_plabel}]").strip()
                _adjusted.append(_nr)
            else:
                _adjusted.append(r)
        _pace_base = sorted(_adjusted, key=lambda x: x.phase2_score if use_phase2 else x.phase1_score)
    else:
        _pace_base = st.session_state.phase3_results or []

    # ── フィールド相対斤量補正（v1.5追加）─────────────────────────────
    # 今回レースの全馬斤量平均を基準に、軽い馬を有利・重い馬を不利に補正。
    # 案B（全レース一律）で検証中。
    # race_info.weight_type == "ハンデ" 判定が安定したら
    #   「if not ri or ri.weight_type == "ハンデ":」に切り替えて案Aへ移行可能。
    _horses_for_weight = st.session_state.horses
    _weights = [h.weight_carried for h in _horses_for_weight if h.weight_carried > 0]
    if len(_weights) >= 2:
        import statistics as _stats
        _field_avg_w = _stats.mean(_weights)
        _wt_adjusted = []
        import copy as _cp3
        for r in _pace_base:
            _h_w = next((h.weight_carried for h in _horses_for_weight if h.number == r.horse_number), 0)
            if _h_w <= 0:
                _wt_adjusted.append(r)
                continue
            _wt_diff = _field_avg_w - _h_w   # 正=平均より軽い=有利
            _wt_adj  = round(_wt_diff * WEIGHT_FIELD_RATE, 3)
            if abs(_wt_adj) >= 0.05:          # 0.05pt未満は表示しない
                _nr_w = _cp3.copy(r)
                if use_phase2:
                    _nr_w.phase2_score = round(_nr_w.phase2_score - _wt_adj, 3)
                else:
                    _nr_w.phase1_score = round(_nr_w.phase1_score - _wt_adj, 3)
                _sign = "-" if _wt_adj > 0 else "+"
                _ri_wt = ri.weight_type if ri else ""
                _wt_label = f"ハンデ" if _ri_wt == "ハンデ" else "斤量"
                _nr_w.note = (_nr_w.note + f" [{_wt_label}:{_sign}{abs(_wt_adj):.2f}pt({_h_w}kg/avg{_field_avg_w:.1f}kg)]").strip()
                _wt_adjusted.append(_nr_w)
            else:
                _wt_adjusted.append(r)
        _pace_base = sorted(_wt_adjusted, key=lambda x: x.phase2_score if use_phase2 else x.phase1_score)
    # ─────────────────────────────────────────────────────────────────

    # 表示用ranking：Phase5適用済みならphase2_resultsを、未適用ならphase3_resultsを使う
    if st.session_state.phase5_applied:
        display_base = st.session_state.phase2_results  # Phase5済みデータ
    else:
        display_base = _pace_base  # 展開バイアス適用済みデータ

    ranking = build_ranking_phase2(display_base) if use_phase2 else build_ranking(display_base)

    # ── 上り馬注目フラグ（フェーズB：クラシック・ステップレース限定）──────
    # 対象レース：菊花賞・秋華賞およびそのステップレース
    CLASSIC_WATCH_RACES = {
        "菊花賞", "セントライト記念", "神戸新聞杯",
        "秋華賞", "ローズS", "紫苑S",
    }
    from calculator import get_class_base, calc_grade_bonus

    _watch_flags = {}   # {horse_number: "⚡2勝上り注目" or "⚡3勝上り注目"}
    _ri = st.session_state.race_info
    _race_name_norm = ""
    if _ri and _ri.race_name:
        import unicodedata as _ud2
        _race_name_norm = _ud2.normalize("NFKC", _ri.race_name)

    _is_watch_race = any(name in _race_name_norm for name in CLASSIC_WATCH_RACES)

    if _is_watch_race:
        # 各馬の直前走クラスを判定（芝ダフィルター前の全走を使用）
        _TWO_WIN_BASE  = 88.0   # 2勝クラス（1000万下含む）
        _THREE_WIN_BASE = 84.0  # 3勝クラス（1600万下含む）

        _two_win_candidates   = []   # [(score, horse_number), ...]
        _three_win_candidates = []   # [(score, horse_number), ...]

        for _h in st.session_state.horses:
            # 直前走を芝ダフィルター前の全走から取得
            _all_pr = _h.past_races   # scraper取得の全走（フィルター前）
            if not _all_pr:
                continue
            _last = _all_pr[0]   # 直前走（インデックス0=最新）

            # 直前走が着外（finish<=0）や取消の場合はスキップ
            if _last.finish <= 0:
                continue

            _last_base = get_class_base(_last.race_class)

            # 直前走が2勝クラス勝ち（finish==1 かつ CLASS_BASE==88.0）
            if _last.finish == 1 and _last_base == _TWO_WIN_BASE:
                _score = next(
                    (r.phase2_score if use_phase2 else r.phase1_score
                     for r in ranking if r.horse_number == _h.number),
                    9999.0
                )
                _two_win_candidates.append((_score, _h.number))

            # 直前走が3勝クラス勝ち（finish==1 かつ CLASS_BASE==84.0）かつ重賞実績なし
            elif _last.finish == 1 and _last_base == _THREE_WIN_BASE:
                _grade_b = calc_grade_bonus(_all_pr, age_limited=False)
                if _grade_b == 0.0:   # 重賞実績なし（格ボーナスゼロ）
                    _score = next(
                        (r.phase2_score if use_phase2 else r.phase1_score
                         for r in ranking if r.horse_number == _h.number),
                        9999.0
                    )
                    _three_win_candidates.append((_score, _h.number))

        # スコア最良（最小値）の1頭だけフラグ付与
        if _two_win_candidates:
            _best_two = min(_two_win_candidates, key=lambda x: x[0])
            _watch_flags[_best_two[1]] = "⚡2勝上り注目"
        if _three_win_candidates:
            _best_three = min(_three_win_candidates, key=lambda x: x[0])
            _watch_flags[_best_three[1]] = "⚡3勝上り注目"

        if _watch_flags:
            _flag_labels = "　".join(
                f"**{_watch_flags[hn]}** (#{hn})"
                for hn in sorted(_watch_flags.keys())
            )
            st.info(f"🔎 上り馬注目：{_flag_labels}")

    # ────────────────────────────────────────────────────────────────────

    table_data = []
    for rank, r in enumerate(ranking, 1):
        if use_phase2:
            score  = r.phase2_score
            p1disp = f"{r.phase1_score:.3f}"
            best   = f"{r.best_time:.3f}" if r.best_time > 0 else "—"
            std    = f"{r.std_dev:.3f}"
        else:
            score  = r.phase1_score
            p1disp = "—"
            best   = f"{r.best_time:.3f}" if r.best_time > 0 else "—"
            std    = "—"

        score_disp = f"{score:.3f}" if score < 9000 else "—"

        # 上り馬フラグをメモに追記
        _flag = _watch_flags.get(r.horse_number, "")
        _note_with_flag = f"{_flag} {r.note}".strip() if _flag else r.note

        # 斤量増減注意情報（前走比±2kg以上で表示）
        _h_obj = next((h for h in st.session_state.horses if h.number == r.horse_number), None)
        if _h_obj and _h_obj.past_races:
            _prev_wc = _h_obj.past_races[0].weight_carried
            _curr_wc = _h_obj.weight_carried
            _wc_diff = _curr_wc - _prev_wc
            if _wc_diff >= 4.0:
                _note_with_flag = f"⚠️⚠️斤量+{_wc_diff:.1f}kg増 {_note_with_flag}".strip()
            elif _wc_diff >= 2.0:
                _note_with_flag = f"⚠️斤量+{_wc_diff:.1f}kg増 {_note_with_flag}".strip()
            elif _wc_diff <= -2.0:
                _note_with_flag = f"✅斤量{_wc_diff:.1f}kg減 {_note_with_flag}".strip()

        table_data.append({
            "予想順位":   rank,
            "馬番":       r.horse_number,
            "馬名":       r.horse_name,
            "スコア":     score_disp,
            "Phase1":    p1disp if use_phase2 else score_disp,
            "ベスト":     best,
            "標準偏差":   std,
            "走数":       r.valid_runs,
            "メモ":       _note_with_flag,
        })

    df = pd.DataFrame(table_data)
    try:
        st.dataframe(df, width="stretch", hide_index=True)
    except Exception:
        st.dataframe(df, hide_index=True)

    # ③ Phase4
    st.header("③ Phase4 レース解像度")
    phase4 = calc_phase4(ranking_base)

    # 指標メトリクス
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("1〜2位差", f"{phase4.gap_1_2:.3f}秒", help="0.3秒以上で1強判定")
    c2.metric("1〜3位差", f"{phase4.gap_1_3:.3f}秒")
    c3.metric("2〜4位差", f"{phase4.gap_2_4:.3f}秒", help="相手の絞りやすさ")
    c4.metric("上位5頭std", f"{phase4.std_top5:.3f}")

    # 判定バナー
    icons = {
        "1強混戦":   "🔵", "1強準混戦": "🟢", "1強明確":  "🟢",
        "明確型":    "🟢", "準混戦":    "🟡", "混戦":     "🟠", "超混戦": "🔴",
    }
    dominant_label = "【1強】" if phase4.is_dominant else ""
    st.info(f"{icons.get(phase4.judgment,'⚪')} **{dominant_label}{phase4.judgment}** → {phase4.recommended_bet}")

    # 軸・相手候補の表示
    if phase4.top3_horses:
        # 馬番→馬名の逆引きマップ
        num_to_name = {h.number: h.name for h in st.session_state.horses}
        if phase4.is_dominant:
            pivot = phase4.top3_horses[0]
            rivals = phase4.rival_range
            pivot_str  = f"**{pivot}番 {num_to_name.get(pivot, '')}**"
            rivals_str = "　".join(f"{n}番 {num_to_name.get(n,'')}" for n in rivals)
            st.markdown(f"🎯 軸: {pivot_str}　　相手候補: {rivals_str}")
        else:
            top_str = "　".join(f"**{n}番 {num_to_name.get(n,'')}**" for n in phase4.top3_horses)
            st.markdown(f"🎯 注目: {top_str}")

    # ④ Phase5
    st.header("④ Phase5 人間確認（パドック・調教・馬場）")
    with st.expander("パドック・調教評価・馬場バイアスを入力する", expanded=False):
        track_bias = st.selectbox("馬場バイアス", ["フラット", "内有利", "外有利"], key="track_bias_select")
        st.write("**各馬評価**（◎ ○ × から選択）")

        paddock_ratings = {}
        training_ratings = {}   # v1.2追加：調教評価
        frame_positions = {}
        muddy_ratings = {}

        # ヘッダー行
        h1, h2, h3, h4, h5 = st.columns([3, 2, 2, 2, 2])
        h2.caption("パドック")
        h3.caption("調教")
        h4.caption("枠位置")
        h5.caption("重馬場")

        # 全馬を馬番順で表示（rankingではなくhorsesから取得）
        all_horses_sorted = sorted(
            st.session_state.horses,
            key=lambda h: h.number
        )
        for h in all_horses_sorted:
            c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 2, 2])
            with c1:
                st.write(f"**{h.number}番 {h.name}**")
            with c2:
                paddock = st.selectbox("パドック", ["パドック—", "◎", "○", "×"],
                    key=f"p5_paddock_{h.number}_{h.name}", label_visibility="collapsed")
                paddock_ratings[h.number] = paddock
            with c3:
                training = st.selectbox("調教", ["調教—", "◎", "○", "×"],
                    key=f"p5_training_{h.number}_{h.name}", label_visibility="collapsed")
                training_ratings[h.number] = training
            with c4:
                pos = st.selectbox("枠位置", ["枠—", "内", "外"],
                    key=f"p5_pos_{h.number}_{h.name}", label_visibility="collapsed")
                if pos not in ("—", "枠—"):
                    frame_positions[h.number] = pos
            with c5:
                muddy = st.selectbox("重馬場", ["馬場—", "得意", "不得意"],
                    key=f"p5_muddy_{h.number}_{h.name}", label_visibility="collapsed")
                muddy_ratings[h.number] = muddy

        if st.button("✅ Phase5補正を適用", type="primary", key="apply_phase5_btn"):
            # 常にphase3_resultsキャッシュ（Phase3済み）にPhase5を上乗せ
            p3_base = st.session_state.phase3_results
            # 調教評価をパドック評価と同スケールで合算
            # ◎:+2.0 / ○:+1.0 / ×:-2.0 でパドック評価に足す
            TRAINING_SCORE = {"◎": 2.0, "○": 1.0, "×": -2.0}
            combined_paddock = dict(paddock_ratings)
            for hn, tr in training_ratings.items():
                tr_pt = TRAINING_SCORE.get(tr, 0.0)
                if tr_pt == 0.0:
                    continue
                # パドック未入力の馬は調教のみ適用
                # パドック入力済みの場合は合算（上限・下限なし）
                pd_label = combined_paddock.get(hn, "パドック—")
                PD_SCORE = {"◎": 2.0, "○": 1.0, "△": 0.0, "×": -2.0}
                pd_pt = PD_SCORE.get(pd_label, 0.0)
                total_pt = pd_pt + tr_pt
                # 合算値を疑似ラベルとしてそのままphase5に渡す代わりに
                # paddock_ratingsの値を合計ptで上書き（apply_phase5が数値対応の場合）
                # → apply_phase5がラベル文字列のみ対応の場合は別途数値を渡す
                combined_paddock[hn] = ("◎" if total_pt >= 3.5
                                        else "○" if total_pt >= 1.5
                                        else "×" if total_pt <= -1.5
                                        else "—")
            adjusted = apply_phase5(p3_base, combined_paddock, track_bias, frame_positions, muddy_ratings)
            if use_phase2:
                st.session_state.phase2_results = adjusted
            else:
                st.session_state.phase1_results = adjusted
            st.session_state.phase5_applied = True
            st.rerun()

    if st.session_state.phase5_applied:
        st.success("✅ Phase5補正済みランキングを表示中")

# ──────────────────────────────────────────────
# 手動入力モード
# ──────────────────────────────────────────────

st.divider()
st.header("🖊️ 手動入力モード")

with st.expander("手動で馬データを入力する"):
    num_horses = st.number_input("出走頭数", min_value=2, max_value=18, value=8, step=1, key="manual_num_horses")
    manual_horses = []

    for i in range(1, int(num_horses) + 1):
        st.write(f"--- {i}番馬 ---")
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("馬名", key=f"m_name_{i}", value=f"馬{i}")
        with c2:
            weight_carried = st.number_input("斤量", key=f"m_wc_{i}", value=55.0, step=0.5, min_value=48.0, max_value=60.0)

        past_races_manual = []
        for j in range(1, 4):
            with st.expander(f"{j}走前", expanded=(j == 1)):
                cc1, cc2, cc3 = st.columns(3)
                with cc1:
                    dist = st.number_input("距離(m)", key=f"m_dist_{i}_{j}", value=1600, step=100)
                    time_input = st.text_input("タイム(例:1:34.5)", key=f"m_time_{i}_{j}", value="")
                with cc2:
                    finish = st.number_input("着順", key=f"m_fin_{i}_{j}", value=1, step=1, min_value=1, max_value=18)
                    margin = st.number_input("着差(馬身)", key=f"m_mar_{i}_{j}", value=0.0, step=0.1)
                with cc3:
                    race_class = st.selectbox("クラス",
                        ["2勝クラス","新馬","未勝利","1勝クラス","3勝クラス","OP","G3","G2","G1"],
                        key=f"m_cls_{i}_{j}")
                    wc_j = st.number_input("斤量", key=f"m_wcj_{i}_{j}", value=float(weight_carried), step=0.5)

                if time_input:
                    from scraper import PastRace, time_to_sec, margin_to_sec
                    pr = PastRace(distance=int(dist), time_sec=time_to_sec(time_input),
                                  finish=int(finish), margin=margin_to_sec(str(float(margin)*0.2)),
                                  race_class=race_class, weight_carried=float(wc_j))
                    if pr.time_sec > 0:
                        past_races_manual.append(pr)

        manual_horses.append((name, i, weight_carried, past_races_manual))

    if st.button("📊 手動データでPhase1+2計算", type="primary", key="manual_calc_btn"):
        p1 = [calc_phase1(name, number, past) for name, number, _, past in manual_horses]
        p2 = calc_phase2_all(p1)
        st.session_state.phase1_results = p1
        st.session_state.phase2_results = p2
        st.session_state.phase5_applied = False
        st.rerun()

st.divider()
st.caption("競馬AI予想システム v1.0 | 着順・着差・クラスベース Phase1 + 距離適性・格ボーナス・昇級勢い・競馬場・騎手適性 | 検証モード対応")
