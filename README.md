# 競馬AI予想システム

## セットアップ手順（Windows）

### 1. このフォルダをPCに配置
`keiba_ai` フォルダをデスクトップなど好きな場所に置く。

### 2. パッケージインストール
コマンドプロンプトを開き、以下を実行：

```
cd C:\Users\ユーザー名\Desktop\keiba_ai
pip install -r requirements.txt
```

### 3. ローカルで起動（テスト）
```
streamlit run app.py
```
ブラウザが自動で開く。スマホからはPC同一Wi-Fi上のIPアドレスで開ける。

---

## Streamlit Cloud でホスト（スマホから常時使う場合）

1. GitHubアカウントを作る
2. リポジトリを作り、このフォルダを push する
3. https://share.streamlit.io/ でリポジトリを選択してデプロイ
4. 発行されたURLをスマホのブラウザで開く

---

## ファイル構成

```
keiba_ai/
├── app.py           # Streamlit UI（メインアプリ）
├── scraper.py       # netkeibaスクレイピング
├── calculator.py    # Phase1〜Phase5計算エンジン
├── requirements.txt # 依存パッケージ
└── README.md
```

---

## Phase実装状況

| Phase | 内容 | 状態 |
|-------|------|------|
| Phase1 | 能力コアスコア | ✅ 実装済み |
| Phase2 | 安定性 | 🔜 今後追加 |
| Phase3 | ローテーション | 🔜 今後追加 |
| Phase4 | レース解像度指数 | ✅ 実装済み |
| Phase5 | 人間確認（パドック・馬場）| ✅ 実装済み |

---

## Phase1 計算式メモ

```
補正後タイム =
  実走タイム
  + 距離補正（1600m基準、100mあたり±0.1秒）
  + 斤量補正（55kg基準、1kgあたり±0.1秒）
  + クラス補正（2勝クラス±0、G1+1.2秒 など）
  - 着差補正（1馬身 = 0.1秒）

Phase1スコア =
  前走×0.5 + 2走前×0.3 + 3走前×0.2
  （スコアが小さいほど高評価）
```
