"""
results_logger.py（v0.1新規作成）

答え合わせモード（app.py・app_nar.py共通）で取得した「予想スコアの内訳」と
「実際の着順・人気・オッズ」をGoogle Sheetsに1頭1行で記録するモジュール。

2拠点（複数の端末・環境）で検証作業を行う運用を想定し、ローカルファイル
には一切保存せず、Google Sheetsをクラウド上の共有データストアとして使う。

────────────────────────────────────────────
■ 事前準備（最初に1回だけ・お手元での作業）
────────────────────────────────────────────
1. Google Cloud Consoleでプロジェクトを作成し、サービスアカウントを作成する
     IAMと管理 → サービスアカウント → 作成
   作成したサービスアカウントの「鍵」タブから、JSON形式のキーをダウンロード
   （このJSONファイルは他人に渡さないこと。credentials.jsonのような名前で
   お手元に保存するが、Streamlit Cloudにはファイルごとではなく中身をSecrets
   に転記する。後述）。

2. 以下2つのAPIを有効化する（APIとサービス → ライブラリ）
     - Google Sheets API
     - Google Drive API

3. 記録用のGoogle Sheetsをブラウザで新規作成する（シート名は何でもよい）。
   ダウンロードしたJSON内の "client_email"（例：
   xxx@yyy.iam.gserviceaccount.com という形式）を、そのSheetの
   「共有」→「編集者」として追加する。
   （サービスアカウントは通常のGoogleアカウントと同じように、
   共有されないと一切そのSheetにアクセスできない）

4. SheetのURLから spreadsheet_id を控える：
     https://docs.google.com/spreadsheets/d/【ここがspreadsheet_id】/edit

5. Streamlit Cloud側：アプリの Settings → Secrets に、以下の形式で
   ダウンロードしたJSONの中身を転記する（private_keyは改行を含むので
   \\n表記のまま貼り付けてよい。gspread/google-authが解釈してくれる）：

     [gcp_service_account]
     type = "service_account"
     project_id = "..."
     private_key_id = "..."
     private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
     client_email = "...@....iam.gserviceaccount.com"
     client_id = "..."
     token_uri = "https://oauth2.googleapis.com/token"

     [sheets]
     spreadsheet_id = "手順4で控えたID"

   ローカル(streamlit run)で試す場合は、プロジェクト直下に
   .streamlit/secrets.toml を作って同じ内容を書く（.gitignoreに追加し、
   Gitにはコミットしないこと）。

6. requirements.txtに以下を追加する：
     gspread
     google-auth

────────────────────────────────────────────
■ 記録される列（ワークシート名："prediction_log"、なければ自動作成）
────────────────────────────────────────────
記録日時 / システム(JRA・NAR) / レース日 / 競馬場 / レース名 / クラス /
距離 / 馬場 / 馬場状態 / 馬番 / 馬名 / 有効走数 / 予想順位 /
Phase1スコア / Phase2スコア / Phase5適用 / メモ / 実際着順 / 人気 / 単勝オッズ

★このサンドボックス環境からはGoogle SheetsのAPIエンドポイントに
ネットワーク到達できないため、実際のAPI疎通は未検証。import・関数呼び出し
のロジック自体はgspread 6.x系のAPIドキュメント通りに実装しているが、
お手元の環境で上記セットアップを済ませたうえで、まず1回小さく
（1頭だけの検証結果等で）動作確認してから本格運用してください。

────────────────────────────────────────────
■ batch_backtest.py（素のPythonスクリプト）からの利用について（v0.2追加）
────────────────────────────────────────────
本モジュールはStreamlitアプリ（app.py・app_nar.py）だけでなく、
`python batch_backtest.py`のような素のスクリプトからも同じ設定
（.streamlit/secrets.toml）を使って呼び出せるようにしている。
st.runtime.exists()でStreamlitアプリとして実行中かどうかを判定し、
そうでなければ toml パッケージで.streamlit/secrets.tomlを直接読む
（st.secretsと全く同じファイルを見るので、二重に設定する必要はない）。
requirements.txtには'toml'も追加すること。
"""

import os
import datetime

import streamlit as st

try:
    import gspread
    from google.oauth2.service_account import Credentials
    _GSPREAD_AVAILABLE = True
except ImportError:
    _GSPREAD_AVAILABLE = False


SHEET_NAME = "prediction_log"   # ワークシート（タブ）名。無ければ自動作成する。
SHEET_HEADERS = [
    "記録日時", "システム",
    "レース日", "競馬場", "レース名", "クラス", "距離", "馬場", "馬場状態",
    "馬番", "馬名", "有効走数",
    "予想順位", "Phase1スコア", "Phase2スコア", "Phase5適用",
    "メモ",
    "実際着順", "人気", "単勝オッズ",
]

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _load_secrets_config() -> dict:
    """
    設定（サービスアカウント情報・spreadsheet_id）をどこから読むかを解決する。

    - Streamlitアプリ内（app.py・app_nar.py）から呼ばれた場合：
      st.secretsから読む（Streamlit Cloudの管理画面で設定した内容、
      またはローカルの.streamlit/secrets.toml）。
    - batch_backtest.py等、素のPythonスクリプトから呼ばれた場合：
      st.runtime.exists()がFalseになるため、.streamlit/secrets.tomlを
      tomlパッケージで直接パースする。環境変数STREAMLIT_SECRETS_PATHで
      パスを変更できる（デフォルトはカレントディレクトリ基準）。
    """
    if st.runtime.exists():
        if "gcp_service_account" not in st.secrets or "sheets" not in st.secrets:
            raise RuntimeError(
                "Secretsに[gcp_service_account]・[sheets]の設定が"
                "見つかりません（本ファイル冒頭のセットアップ手順を参照）。"
            )
        return {
            "gcp_service_account": dict(st.secrets["gcp_service_account"]),
            "sheets": dict(st.secrets["sheets"]),
        }

    # ── 素のスクリプト（batch_backtest.py）から呼ばれた場合 ──
    try:
        import toml
    except ImportError:
        raise RuntimeError(
            "batch_backtest.pyから呼び出す場合はtomlパッケージが必要です。"
            "`pip install toml` を実行してください。"
        )
    secrets_path = os.environ.get("STREAMLIT_SECRETS_PATH", ".streamlit/secrets.toml")
    if not os.path.exists(secrets_path):
        raise RuntimeError(
            f"設定ファイルが見つかりません: {secrets_path}\n"
            "results_logger.py冒頭のセットアップ手順に従って"
            ".streamlit/secrets.tomlを作成するか、環境変数"
            "STREAMLIT_SECRETS_PATHでパスを指定してください。"
        )
    config = toml.load(secrets_path)
    if "gcp_service_account" not in config or "sheets" not in config:
        raise RuntimeError(
            f"{secrets_path} に[gcp_service_account]・[sheets]の設定が"
            "見つかりません。"
        )
    return config


@st.cache_resource(show_spinner=False)
def _get_worksheet():
    """
    設定を読み込み、対象のワークシートを返す。
    毎回API接続をやり直すとレート制限に引っかかりやすいため、
    st.cache_resourceで接続オブジェクト自体をプロセス内キャッシュする
    （Streamlitのセッションをまたいで共有される。素のスクリプトから
    呼ばれた場合も、st.cache_resource自体はStreamlitランタイムなしで
    動作するため問題ない）。
    """
    if not _GSPREAD_AVAILABLE:
        raise RuntimeError(
            "gspreadがインストールされていません。requirements.txtに"
            "'gspread'と'google-auth'を追加してください。"
        )

    config = _load_secrets_config()

    creds = Credentials.from_service_account_info(
        dict(config["gcp_service_account"]), scopes=_SCOPES,
    )
    gc = gspread.authorize(creds)

    spreadsheet_id = config["sheets"]["spreadsheet_id"]
    sh = gc.open_by_key(spreadsheet_id)

    try:
        ws = sh.worksheet(SHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=SHEET_NAME, rows=1000, cols=len(SHEET_HEADERS))
        ws.append_row(SHEET_HEADERS)

    # ワークシートは既にあるがヘッダー行がまだ無いケースの保険
    if not ws.row_values(1):
        ws.append_row(SHEET_HEADERS)

    return ws


def log_backtest_results(
    system: str,             # "JRA" または "NAR"
    race_info,
    horses: list,
    display_results: list,   # ranking結果のリスト（Phase5適用後ならそちら）
    phase5_applied: bool = False,
) -> tuple:
    """
    答え合わせモードの結果をGoogle Sheetsに1頭1行で追記する。

    Parameters
    ----------
    system : "JRA" または "NAR"
    race_info : RaceInfo（scraper.py / scraper_nar.py共通）
    horses : list[Horse]（answer_finish等の実績値がsetattrで付与済みのもの）
    display_results : Phase2Result/Phase5適用後のリスト（app.py・app_nar.pyの
        display_results相当。予想順位はこの並び順から算出する）
    phase5_applied : Phase5補正を適用した状態で記録するかどうか

    Returns
    -------
    (success: bool, message: str)
    """
    try:
        ws = _get_worksheet()
    except Exception as e:
        return False, f"Google Sheets接続に失敗しました：{e}"

    horse_map = {h.number: h for h in horses}
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    rows = []
    for rank, r in enumerate(display_results, 1):
        h = horse_map.get(r.horse_number)
        rows.append([
            now_str,
            system,
            getattr(race_info, "race_date", ""),
            getattr(race_info, "venue", ""),
            getattr(race_info, "race_name", ""),
            getattr(race_info, "race_class", ""),
            getattr(race_info, "distance", ""),
            getattr(race_info, "surface", ""),
            getattr(race_info, "track_cond", ""),
            r.horse_number,
            r.horse_name,
            getattr(r, "valid_runs", ""),
            rank,
            round(getattr(r, "phase1_score", 0.0), 3),
            round(getattr(r, "phase2_score", 0.0), 3),
            "TRUE" if phase5_applied else "FALSE",
            getattr(r, "note", ""),
            getattr(h, "actual_finish", "") if h else "",
            getattr(h, "actual_popularity", "") if h else "",
            getattr(h, "actual_odds", "") if h else "",
        ])

    try:
        ws.append_rows(rows, value_input_option="USER_ENTERED")
    except Exception as e:
        return False, f"Google Sheetsへの書き込みに失敗しました：{e}"

    return True, f"✅ {len(rows)}頭分をGoogle Sheetsに記録しました"
