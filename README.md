# 事業評価AIサービス

事業構想を入力すると、構造化フレームワークで多角的分析を行い、現状評価と改善案を出力するAIサービス。

> クローズドベータ版 — G's Academy / Globis アントレプレナークラブ / COO代行向け

---

## 評価フレームワーク

### スタートアップ評価モード

| セクション | 内容 | 担当モデル |
|---|---|---|
| **Why Me** | 創業者適性（情報収集力・継続性・巻き込み力）の3因子乗算スコア | Claude Haiku |
| **Why Now** | 市場成長性 × 潜在爆発力 × 社会還元性 | Gemini 3.5 Flash |
| **競合調査** | 主要5社の自動リサーチ＋差別化軸 | Perplexity Sonar |
| **VRIO分析** | 持続的競争優位の4軸評価 | Claude Haiku |
| **マーケティング** | GTM戦略・CAC/LTV試算・チャネル蓋然性 | Claude Haiku |
| **オペレーション** | 実行体制・クリティカルパス・ボトルネック | GPT-4o |
| **キャッシュフロー** | CF循環構造・資金調達計画との整合性 | Claude Haiku |
| **NPV / EXIT** | 財務仮定の自動生成 → DCF / EXIT事業価値算出 | DeepSeek R1 |
| **クロスSWOT** | SO/ST/WO/WT戦略＋優先3手 | Claude Haiku |

### COOターンアラウンドモード

既存事業の財務診断 → 穴塞ぎ → アクセラレーター → フロー改善 → 文化構築の優先度設計。

---

## Why Me 評価設計

```
Why Me スコア = F1(情報収集力) × F2(継続性・公的能力) × F3(巻き込み力)
```

- **Factor 1** — 競合5社をどれだけ正確に把握しているか
- **Factor 2** — `[継続性 × 0.6] + [資格係数A × 実践スコアB + AI加点] × 0.4`
  - 資格係数Aはメンバーの役割（CEO / フルコミット / コミッタブル）で重み付け
- **Factor 3** — 1次ドア（直接資源アクセス）× 2次ドア（コミュニティブローカー）保有数

---

## 技術構成

```
FastAPI + Jinja2 (SSR)
    ↓
OpenRouter (マルチモデルゲートウェイ)
    ├── anthropic/claude-haiku-4-5   Why Me / VRIO / マーケ / CF / クロスSWOT
    ├── google/gemini-3.5-flash      Why Now（web検索付き市場評価）
    ├── openai/gpt-4o                オペレーション蓋然性
    ├── deepseek/deepseek-r1         NPV / DCF / EXIT算出
    └── perplexity/sonar             競合自動リサーチ
```

各セクションは `asyncio.gather` で並列実行。クロスSWOTのみ全セクション結果を集約してから生成。

---

## セットアップ

### 前提

- Python 3.11+
- [OpenRouter](https://openrouter.ai) アカウント（API キー）

### インストール

```bash
git clone https://github.com/sakazeruga/business-eval-ai.git
cd business-eval-ai
pip install -r requirements.txt
```

### 環境変数

`.env` ファイルをプロジェクトルートに作成:

```env
OPENROUTER_API_KEY=sk-or-xxxxxxxxxxxxxxxx
```

### 起動

```bash
py -m uvicorn main:app --reload
```

`http://localhost:8000` をブラウザで開く。

---

## 入力フォーム

### 事業構想

| フィールド | 説明 |
|---|---|
| 解決する課題 | 誰の・どんな問題か |
| ソリューション | どう解決するか |
| ビジネスモデル | どう収益化するか |
| 現在のフェーズ | アイデア段階 / β版 / 月商XX円 など |

### Why Me（スタートアップモードのみ）

| フィールド | 説明 |
|---|---|
| あなたのポジション | CEO / フルコミット共同創業者 / コミッタブルメンバー |
| フルコミットメンバー | 他のフルコミットメンバーと概要 |
| コミッタブルメンバー | 週20h以上コミット可能なメンバー |
| Q2-1: 継続性 | 最も長く取り組んだこと・挫折回数 |
| Q2-2: チーム資格・職歴 | メンバー別に役割＋資格・職歴を記載 |
| Q2-3: 経験の活かし方 | 事業との具体的な接点 |
| Q2-4: AI活用度 | 業務でのAIツール活用状況 |
| Q3-1: 信頼ネットワーク | 1万円貸してくれる人の数・重複度 |
| Q3-2: 1次/2次ドア | 直接資源アクセス者 / コミュニティブローカーの数 |

---

## 出力

- 各セクションをカラーコード別に表示（分析系 / 財務系 / 戦略系）
- 使用モデルをセクションごとに表示
- **AI壁打ち用エクスポート** — 全評価結果をMarkdown形式でコピー / ダウンロード

---

## コスト目安

| 構成 | 1回あたり概算 |
|---|---|
| 全セクション実行 | $0.05〜$0.15 |
| DeepSeek R1（NPV）| $0.01〜$0.03 |
| Perplexity Sonar（競合）| $0.005〜$0.01 |

> Extended Thinking は明示的に無効化済み（`thinking: {type: "disabled"}`）。Claude の思考トークン課金は発生しません。

---

## ライセンス

Private / Closed Beta — 無断転用・再配布禁止
