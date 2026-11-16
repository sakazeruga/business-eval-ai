"""外部検索に送る語の検査（search_gate.py）のテスト。外部APIは呼ばない。

Perplexity の検索は ZDR の対象外なので、事業構想を特定できる語が
外に出ないことを機械的に確かめる。
"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

import search_gate as G  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(detail)) if detail and not cond else ""))
    if not cond:
        fails += 1


SOURCE = """## 事業構想
### 解決する課題
中小製造業の見積作成に1件あたり平均3時間かかり、属人化している。
### ソリューション
図面PDFから工程・材料を自動抽出し、過去実績から見積を即時生成するSaaS「ミツモリAI」。
代表の山田太郎が株式会社スタジオンで開発。月額5万円。"""
TERMS = ["ミツモリAI", "山田太郎", "株式会社スタジオン", "スタジオン"]


def reason(q):
    return G.reject_reason(q, SOURCE, TERMS)


# --- 送ってよい語 ---
for q in ["製造業 見積 自動化 クラウド", "図面 読み取り 見積 ソフト 比較",
          "製造業 DX 市場規模 2025", "中小企業 省力化 補助金"]:
    check(f"一般語は通す: {q}", reason(q) is None, reason(q))

# --- 送ってはいけない語 ---
check("サービス名を含む語は捨てる", reason("ミツモリAI 評判") == "固有名")
check("人名を含む語は捨てる", reason("山田太郎 起業") == "固有名")
check("社名の表記揺れ（全角・空白）も捨てる", reason("スタジオン 見積") == "固有名")
check("金額を含む語は捨てる", reason("見積SaaS 月額5万円") == "数値（年以外）")
check("全角数字も数値として捨てる", reason("見積 ３時間 削減") == "数値（年以外）")
check("年は数値として扱わない", reason("見積 SaaS 2026 市場") is None)
check("URLは捨てる", reason("https://example.com 見積") == "URL・ドメイン・メールアドレス")
check("ドメインは捨てる", reason("studion.quest 見積") == "URL・ドメイン・メールアドレス")
check("メールアドレスは捨てる", reason("info@ 見積") == "URL・ドメイン・メールアドレス")
check("入力の長い書き写しは捨てる",
      reason("図面PDFから工程・材料を自動抽出") == "入力の書き写し", reason("図面PDFから工程・材料を自動抽出"))
check("空白を挟んだ書き写しも捨てる",
      reason("過去実績から 見積を即時生成") == "入力の書き写し", reason("過去実績から 見積を即時生成"))
check("長すぎる語は捨てる", reason("製造業" * 20) == "長すぎる")
check("空の語は捨てる", reason("  ") == "空")

# --- sanitize: 重複除去・上限・型 ---
kept, dropped = G.sanitize(
    ["製造業 見積 自動化", "製造業　見積　自動化", "ミツモリAI", 123, "見積 ソフト 比較",
     "図面 解析 クラウド", "工程 管理 SaaS", "原価計算 ソフト", "生産管理 クラウド"],
    SOURCE, TERMS)
check("全角空白の重複は1つにまとめる", kept.count("製造業 見積 自動化") == 1, kept)
check("上限件数で切る", len(kept) == G.MAX_QUERIES, kept)
check("捨てた数を数える（固有名・非文字列・上限超過）", dropped == 3, dropped)

# --- build_plan: private_terms は外に出さない ---
raw = {"competitors": ["製造業 見積 自動化", "ミツモリAI 競合"],
       "why_now": ["製造業 DX 市場規模 2025"],
       "private_terms": TERMS}
plan = G.build_plan(raw, SOURCE)
check("目的別に検索語を残す", plan["competitors"] == ["製造業 見積 自動化"]
      and plan["why_now"] == ["製造業 DX 市場規模 2025"], plan)
check("捨てた数を返す", plan["dropped"] == 1, plan)
check("固有名の一覧は計画に含めない", "private_terms" not in plan, plan)

plan = G.build_plan({"competitors": "文字列", "why_now": None}, SOURCE)
check("壊れた形でも空で返す", plan["competitors"] == [] and plan["why_now"] == [], plan)

# --- Perplexity に送る本文 ---
body = G.research_input("competitors", ["製造業 見積 自動化"])
check("送る本文に検索語が入る", "- 製造業 見積 自動化" in body)
check("送る本文に事業構想が入らない", all(t not in body for t in ["ミツモリ", "3時間", "5万円", "山田"]), body)
check("目的ごとに依頼内容が変わる", "市場規模" in G.research_input("why_now", ["x"]))

# --- スキーマと読み取り ---
s = G.schema()
check("スキーマは全項目必須・追加禁止", set(s["required"]) == {"competitors", "why_now", "private_terms"}
      and s["additionalProperties"] is False)
payload = json.dumps(raw, ensure_ascii=False)
check("コードフェンス付きを読む", G.parse("```json\n" + payload + "\n```")["why_now"] == raw["why_now"])
try:
    G.parse("JSONではありません")
    check("壊れた出力は例外にする", False)
except ValueError:
    check("壊れた出力は例外にする", True)

check("送った語の注記", "`製造業 見積 自動化`" in G.sent_note(["製造業 見積 自動化"]))
check("語が無ければ注記なし", G.sent_note([]) == "")

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
