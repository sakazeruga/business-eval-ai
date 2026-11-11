"""前提数値: レポート全体で共通に使う数値の唯一の出所。

マーケティング・財務CF・NPV の各セクションは並列に走り、互いの出力を
見ないまま CAC・LTV・単価・損益分岐点などをそれぞれ推定していた。
そのため1本のレポートの中で数字が食い違っていた。

そこで評価の前に一度だけ数値を確定し、全セクションへ同じ表を渡す。
- 素の数値（単価・粗利率・解約率など）は LLM が入力から抽出または推定する
- 派生指標（LTV・回収期間・損益分岐顧客数・ランウェイなど）は Python で計算する。
  LLM の計算誤りを持ち込まないため

このモジュールは外部APIを呼ばない（テストしやすいように純粋な処理だけを置く）。
"""

import json
import re

# (キー, 表示名, 単位, LLMへの説明)
# 単位: yen=円, pct=%, count=件数, multiple=倍
FIELDS: list[tuple[str, str, str, str]] = [
    ("customers_now", "現在の有料顧客数", "count", "社または人。新規事業で未開始なら0"),
    ("customers_12m", "12ヶ月後の有料顧客数", "count", "計画値または見込み"),
    ("arpu_monthly", "顧客あたり月額単価", "yen", "サブスクでなければ月あたりに換算"),
    ("one_time_fee", "初期・導入費（1顧客あたり）", "yen", "無ければ0"),
    ("gross_margin_pct", "粗利率", "pct", "売上に対する粗利の割合"),
    ("fixed_cost_monthly", "月次固定費", "yen", "人件費・家賃・ツール費などの合計"),
    ("cac", "顧客獲得コスト（CAC）", "yen", "1顧客を獲得するのにかかる営業・広告費"),
    ("churn_monthly_pct", "月次解約率", "pct", "継続課金でなければ不明"),
    ("monthly_revenue_now", "現在の月次売上", "yen", "既存事業は実績。新規事業で未開始なら0"),
    ("operating_income_monthly", "現在の月次営業損益", "yen", "赤字はマイナスの数値"),
    ("cash_on_hand", "手元資金", "yen", "現時点の現預金"),
    ("debt", "有利子負債", "yen", "借入金など。無ければ0"),
    ("initial_investment", "初期投資", "yen", "立ち上げに必要な一時費用。既存事業では不明でよい"),
    ("growth_y1_y3_pct", "年平均成長率（1→3年目）", "pct", "売上の年平均成長率"),
    ("growth_y3_y5_pct", "年平均成長率（3→5年目）", "pct", "売上の年平均成長率"),
    ("discount_rate_pct", "割引率", "pct", "NPV算出用。スタートアップは20〜40%が目安"),
    ("ev_revenue_multiple", "EV/売上倍率", "multiple", "同業の企業価値/売上倍率"),
]

FIELD_KEYS = [k for k, *_ in FIELDS]
LABELS = {k: label for k, label, *_ in FIELDS}
UNITS = {k: unit for k, _, unit, _ in FIELDS}
SOURCES = ["入力", "推定", "不明"]

# 値の妥当な範囲。外れた値は誤り（単位違いなど）とみなして捨てる
_RANGES = {
    "gross_margin_pct": (-100, 100),
    "churn_monthly_pct": (0, 100),
    "discount_rate_pct": (0, 100),
    "growth_y1_y3_pct": (-100, 2000),
    "growth_y3_y5_pct": (-100, 2000),
    "ev_revenue_multiple": (0, 200),
}
# マイナスを許す項目（それ以外の金額・件数は0以上）
_SIGNED = {"operating_income_monthly", "gross_margin_pct", "growth_y1_y3_pct", "growth_y3_y5_pct"}


def field_lines() -> str:
    """LLM へ渡す項目一覧"""
    unit_text = {"yen": "円", "pct": "%", "count": "件", "multiple": "倍"}
    return "\n".join(
        f"- {key}: {label}（単位: {unit_text[unit]}）… {hint}"
        for key, label, unit, hint in FIELDS
    )


def schema() -> dict:
    """構造化出力用の JSON Schema（strict モード対応）

    value を null 許容（型の和）にすると、項目数が多いとき OpenRouter/Anthropic が
    400 を返す（実測: null 許容 8項目は可、16項目以上で 400）。
    そのため value は常に数値とし、不明は source="不明" で表す（値は捨てる）。
    """
    item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "value": {"type": "number"},
            "source": {"type": "string", "enum": SOURCES},
            "rationale": {"type": "string"},
        },
        "required": ["value", "source", "rationale"],
    }
    props = {k: item for k in FIELD_KEYS}
    props["notes"] = {"type": "string"}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": props,
        "required": FIELD_KEYS + ["notes"],
    }


def parse(raw: str) -> dict:
    """LLM の出力から JSON を取り出す。コードフェンスや前後の説明文があっても読む。"""
    text = (raw or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"前提数値のJSONを読み取れません: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("前提数値のJSONがオブジェクトではありません")
    return data


def _to_number(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace(",", "").replace("円", "").replace("%", "").strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


# 構造化出力が使えず通常出力で読んだ場合、出所の表記が揺れるので寄せる
_SOURCE_ALIASES = {
    "入力": "入力", "入力値": "入力", "入力済み": "入力", "記載": "入力", "明記": "入力",
    "input": "入力", "given": "入力", "provided": "入力",
    "推定": "推定", "推定値": "推定", "仮定": "推定", "想定": "推定",
    "estimate": "推定", "estimated": "推定", "assumed": "推定",
    "不明": "不明", "unknown": "不明", "none": "不明", "null": "不明", "n/a": "不明",
}


def _source(raw) -> str:
    s = str(raw or "").strip()
    return _SOURCE_ALIASES.get(s, _SOURCE_ALIASES.get(s.lower(), "推定"))


def normalize(data: dict) -> dict:
    """各項目を {value: float|None, source, rationale} に揃え、範囲外の値を捨てる。"""
    out = {}
    for key in FIELD_KEYS:
        item = data.get(key)
        if not isinstance(item, dict):
            item = {"value": item}
        value = _to_number(item.get("value"))
        source = _source(item.get("source"))
        rationale = str(item.get("rationale") or "").strip()

        # 構造化出力では value が必須の数値なので、不明の項目にも 0 などが入ってくる。
        # 「不明」の値は使わない（ダミーの 0 を前提として流さないため）
        if source == "不明":
            value = None
        if value is not None:
            lo, hi = _RANGES.get(key, (None, None))
            if key not in _SIGNED and value < 0:
                value = None
            elif lo is not None and not (lo <= value <= hi):
                value = None
        if value is None:
            source = "不明"
        out[key] = {"value": value, "source": source, "rationale": rationale}
    out["notes"] = str(data.get("notes") or "").strip()
    return out


def reconcile(values: dict) -> dict:
    """他の項目から計算で決まる値は、LLM の推定値を捨てて計算値で上書きする。

    実測で、LLM が「2社 × 5万円 = 10万円」と根拠に書きながら値には 20万円を
    入れる例があった。入力そのもの（source=入力）は上書きしない。
    """
    v = {k: values[k]["value"] for k in FIELD_KEYS}

    def overwrite(key, value, rationale):
        if values[key]["source"] != "入力":
            values[key] = {"value": value, "source": "計算", "rationale": rationale}
            v[key] = value

    if v["customers_now"] is not None and v["arpu_monthly"] is not None:
        overwrite("monthly_revenue_now", v["customers_now"] * v["arpu_monthly"], "現在の顧客数 × 月額単価")
    if v["monthly_revenue_now"] is not None and v["gross_margin_pct"] is not None \
            and v["fixed_cost_monthly"] is not None:
        overwrite("operating_income_monthly",
                  v["monthly_revenue_now"] * v["gross_margin_pct"] / 100 - v["fixed_cost_monthly"],
                  "現在の月次売上 × 粗利率 − 月次固定費")
    return values


def compute_derived(values: dict) -> tuple[list[dict], list[str]]:
    """前提数値から派生指標を計算する。(指標の一覧, 警告の一覧) を返す。"""
    v = {k: values[k]["value"] for k in FIELD_KEYS}
    derived: list[dict] = []
    warnings: list[str] = []

    def add(key, label, value, unit, formula):
        derived.append({"key": key, "label": label, "value": value, "unit": unit, "formula": formula})

    arpu, gm_pct = v["arpu_monthly"], v["gross_margin_pct"]
    gp = arpu * gm_pct / 100 if arpu is not None and gm_pct is not None else None
    if gp is not None:
        add("gross_profit_per_customer", "1顧客あたり月次粗利", gp, "yen", "月額単価 × 粗利率")

    churn = v["churn_monthly_pct"]
    ltv = None
    if gp is not None and churn is not None:
        if churn > 0:
            ltv = gp / (churn / 100)
            add("ltv", "LTV（顧客生涯粗利）", ltv, "yen", "1顧客あたり月次粗利 ÷ 月次解約率")
        else:
            warnings.append("月次解約率が0%のため、LTVを算出できません。現実的な解約率を設定してください。")

    cac = v["cac"]
    if ltv is not None and cac:
        ratio = ltv / cac
        add("ltv_cac", "LTV/CAC", ratio, "multiple", "LTV ÷ CAC（3倍以上が目安）")
        if ratio > 20:
            warnings.append(f"LTV/CACが{ratio:.0f}倍と極端に高い値です。解約率かCACの前提が楽観的な可能性があります。")
    if gp and cac is not None and gp > 0:
        add("cac_payback", "CAC回収期間", cac / gp, "months", "CAC ÷ 1顧客あたり月次粗利")

    fixed = v["fixed_cost_monthly"]
    if fixed is not None and gp and gp > 0:
        add("breakeven_customers", "損益分岐に必要な顧客数", fixed / gp, "count", "月次固定費 ÷ 1顧客あたり月次粗利")

    # 現在の売上: 明示値を優先し、無ければ顧客数 × 単価
    mrr = v["monthly_revenue_now"]
    implied = v["customers_now"] * arpu if v["customers_now"] is not None and arpu is not None else None
    if mrr is not None and implied is not None and max(mrr, implied) > 0:
        gap = abs(mrr - implied) / max(mrr, implied)
        if gap > 0.25:
            warnings.append(
                f"現在の月次売上（{format_value(mrr, 'yen')}）と、顧客数×月額単価"
                f"（{format_value(implied, 'yen')}）が一致しません。どちらかの前提を確認してください。"
            )
    if mrr is None:
        mrr = implied

    op = v["operating_income_monthly"]
    if op is None and mrr is not None and gm_pct is not None and fixed is not None:
        op = mrr * gm_pct / 100 - fixed
        add("operating_income_calc", "現在の月次営業損益（計算値）", op, "yen", "月次売上 × 粗利率 − 月次固定費")

    cash = v["cash_on_hand"]
    if op is not None and op < 0 and cash is not None:
        add("runway", "ランウェイ", cash / -op, "months", "手元資金 ÷ 月次の赤字額")
    elif op is not None and op >= 0 and cash is not None:
        add("runway", "ランウェイ", None, "months", "営業黒字のため資金は減らない")

    if v["customers_12m"] is not None and arpu is not None:
        add("arr_12m", "12ヶ月後の年換算売上", v["customers_12m"] * arpu * 12, "yen", "12ヶ月後の顧客数 × 月額単価 × 12")

        # 1年目の実績見込み。顧客は期中に増えるので、年末の顧客数で12ヶ月分を
        # 計上すると売上を過大に見積もる（NPV と財務CFが食い違う原因だった）
        start = v["customers_now"] or 0
        end = v["customers_12m"]
        fee = v["one_time_fee"] or 0
        y1_revenue = (start + end) / 2 * arpu * 12 + max(end - start, 0) * fee
        add("y1_revenue", "1年目の売上（期中平均）", y1_revenue, "yen",
            "期中平均顧客数 × 月額単価 × 12 ＋ 新規顧客数 × 導入費")
        if gm_pct is not None and fixed is not None:
            add("y1_operating_income", "1年目の営業損益", y1_revenue * gm_pct / 100 - fixed * 12, "yen",
                "1年目の売上 × 粗利率 − 月次固定費 × 12")

    return derived, warnings


def format_value(value, unit: str) -> str:
    if value is None:
        return "—"
    if unit == "yen":
        a = abs(value)
        sign = "−" if value < 0 else ""
        if a >= 1e8:
            return f"{sign}{a / 1e8:,.2f}億円".replace(".00億", "億")
        if a >= 1e4:
            n = a / 1e4
            return f"{sign}{n:,.0f}万円" if n >= 100 else f"{sign}{n:,.1f}万円".replace(".0万", "万")
        return f"{sign}{a:,.0f}円"
    if unit == "pct":
        return f"{value:g}%"
    if unit == "count":
        return f"{value:,.0f}" if value >= 10 else f"{value:,.1f}".replace(".0", "")
    if unit == "multiple":
        return f"{value:,.1f}倍"
    if unit == "months":
        return f"{value:,.1f}ヶ月"
    return f"{value:g}"


def render(values: dict, derived: list[dict], warnings: list[str]) -> str:
    """レポートに表示する Markdown（後続セクションへもそのまま渡す）"""
    rows = ["| 項目 | 値 | 出所 | 根拠 |", "|---|---|---|---|"]
    unknown = []
    for key in FIELD_KEYS:
        item = values[key]
        if item["value"] is None:
            unknown.append(LABELS[key])
            continue
        rationale = item["rationale"].replace("|", "／").replace("\n", " ") or "—"
        rows.append(f"| {LABELS[key]} | {format_value(item['value'], UNITS[key])} | {item['source']} | {rationale} |")

    parts = ["### 前提数値（全セクション共通）", "", *rows]
    if unknown:
        parts += ["", f"**不明（推定していない項目）:** {'、'.join(unknown)}"]

    if derived:
        parts += ["", "### 自動計算した指標", "",
                  "| 指標 | 値 | 計算式 |", "|---|---|---|"]
        for d in derived:
            parts.append(f"| {d['label']} | {format_value(d['value'], d['unit'])} | {d['formula']} |")

    if warnings:
        parts += ["", "**前提の注意点**"]
        parts += [f"- ⚠️ {w}" for w in warnings]
    if values.get("notes"):
        parts += ["", f"> {values['notes']}"]
    return "\n".join(parts)


CONTEXT_HEADER = """## 前提数値（全セクション共通・確定）
以下は本評価の全セクションで共通に使う数値です。売上・単価・顧客数・粗利率・コスト・CAC・
LTV・損益分岐・ランウェイなどは、この表の値だけを使ってください。表と異なる数値を独自に
推定しないこと。表に無い数値が必要な場合は「推定」と明記し、表の数値と矛盾させないこと。
「不明」の項目は推定で埋めず、何を確認すれば分かるかを示すこと。"""


def context_block(markdown: str) -> str:
    """後続セクションのプロンプトに差し込む文脈"""
    return CONTEXT_HEADER + "\n\n" + markdown
