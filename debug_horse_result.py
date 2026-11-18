"""
debug_horse_result.py（診断用）
馬の過去走ページ(db.netkeiba.com/horse/result/<horse_id>/)を取りに行き、
HTTP状態・サイズ・タイトル・過去走テーブルの有無を表示する。HTMLは data/ に保存。
「過去走0件」の原因が (A)アクセス制限(HTTPエラー) (B)200だがブロック画面/構造変更 (C)本当に新馬 のどれかを切り分ける。

  python debug_horse_result.py 2022100567 [<horse_id> ...]
  （horse_idは出馬表の馬リンク /horse/xxxxxxxxxx の数字）
"""
import os, sys, requests
from bs4 import BeautifulSoup
try:
    from scraper import HEADERS
except Exception:
    HEADERS = {"User-Agent": "Mozilla/5.0"}

for hid in sys.argv[1:] or ["2022100567"]:
    url = f"https://db.netkeiba.com/horse/result/{hid}/"
    print(f"\n=== {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
    except Exception as e:
        print("  取得失敗:", e); continue
    html = r.content.decode("euc-jp", errors="replace")
    os.makedirs("data", exist_ok=True)
    path = f"data/debug_horse_{hid}.html"
    open(path, "w", encoding="utf-8").write(html)
    soup = BeautifulSoup(html, "html.parser")
    t = soup.find("title")
    tb = soup.find("table", class_="db_h_race_results")
    print(f"  HTTP {r.status_code}  最終URL {r.url}  サイズ {len(r.content)}B  保存 {path}")
    print(f"  title: {t.get_text(strip=True) if t else None}")
    print(f"  db_h_race_results: {'あり 行数=' + str(len(tb.find_all('tr'))) if tb else 'なし'}")
    print(f"  全テーブルclass: {[x.get('class') for x in soup.find_all('table')][:6]}")
