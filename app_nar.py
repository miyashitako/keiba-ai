"""
地方競馬(NAR)AI予想システム Streamlit UI（v0.2・Phase5対応）

run_nar_prediction.py（コマンドライン版）と同じPhase1〜4のパイプラインを
Streamlit UIから実行できるようにしたもの。app.py（JRA版）と同じ考え方で、
レース選択セレクトボックスの見た目をコンパクト化している。

v0.2で追加：
  - Phase5（パドック・馬場バイアス・重馬場適性の人間確認）に対応。
    calculator_nar.apply_phase5（実体はcalculator.pyのapply_phase5をそのまま
    再エクスポートしたもの。JRA/NARでロジック差がないため計算側の改修は
    不要だった）を呼び出す。
  - Phase5適用ボタンを押すたびに再フェッチが走らないよう、fetch結果
    （race_info・horses・Phase3までの結果）をst.session_stateに保持する
    方式に変更（app.pyと同じ設計）。

★注意：このファイルはこのセッションで書き起こしたばかりで、
実際にStreamlitを起動しての動作確認・ブラウザでの見た目確認は
まだ行えていない（このサンドボックス環境からnetkeiba等への
ネットワークアクセスができないため）。お手元の環境で実際に
`streamlit run app_nar.py`を実行し、動作・見た目を確認しながら
調整してください。特に「開催日」はst.date_input（カレンダー picker）
という別種のウィジェットのため、st.selectbox（app.pyで実績のある
input[aria-label="..."]方式）と同じCSSが素直に効くかどうかは
未検証。もし効かない場合は、app.pyのときと同様にブラウザの
デベロッパーツールで実際の要素を確認しながら追い込んでください。

使い方：
    streamlit run app_nar.py
"""

import datetime

import streamlit as st

import calculator_nar as cn
from scraper_nar import (
    fetch_all_horses_nar,
    fetch_all_horses_nar_backtest,
    build_nar_race_id,
)


st.set_page_config(
    page_title="地方競馬AI予想システム",
    page_icon="🐎",
    layout="wide",
)

st.title("🐎 地方競馬(NAR)AI予想システム")
st.caption(f"[calculator_nar version: {cn.__version__}]")

# ── レース選択セレクトボックスの幅をコンパクト化（スマホ対応） ──────
# app.py（JRA版）で実績のある方式をそのまま踏襲。
#   競馬場　　：NAR14場中、最長は「名古屋」の3文字（他は全て2文字）
#   開催日　　：st.date_inputはカレンダー形式のため、表示形式
#              （例："2026/08/09"）に合わせて余裕を持たせた幅にする
#   レース番号：「1R」〜「12R」＝最大3文字
# ドロップダウンの矢印アイコン・カレンダーアイコン分の余白として、
# いずれも入力欄自体の幅より外枠を広めに取っている。
st.markdown(
    """
    <style>
    /* 列自体は中身の幅だけを取り、余白を持て余して間延びしないようにする */
    div[data-testid="stHorizontalBlock"]:nth-of-type(1) > div[data-testid="column"] {
        flex: 0 1 auto !important;
        width: auto !important;
        min-width: 0 !important;
    }

    /* セレクトボックス本体（react-aria版：実体は<input aria-label="...">）。
       aria-label（ラベル文字列そのもの）を目印にする方式。
       文字列長ちょうどだと詰まりすぎるため、+1文字分程度の余白を
       持たせている（狭すぎる/広すぎる場合はこの数値を増減してください）。*/
    input[aria-label="競馬場"] {
        width: 7ch !important;
        min-width: 7ch !important;
        max-width: 7ch !important;
    }
    input[aria-label="レース番号"] {
        width: 5.5ch !important;
        min-width: 5.5ch !important;
        max-width: 5.5ch !important;
    }
    /* 開催日（st.date_input）：selectboxとは別種のウィジェットのため、
       同じCSSが効くかどうか未検証。効かない場合はここを調整してください。 */
    input[aria-label="開催日"] {
        width: 11ch !important;
        min-width: 11ch !important;
        max-width: 11ch !important;
    }

    /* inputを囲む「箱」（枠線・矢印/カレンダーアイコンを含む外側の見た目
       部分）。直近の親（1階層だけ）に限定する">"を使うことで、間違って
       祖先全部（列・行・ページ全体まで）を巻き込んで縮めてしまわない
       ようにする。 */
    div:has(> input[aria-label="競馬場"]) {
        width: 9.5ch !important;
        max-width: 9.5ch !important;
    }
    div:has(> input[aria-label="レース番号"]) {
        width: 8ch !important;
        max-width: 8ch !important;
    }
    div:has(> input[aria-label="開催日"]) {
        width: 13ch !important;
        max-width: 13ch !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# NAR14場（帯広・ばんえいは対象外の方針）
NAR_VENUES = [
    "盛岡", "水沢", "浦和", "船橋", "大井", "川崎", "笠松", "名古屋",
    "園田", "姫路", "高知", "佐賀", "門別", "金沢",
]

# ──────────────────────────────────────────────
# セッション初期化（v0.2追加：Phase5適用のため状態を保持）
# ──────────────────────────────────────────────
for key, default in [
    ("nar_race_info", None),
    ("nar_horses", []),
    ("nar_phase3_results", None),   # Phase3（会場・馬場・騎手）適用済みキャッシュ
    ("nar_phase5_applied", False),
    ("nar_display_results", None),  # 表示用：Phase5適用済みならそちら、未適用ならPhase3
    ("nar_backtest_mode", False),
]:
    if key not in st.session_state:
        st.session_state[key] = default

col_v, col_d, col_r = st.columns([1, 1, 1])
with col_v:
    venue = st.selectbox("競馬場", NAR_VENUES)
with col_d:
    race_date = st.date_input("開催日", value=datetime.date.today())
with col_r:
    race_no = st.selectbox("レース番号", list(range(1, 13)), format_func=lambda x: f"{x}R")

if venue not in cn.NAR_VENUE_JOCKEY_LEADING:
    st.info(f"※ {venue}のリーディング表が未登録です。騎手ボーナスは0で計算されます。")

backtest_mode = st.toggle(
    "検証モード（終了済みレースの答え合わせ）",
    value=False,
    help="ONにすると、指定したレースの確定結果ページ(result.html)から実際の着順・"
         "人気を取得し、予想と突き合わせて表示します。まだ発走前のレースには使えません。",
)

run = st.button("検証を実行" if backtest_mode else "予想を実行", type="primary")

if run:
    race_id = build_nar_race_id(venue, race_date, race_no)
    st.caption(f"race_id={race_id}")

    spinner_label = (
        "確定結果・過去走を取得中..." if backtest_mode
        else "出走表・過去走を取得中...（頭数が多いと数十秒かかります）"
    )
    with st.spinner(spinner_label):
        try:
            if backtest_mode:
                race_info, horses = fetch_all_horses_nar_backtest(
                    venue, race_date, race_no, past_limit=5
                )
            else:
                race_info, horses = fetch_all_horses_nar(
                    venue, race_date, race_no, past_limit=5
                )
        except Exception as e:
            st.error(f"データ取得に失敗しました: {e}")
            st.stop()

    # Phase1〜2
    phase1_results = cn.calc_phase1_all_nar(
        horses,
        target_distance=race_info.distance,
        target_surface=race_info.surface,
        current_class=race_info.race_class,
        target_venue=venue,
        race_date=race_info.race_date,
    )
    phase2_results = cn.calc_phase2_all_nar(phase1_results)

    # Phase3（競馬場・馬場・騎手適性）
    all_past_races = {h.number: h.past_races for h in horses}
    adjusted = cn.apply_venue_jockey_bonus_nar(
        phase2_results, horses, venue, all_past_races,
        target_track_cond=race_info.track_cond,
    )

    # ── フェッチ結果をセッションに保存（Phase5をあとから適用するため）──
    st.session_state.nar_race_info       = race_info
    st.session_state.nar_horses          = horses
    st.session_state.nar_phase3_results  = adjusted
    st.session_state.nar_display_results = adjusted
    st.session_state.nar_phase5_applied  = False
    st.session_state.nar_backtest_mode   = backtest_mode

# ──────────────────────────────────────────────
# 以降はセッションに保存済みのデータを使って表示
# （runボタンを押していない再実行時＝Phase5適用ボタン押下時にも
#   このブロックが走るようにするため、if run: の外に出している）
# ──────────────────────────────────────────────

if st.session_state.nar_race_info is not None:
    race_info = st.session_state.nar_race_info
    horses = st.session_state.nar_horses
    backtest_mode = st.session_state.nar_backtest_mode

    st.subheader(race_info.race_name)
    st.write(
        f"距離/馬場：{race_info.surface}{race_info.distance}m ({race_info.direction})　"
        f"馬場状態：{race_info.track_cond}　クラス：{race_info.race_class}　"
        f"出走頭数：{len(horses)}"
    )

    no_past_data = [h.name for h in horses if not h.past_races]
    if no_past_data:
        st.warning(
            f"過去走データが0件の馬：{', '.join(no_past_data)}"
            "（新馬・地方転入直後等の可能性。有効走数0としてPhase1で処理されます）"
        )

    # 表示用ランキング：Phase5適用済みならそちら、未適用ならPhase3結果
    display_results = st.session_state.nar_display_results

    st.subheader("■ 予想ランキング（小さいほど高評価）" + ("／答え合わせ" if backtest_mode else ""))
    horse_map = {h.number: h for h in horses}
    table_rows = []
    top3_pred = [r.horse_number for r in display_results[:3]]
    for i, r in enumerate(display_results, 1):
        h = horse_map.get(r.horse_number)
        jockey = h.jockey if h else "?"
        row = {
            "予想順位": i,
            "馬番": r.horse_number,
            "馬名": r.horse_name,
            "騎手": jockey,
            "スコア": round(r.phase2_score, 2),
        }
        if backtest_mode:
            actual_finish = getattr(h, "actual_finish", "?") if h else "?"
            actual_pop = getattr(h, "actual_popularity", "?") if h else "?"
            mark = ""
            if isinstance(actual_finish, int) and actual_finish > 0:
                if i <= 3 and actual_finish <= 3:
                    mark = "○的中"
                elif i <= 3 and actual_finish >= 8:
                    mark = "×大外れ"
            row["実際着順"] = f"{actual_finish}着"
            row["人気"] = f"{actual_pop}人気"
            row["判定"] = mark
        table_rows.append(row)
    st.dataframe(table_rows, use_container_width=True, hide_index=True)

    if backtest_mode:
        top3_actual = sorted(
            h.number for h in horses
            if isinstance(getattr(h, "actual_finish", 0), int)
            and 1 <= h.actual_finish <= 3
        )
        hit_count = len(set(top3_pred) & set(top3_actual))
        col_x, col_y, col_z = st.columns(3)
        col_x.metric("予想上位3頭", str(top3_pred))
        col_y.metric("実際の上位3頭", str(top3_actual))
        col_z.metric("重複数", f"{hit_count}/3")

    with st.expander("各馬の詳細note（大差負け・近走不振・地区転入等の内訳）"):
        for r in display_results:
            note_text = r.note if r.note else "（特記事項なし）"
            st.markdown(f"**{r.horse_number}番 {r.horse_name}**：{note_text}")

    # Phase4（レース解像度指数）：表示用データ（Phase5適用済みなら反映後）から再計算
    phase4 = cn.calc_phase4(display_results)

    st.subheader("■ Phase4：レース解像度指数")
    col_a, col_b = st.columns(2)
    with col_a:
        st.metric("判定", phase4.judgment)
        st.write(f"推奨買い目：{phase4.recommended_bet}")
    with col_b:
        st.write(f"上位3頭：{phase4.top3_horses}")

    # ──────────────────────────────────────────────
    # Phase5：人間確認（パドック・馬場バイアス・重馬場適性）v0.2追加
    # ──────────────────────────────────────────────
    st.subheader("■ Phase5 人間確認（パドック・馬場バイアス・重馬場適性）")
    with st.expander("パドック評価・馬場バイアスを入力する", expanded=False):
        track_bias = st.selectbox(
            "馬場バイアス", ["フラット", "内有利", "外有利"], key="nar_track_bias_select"
        )
        st.write("**各馬評価**（◎ ○ × から選択）")

        paddock_ratings = {}
        frame_positions = {}
        muddy_ratings = {}

        h1, h2, h3, h4 = st.columns([3, 2, 2, 2])
        h2.caption("パドック")
        h3.caption("枠位置")
        h4.caption("重馬場")

        all_horses_sorted = sorted(horses, key=lambda h: h.number)
        for h in all_horses_sorted:
            c1, c2, c3, c4 = st.columns([3, 2, 2, 2])
            with c1:
                st.write(f"**{h.number}番 {h.name}**")
            with c2:
                paddock = st.selectbox(
                    "パドック", ["パドック—", "◎", "○", "×"],
                    key=f"nar_p5_paddock_{h.number}_{h.name}", label_visibility="collapsed",
                )
                paddock_ratings[h.number] = paddock
            with c3:
                pos = st.selectbox(
                    "枠位置", ["枠—", "内", "外"],
                    key=f"nar_p5_pos_{h.number}_{h.name}", label_visibility="collapsed",
                )
                if pos not in ("—", "枠—"):
                    frame_positions[h.number] = pos
            with c4:
                muddy = st.selectbox(
                    "重馬場", ["馬場—", "得意", "不得意"],
                    key=f"nar_p5_muddy_{h.number}_{h.name}", label_visibility="collapsed",
                )
                muddy_ratings[h.number] = muddy

        if st.button("✅ Phase5補正を適用", type="primary", key="nar_apply_phase5_btn"):
            # 常にPhase3キャッシュ（st.session_state.nar_phase3_results）を
            # 起点にする（app.pyと同じ考え方：Phase5を何度押しても
            # 前回のPhase5補正の上に積み上がらないようにするため）。
            p3_base = st.session_state.nar_phase3_results
            adjusted5 = cn.apply_phase5(
                p3_base, paddock_ratings, track_bias, frame_positions, muddy_ratings,
            )
            st.session_state.nar_display_results = adjusted5
            st.session_state.nar_phase5_applied = True
            st.rerun()

    if st.session_state.nar_phase5_applied:
        st.success("✅ Phase5補正済みランキングを表示中")
        if st.button("Phase5補正を解除（Phase3時点に戻す）", key="nar_reset_phase5_btn"):
            st.session_state.nar_display_results = st.session_state.nar_phase3_results
            st.session_state.nar_phase5_applied = False
            st.rerun()
