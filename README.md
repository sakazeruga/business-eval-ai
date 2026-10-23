# 事業評価AIサービス

事業構想を入力すると、構造化フレームワークで多角的分析を行い、現状評価と改善案を出力するAIサービス。

> クローズドベータ版 — G's Academy / Globis アントレプレナークラブ / COO代行向け

---

## 評価フレームワーク

### スタートアップ評価モード

| セクション | 内容 | 担当モデル |
|---|---|---|
| **Why Me** | 創業者適性（情報収集力・継続性・巻き込み力）の3因子乗算スコア | Claude Haiku 5.5 |
| **Why Now** | 市場成長性 × 潜在爆発力 × 社会還元性 | Claude Haiku 5.5 + Web検索（Perplexity） |
| **競合分析** | 主要5社の自動リサーチ＋KBF（購買決定要因）/ KSF（重要成功要因）の特定 | Claude Haiku 5.5 + Web検索（Perplexity） |
| **VRIO分析** | 持続的競争優位の4軸評価 | Claude Haiku 5.5 |
| **マーケティング** | GTM戦略・CAC/LTV試算・チャネル蓋然性 | Claude Haiku 5.5 |
| **オペレーション** | 実行体制・クリティカルパス・ボトルネック | GPT-6 Luna |
| **キャッシュフロー** | CF循環構造・資金調達計画との整合性 | Claude Haiku 5.5 |
| **NPV / EXIT** | 財務仮定の自動生成 → DCF / EXIT事業価値算出 | Claude Haiku 5.5 |
| **クロスSWOT** | SO/ST/WO/WT戦略＋優先3手 | Claude Sonnet 5.5 |

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
OpenRouter（ZDR強制）
    ├── anthropic/claude-haiku-5.5   Why Me / VRIO / マーケ / CF / NPV・EXIT
    ├── anthropic/claude-sonnet-5.5  クロスSWOT（統合判断）
    └── openai/gpt-6-luna            オペレーション蓋然性
Perplexity Agent API（Web検索 + claude-haiku-5-5）
    ├── 競合自動リサーチ
    └── Why Now（市場データの検索・出典付き）
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

## 利用許可の管理

Cloudflare Access は「本人か」を確認するだけなので、ポリシーを緩めると誰でも入れる。
「使ってよいか」はアプリ側で持ち、Cloudflare の画面を触らずに承認できる。

| 方法 | 使いどころ |
|---|---|
| 申請 → `/admin` でワンタップ承認 | 個別に許可する |
| 招待リンク / QR（期限・人数の上限つき） | 講座や説明会で一斉に配る |
| `AUTO_APPROVE_DOMAINS` によるドメイン自動承認 | 社内・特定校の全員に開放する |

- 未承認の利用者は `/no-access` に誘導され、評価も調査も走らないため費用は発生しない
- `/admin` は `ADMIN_EMAILS` のみ。管理者は利用上限も受けない
- 承認リストと利用回数は Upstash Redis に保存する。未設定ならメモリで動くが、
  マシンの再起動で消え、複数マシン間でも共有されない

## テスト

外部APIを呼ばないため費用はかからない。

```bash
python tests/test_access.py   # 承認・招待・権限
python tests/test_limits.py   # 利用上限・入力サイズ・同時実行
python tests/test_stream.py   # 評価ストリーミングの回帰
```

## コスト目安

| 構成 | 1回あたり概算 |
|---|---|
| 全セクション実行 | 約 $0.06（約 9 円） |
| うちクロスSWOT（Sonnet 5.5） | 約 $0.05（全体の約 8 割） |

> Haiku 5.5 / Luna は推論を無効化（`reasoning.enabled=false`）。既定のままだと推論トークンが毎回変動し、`max_tokens` を食い潰して本文が切れる。Sonnet 5.5 は推論を無効化できないため `effort: medium`。
> 要件: 学習利用なし（OpenRouter は `provider.zdr=true`）／非中華モデルのみ。

---

## ライセンス

Private / Closed Beta — 無断転用・再配布禁止
