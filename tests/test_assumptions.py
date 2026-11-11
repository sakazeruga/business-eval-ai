"""前提数値（assumptions.py）のテスト。外部APIは呼ばない。

派生指標を Python で計算しているので、計算そのものが正しいことと、
LLM が単位違いや不正な値を返したときに捨てられることを確認する。
"""
import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, ".")

import assumptions as A  # noqa: E402

fails = 0


def check(name, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + name + (("  -> " + str(detail)) if detail and not cond else ""))
    if not cond:
        fails += 1


def item(value, source="入力", rationale="入力値"):
    return {"value": value, "source": source, "rationale": rationale}


def full(**overrides):
    base = {k: item(None, "不明", "") for k in A.FIELD_KEYS}
    base.update({k: item(v) for k, v in overrides.items()})
    base["notes"] = "テスト"
    return base


def derived_map(values):
    d, w = A.compute_derived(values)
    return {x["key"]: x["value"] for x in d}, w


# --- スキーマ ---
s = A.schema()
check("スキーマは全項目を必須にしている（strictモード要件）",
      set(s["required"]) == set(A.FIELD_KEYS) | {"notes"}, s["required"])
check("スキーマは追加プロパティを禁止している", s["additionalProperties"] is False)
check("値はnull許容にしない（多数のnull許容項目は400になる）",
      all(s["properties"][k]["properties"]["value"] == {"type": "number"} for k in A.FIELD_KEYS))

# --- 派生指標の計算 ---
v = A.normalize(full(arpu_monthly=50000, gross_margin_pct=80, churn_monthly_pct=2,
                     cac=300000, fixed_cost_monthly=2000000, customers_12m=60))
d, w = derived_map(v)
check("1顧客あたり月次粗利 = 単価 × 粗利率", d["gross_profit_per_customer"] == 40000, d)
check("LTV = 月次粗利 ÷ 解約率", d["ltv"] == 2000000, d.get("ltv"))
check("LTV/CAC", abs(d["ltv_cac"] - 2000000 / 300000) < 1e-9, d.get("ltv_cac"))
check("CAC回収期間 = CAC ÷ 月次粗利", d["cac_payback"] == 7.5, d.get("cac_payback"))
check("損益分岐顧客数 = 固定費 ÷ 月次粗利", d["breakeven_customers"] == 50, d.get("breakeven_customers"))
check("12ヶ月後の年換算売上", d["arr_12m"] == 60 * 50000 * 12, d.get("arr_12m"))
check("妥当な前提では警告なし", w == [], w)

# --- ランウェイ ---
v = A.normalize(full(operating_income_monthly=-12000000, cash_on_hand=96000000))
d, _ = derived_map(v)
check("ランウェイ = 手元資金 ÷ 月次赤字", d["runway"] == 8, d.get("runway"))

v = A.normalize(full(operating_income_monthly=500000, cash_on_hand=10000000))
d, _ = derived_map(v)
check("黒字ならランウェイは算出しない（資金は減らない）", d["runway"] is None, d)

# 営業損益が未入力でも、売上・粗利率・固定費から計算する
v = A.normalize(full(monthly_revenue_now=1000000, gross_margin_pct=60, fixed_cost_monthly=1000000,
                     cash_on_hand=4000000))
d, _ = derived_map(v)
check("営業損益を計算で補う", d["operating_income_calc"] == -400000, d.get("operating_income_calc"))
check("計算した赤字からランウェイを出す", d["runway"] == 10, d.get("runway"))

# --- 警告 ---
v = A.normalize(full(arpu_monthly=50000, gross_margin_pct=80, churn_monthly_pct=0))
_, w = derived_map(v)
check("解約率0%は警告（LTVが無限大になる）", any("解約率が0%" in x for x in w), w)

v = A.normalize(full(monthly_revenue_now=10000000, customers_now=10, arpu_monthly=50000))
_, w = derived_map(v)
check("売上と顧客数×単価の食い違いを警告", any("一致しません" in x for x in w), w)

v = A.normalize(full(arpu_monthly=50000, gross_margin_pct=90, churn_monthly_pct=0.5, cac=10000))
_, w = derived_map(v)
check("LTV/CACが極端に高いと警告", any("極端に高い" in x for x in w), w)

# --- 不正な値を捨てる ---
v = A.normalize(full(gross_margin_pct=7000, churn_monthly_pct=-3, arpu_monthly=-50000,
                     operating_income_monthly=-100))
check("粗利率7000%は捨てる（単位違い）", v["gross_margin_pct"]["value"] is None)
check("負の解約率は捨てる", v["churn_monthly_pct"]["value"] is None)
check("負の単価は捨てる", v["arpu_monthly"]["value"] is None)
check("営業損益はマイナスを許す", v["operating_income_monthly"]["value"] == -100)
check("捨てた値の出所は不明になる", v["gross_margin_pct"]["source"] == "不明")

v = A.normalize({"arpu_monthly": {"value": "50,000円", "source": "入力", "rationale": ""}})
check("文字列の数値も読む", v["arpu_monthly"]["value"] == 50000, v["arpu_monthly"])
check("欠けた項目は不明で埋める", v["cac"] == {"value": None, "source": "不明", "rationale": ""}, v["cac"])

v = A.normalize({"cac": {"value": 0, "source": "不明", "rationale": ""}})
check("不明の値は捨てる（ダミーの0を前提に流さない）", v["cac"]["value"] is None, v["cac"])

v = A.normalize({"arpu_monthly": {"value": 50000, "source": "input", "rationale": ""},
                 "cac": {"value": 1, "source": "推定値", "rationale": ""},
                 "debt": {"value": 1, "source": "unknown", "rationale": ""}})
check("出所の表記揺れを寄せる（input→入力）", v["arpu_monthly"]["source"] == "入力", v["arpu_monthly"])
check("出所の表記揺れを寄せる（推定値→推定）", v["cac"]["source"] == "推定", v["cac"])
check("出所の表記揺れを寄せる（unknown→不明、値も捨てる）", v["debt"] == {"value": None, "source": "不明", "rationale": ""}, v["debt"])

# --- 計算で決まる値は推定値を捨てて上書きする ---
raw = full(customers_now=2, arpu_monthly=50000, gross_margin_pct=80, fixed_cost_monthly=2500000)
raw["monthly_revenue_now"] = item(200000, "推定", "2社×5万円=10万円")  # 実測で起きた食い違い
raw["operating_income_monthly"] = item(-2400000, "推定", "概算")
v = A.reconcile(A.normalize(raw))
check("推定した売上を顧客数×単価で上書きする",
      v["monthly_revenue_now"] == {"value": 100000, "source": "計算", "rationale": "現在の顧客数 × 月額単価"},
      v["monthly_revenue_now"])
check("推定した営業損益を計算値で上書きする",
      v["operating_income_monthly"]["value"] == 100000 * 0.8 - 2500000
      and v["operating_income_monthly"]["source"] == "計算", v["operating_income_monthly"])
_, w = A.compute_derived(v)
check("上書き後は食い違いの警告が出ない", not any("一致しません" in x for x in w), w)

raw = full(customers_now=10, arpu_monthly=50000, monthly_revenue_now=10000000)
v = A.reconcile(A.normalize(raw))
check("入力された売上は上書きしない", v["monthly_revenue_now"]["value"] == 10000000
      and v["monthly_revenue_now"]["source"] == "入力", v["monthly_revenue_now"])
_, w = A.compute_derived(v)
check("入力同士の食い違いは警告で知らせる", any("一致しません" in x for x in w), w)

# --- 1年目の見込み（NPVのY1と財務CFを揃えるための値） ---
v = A.normalize(full(customers_now=2, customers_12m=60, arpu_monthly=50000, one_time_fee=300000,
                     gross_margin_pct=80, fixed_cost_monthly=2500000))
d, _ = derived_map(v)
check("1年目の売上は期中平均顧客数で計算する",
      d["y1_revenue"] == (2 + 60) / 2 * 50000 * 12 + 58 * 300000, d.get("y1_revenue"))
check("1年目の営業損益（この条件では赤字）",
      d["y1_operating_income"] == d["y1_revenue"] * 0.8 - 2500000 * 12 and d["y1_operating_income"] < 0,
      d.get("y1_operating_income"))
check("年末顧客数×12より小さい（過大計上しない）", d["y1_revenue"] < 60 * 50000 * 12 + 58 * 300000)

# --- JSON の読み取り ---
payload = json.dumps(full(arpu_monthly=1000), ensure_ascii=False)
check("素のJSONを読む", A.parse(payload)["arpu_monthly"]["value"] == 1000)
check("コードフェンス付きを読む", A.parse("```json\n" + payload + "\n```")["arpu_monthly"]["value"] == 1000)
check("前後の説明文があっても読む", A.parse("結果です\n" + payload + "\n以上")["arpu_monthly"]["value"] == 1000)
try:
    A.parse("JSONではありません")
    check("壊れた出力は例外にする", False)
except ValueError:
    check("壊れた出力は例外にする", True)

# --- 表示 ---
check("金額は万円で表示", A.format_value(50000, "yen") == "5万円", A.format_value(50000, "yen"))
check("億円で表示", A.format_value(1200000000, "yen") == "12億円", A.format_value(1200000000, "yen"))
check("赤字は記号付きで表示", A.format_value(-12000000, "yen") == "−1,200万円", A.format_value(-12000000, "yen"))
check("少額は円で表示", A.format_value(3000, "yen") == "3,000円", A.format_value(3000, "yen"))

v = A.normalize(full(arpu_monthly=50000, gross_margin_pct=80, churn_monthly_pct=2, cac=300000))
d, w = A.compute_derived(v)
md = A.render(v, d, w)
check("表に入力値が出る", "| 顧客あたり月額単価 | 5万円 | 入力 |" in md, md[:300])
check("不明の項目を一覧で示す", "不明（推定していない項目）" in md and "手元資金" in md)
check("派生指標の表と計算式が出る", "| LTV（顧客生涯粗利） | 200万円 |" in md, md)
piped = A.normalize(full())
piped["cac"] = {"value": 1000, "source": "推定", "rationale": "広告費|営業費の合計"}
row = [ln for ln in A.render(piped, [], []).splitlines() if ln.startswith("| 顧客獲得コスト")][0]
check("根拠中の縦棒で表の列が崩れない", row.count("|") == 5 and "広告費／営業費" in row, row)
ctx = A.context_block(md)
check("後続セクション向けの文脈に厳守ルールが入る", "この表の値だけを使って" in ctx and md in ctx)

print("\nfailures:", fails)
sys.exit(1 if fails else 0)
