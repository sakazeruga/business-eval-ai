# 事業評価AIの外部データ源とRAG最小設計

調査基準日：2026年11月17日。対象：日本のスタートアップ・中小企業を評価する、FastAPI＋Jinja2／Fly.io上のクローズドβサービス。

推奨は**A：出典付き数値表、B：許諾済み政府文書の小規模RAG、C：最新情報の検索**である。数値はSQL・JSONで取得し、文書検索は定義・背景を補う。本書の構成・選定順位・サイズ・運用頻度・試算・品質基準は設計提案であり、提供元の保証ではない。

先に解消すべき条件は、外部検索への機密入力と、ベンチマークの商用利用許諾である。PerplexityのZDRは現在、デフォルトではなく書面合意に基づくアカウント設定であり、さらにSearch AddendumはSearch Servicesに他製品のZDR義務が及ばないと規定しているため、現行の「web_search」という名称だけで要件充足とは判断できない。([Perplexity Privacy & Security](https://docs.perplexity.ai/docs/resources/privacy-security)、[Search Addendum §1.2.2・§2.2](https://www.perplexity.ai/hub/legal/perplexity-api-terms-of-service-search))

## データ源の表

### 読み方・許諾の判定

- **A**：取得時点・定義・母集団を固定して保持する構造化ベンチマーク。JSONだけでなく、SQLiteの通常テーブルも含む。
- **B**：文書を保存・チャンク化し、ベクトル検索で参照する文書群。
- **C**：実行時の検索による発見・原典確認。表の「条件付き」「保留」は、検索結果を介して利用許諾を迂回してよいという意味ではない。
- **補助API**：法人照合や補助金照会は、本来A・B・Cと直交する構造化取得である。三分類に合わせて主用途を記し、必要なものは「直接API」を併記する。最新値をベクトルDBに押し込む設計にはしない。
- **公開／料金明文未確認**：公開ページ・配布物を確認したが、無料提供を明記した料金条項までは確認できなかったもの。「有料である」ことを意味しない。通信・変換・運用費は別途発生する。
- **保存・再配布の区別**：複製許諾を根拠に内部キャッシュ可能と判断する場合は「複製許諾からの判断」と記す。生データの外部再配布、顧客レポートでの抜粋、内部RAG保存は別々に審査する。

公共データ利用規約PDL1.0は、対象コンテンツについて商用利用、複製、公衆送信、翻案を認め、§1.1で出典・加工表示、§1.2で第三者権利への対応を要求するが、個別規約や第三者著作物まで一律に許諾するものではない。([デジタル庁 PDL1.0](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0))

### 市場規模・成長率、政策・社会還元性

| カテゴリ | 名称・日本データか | URL・ドメイン | 無料・有料 | API・認証 | 提供元の更新頻度 | キャッシュ・再配布・商用再利用と根拠 | 振り分け |
|---|---|---|---|---|---|---|---|
| 市場規模・成長率 | e-Stat。国内統計の入口。統計ごとに対象を確認 | [e-Stat規約](https://www.e-stat.go.jp/terms-of-use)、`e-stat.go.jp` | 公開／API料金の明文は未確認 | ユーザー登録＋アプリケーションID。([利用ガイド](https://www.e-stat.go.jp/api/api-info/api-guide)) | 統計ごとに異なる。一律の周期ではない | 商用・複製・公衆送信・加工可。内部保存可は複製許諾からの判断。出典と加工表示、第三者権利・外部連携元の条件に注意。([e-Stat利用規約1）・2）](https://www.e-stat.go.jp/terms-of-use)) | **A主**。統計の定義・利用上の注意はB。APIは定期取得用 |
| 財務・市場構造 | 中小企業実態基本調査。日本の中小企業、業種・規模別 | [調査入口](https://www.chusho.meti.go.jp/koukai/chousa/kihon/index.html)、`chusho.meti.go.jp` | 公開／料金明文未確認 | 個別サイトの専用APIは未確認。e-Stat経由は、対象表がAPI対象か表単位で確認 | 毎年。速報は翌年2月下旬頃、確報は6月下旬頃。([調査説明](https://www.chusho.meti.go.jp/koukai/chousa/kihon/index.html)) | 転載時の調査名表示を要求。サイト利用はMETIのPDL準拠ルールと個別注記を確認。内部保存は複製許諾の範囲から判断。([調査説明](https://www.chusho.meti.go.jp/koukai/chousa/kihon/index.html)、[METI利用規約](https://www.meti.go.jp/main/rules.html)) | **A最優先**。定義・標本・集計方法はB |
| 市場・政策 | 中小企業白書・小規模企業白書。日本 | [白書入口](https://www.chusho.meti.go.jp/pamflet/hakusyo/index.html)、`chusho.meti.go.jp` | 公開／料金明文未確認 | 専用APIは未確認。HTML／PDFが入口 | 年版ごと。2026年版も掲載。([白書一覧](https://www.chusho.meti.go.jp/pamflet/hakusyo/index.html)) | METIは原則PDL1.0準拠。第三者の調査図表・写真・転載資料は除外審査し、自動的に全ページ許諾済みと扱わない。([METI利用規約](https://www.meti.go.jp/main/rules.html)) | **B最優先**。権利確認済みの基礎数値だけA |
| 市場規模・成長率 | METI電子商取引に関する市場調査。国内ECと越境ECを区別 | [2026年公表ページ](https://www.meti.go.jp/press/2026/10/20261021001.html)、`meti.go.jp` | 公開／料金明文未確認 | 専用APIは未確認。公表ページ・報告書 | 年度調査。([公表ページ](https://www.meti.go.jp/press/2026/10/20261021001.html)) | METIのPDL準拠。委託報告書内の第三者引用・個別権利表示を確認してから保存・外部表示。([METI利用規約](https://www.meti.go.jp/main/rules.html)) | **A＋B**。対象業種をECに広げる際の追加候補 |
| 補助金・政策 | Jグランツ／jGrants。日本の掲載補助金 | [公式API仕様](https://developers.digital.go.jp/documents/jgrants/api/)、`api.jgrants-portal.go.jp` | 公開／API料金の明文未確認 | 一覧v1、詳細v1／v2。公式ページで認証不要との明文は未確認。([API仕様](https://developers.digital.go.jp/documents/jgrants/api/)) | 掲載・修正随時。固定更新周期・反映SLAは未確認 | §4は公開情報の集計・状況確認等をする利用者を想定。§5.1はサービス提供・加工と出典表示、§5.2は第三者権利処理を規定。商用可・保存期間・生データ再配布の包括的明文は未確認。内部保存は条件付き判断とし、添付公募要領は別審査。([Web-API利用規約](https://fs2.jgrants-portal.go.jp/API%E5%88%A9%E7%94%A8%E8%A6%8F%E7%B4%84.pdf)) | **C＋直接API主**。Bへの公募要領蓄積は後回し |
| 支援策・経営解説 | J-Net21。日本の中小企業向け | [サイト利用条件](https://j-net21.smrj.go.jp/rule/)、`j-net21.smrj.go.jp` | 無償サービス。([利用条件](https://j-net21.smrj.go.jp/rule/)) | 公開APIは未確認 | コンテンツごと。固定周期は未確認 | 「著作権」で事前承諾なしの複製・公開・送信・転載・再利用等を禁止。個別注記がなければリンクは原則自由、出典明記。([サイト利用条件](https://j-net21.smrj.go.jp/rule/)) | **C条件付き**。発見後は制度実施機関の原典へ。無許諾のB化は不可 |
| 再生・政策 | 中小企業活性化協議会の収益力改善支援実施要領。日本 | [2026年改正PDF](https://www.chusho.meti.go.jp/keiei/saisei/2026/260326kaitei_2/001.pdf)、`chusho.meti.go.jp` | 公開／料金明文未確認 | 専用APIは未確認 | 改正時。資料に2026年3月31日改正の記載。([実施要領](https://www.chusho.meti.go.jp/keiei/saisei/2026/260326kaitei_2/001.pdf)) | METIのPDL準拠・個別例外を確認。これは支援制度・業務手順の根拠で、業種別の健全性基準そのものではない。([METI利用規約](https://www.meti.go.jp/main/rules.html)、[実施要領](https://www.chusho.meti.go.jp/keiei/saisei/2026/260326kaitei_2/001.pdf)) | **B**。ターンアラウンドの定性補助 |

### バリュエーション、資本コスト、業種別財務指標

| カテゴリ | 名称・日本データか | URL・ドメイン | 無料・有料 | API・認証 | 提供元の更新頻度 | キャッシュ・再配布・商用再利用と根拠 | 振り分け |
|---|---|---|---|---|---|---|---|
| バリュエーション・資本コスト | Damodaran Online。Japan業種別WACC、PE、EV/Sales、EV/EBITDA等の入口あり | [Current Data](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datacurrent.html)、`pages.stern.nyu.edu` | 公開ダウンロード／商用ライセンス料未確認 | XLS等の配布。公式APIは未確認 | 原則毎年最初の2週間。一部は高頻度。2026年更新案内あり。([Data説明](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/data.html)) | 商用組み込み・定期キャッシュ・顧客への派生値提供・再配布を包括的に認める公式条項は未確認。会社単位データはデータ提供会社の制約で共有できない旨もある。無料閲覧を再利用許諾と解釈しない。([Data説明](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/data.html)) | **A候補・許諾確認まで保留**。取得不能をLLM推定値で補わない |
| 割引率の構成要素 | 財務省・国債金利情報。日本円の金利データ | [国債金利情報](https://www.mof.go.jp/jgbs/reference/interest_rate/)、`mof.go.jp` | 公開CSV／料金明文未確認 | CSV案内あり。専用APIは未確認 | 公表時刻のFAQはあるが、本調査で固定更新周期の明文は未確認 | 財務省サイトは、権利表示がなければPDL1.0適用。複製・加工・商用利用はPDL条件内、内部保存可は複製許諾からの判断。個別法令・例外に注意。([財務省サイト利用条件](https://www.mof.go.jp/about_mof/notice/)、[PDL1.0](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)) | **A**。業種WACCではなく、その構成要素の候補 |
| 景況・金利・マクロ | 日本銀行時系列統計。日本中心、系列ごとに対象確認 | [サイト留意点](https://www.stat-search.boj.or.jp/ssi/docs/info/notice.html)、`stat-search.boj.or.jp` | 公開／API料金明文未確認 | 2026年に公式API開始、JSON／CSV。マニュアルで認証・キー要否の明文は未確認。([開始告知](https://www.boj.or.jp/en/statistics/outline/notice_2026/not260218a.htm)、[マニュアル](https://www.stat-search.boj.or.jp/ssi/docs/info/api_manual.pdf)) | 統計系列ごと。遡及改定あり。([サイト留意点](https://www.stat-search.boj.or.jp/ssi/docs/info/notice.html)) | 商用目的の転載・複製は事前相談。無断改変不可。API公開サービスは連絡と指定クレジットが必要。APIがあることと商用利用許諾は別。([サイト留意点](https://www.stat-search.boj.or.jp/ssi/docs/info/notice.html)、[API留意点](https://www.stat-search.boj.or.jp/info/api_notice.pdf)) | **A候補・商用相談後**。Bへの大量取り込みは不要 |
| 業種別財務・再生 | 財務省・法人企業統計。日本の営利法人等 | [法人企業統計](https://www.mof.go.jp/pri/reference/ssc/index.htm)、`mof.go.jp` | 公開／料金明文未確認 | サイト専用APIは未確認。e-Statで利用する場合は表単位のAPI対応を確認 | 年次は9月、四半期は3・6・9・12月。([調査説明](https://www.mof.go.jp/pri/reference/ssc/index.htm)) | 財務省サイトのPDL1.0が原則。e-Stat経由なら同サイト・API規約も適用。保存・再配布は複製・公衆送信許諾の範囲から判断。([財務省利用条件](https://www.mof.go.jp/about_mof/notice/)、[e-Stat利用規約](https://www.e-stat.go.jp/terms-of-use)) | **A最優先**。定義・標本・改定注意はB |
| 業種別財務・再生 | 日本政策金融公庫「小企業の経営指標調査」。日本の小企業 | [経営指標](https://www.jfc.go.jp/n/findings/shihyou_kekka_m_index.html)、`jfc.go.jp` | 公開／商用利用料未確認 | 公式公開APIは未確認。PDF等 | 業種ごとに隔年。([調査入口](https://www.jfc.go.jp/n/findings/shihyou_kekka_m_index.html)) | 調査入口に「無断転載を禁じます」。一般サイト条件でも商用の転載・複製は事前相談。内部保存・抽出数値・派生ベンチマークの許諾範囲は未確認。([調査入口](https://www.jfc.go.jp/n/findings/shihyou_kekka_m_index.html)、[利用注意事項](https://www.jfc.go.jp/n/guideline/)) | **A候補・許諾後**。無許諾でB化しない |
| 業種別財務・再生 | TKC経営指標BAST。TKC関与先、速報版は黒字企業 | [BAST速報版](https://www.tkc.jp/tkcnf/bast/sample/)、`tkc.jp` | 原則会員外販売なし。公開速報版あり。再利用料金未確認 | 公開API未確認 | 速報の対象決算期間を表示。定期更新頻度の明文は未確認 | サイト一般条件は改編・複製・転載・再配布等を禁止。公開速報もサービスへの組み込み許諾とはみなさない。要約版は2026年8月末終了と記載され、速報版と混同しない。([著作権条件](https://www.tkc.jp/link)、[速報版](https://www.tkc.jp/tkcnf/bast/sample/)) | **A候補・許諾後**。Phase 1から除外 |
| 上場財務・比較企業 | EDINET。日本の法定開示、国内中小企業全体のDBではない | [API仕様v2](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf)、`api.edinet-fsa.go.jp` | 公開／API料金明文未確認 | APIキー、`Subscription-Key`。書類一覧・書類取得API。([公式仕様](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf)) | 当日一覧は8時30分過ぎから原則1分ごと、過去分は日次更新。これは一覧更新で、各社の提出頻度とは別。([公式仕様](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf)) | 原則PDL1.0準拠、出典・加工主体表示。タクソノミ等は別条件。APIで取れる情報のWebスクレイピングは原則禁止、公式APIを使う。([EDINET利用規約 I・2.2・3.2](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html)、[PDL1.0](https://www.digital.go.jp/resources/open_data/public_data_license_v1.0)) | **A＋B**。Phase 1は少数比較企業のみ。全件XBRL処理は後回し |
| 株価・上場企業情報 | JPX／J-Quants。日本市場 | [J-Quants利用・ライセンス](https://jpx-jquants.com/en/help/usage)、`jpx-jquants.com` | 個人版と法人用Proを区別。Proの必要契約・総額は未確認 | 個人版APIあり。本サービス向けPro認証・契約範囲は別途確認 | データセット次第 | 個人版は法人利用・第三者配布・データ利用アプリ提供を禁止。個人版を契約して商用レポートに使う案は不可。Proも外部配信・派生指標の許諾を個別確認。([公式利用条件](https://jpx-jquants.com/en/help/usage)) | **A候補・法人契約後**。Phase 1除外。公式ページのC参照も権利条件を確認 |

### SaaS KPI、競合・企業情報

| カテゴリ | 名称・日本データか | URL・ドメイン | 無料・有料 | API・認証 | 提供元の更新頻度 | キャッシュ・再配布・商用再利用と根拠 | 振り分け |
|---|---|---|---|---|---|---|---|
| SaaS KPI | SaaS Capital公開ベンチマーク。非上場B2B SaaS調査、日本代表値ではない | [2026 Bootstrapped Benchmarks](https://www.saas-capital.com/blog-posts/benchmarking-metrics-for-bootstrapped-saas-companies/)、`saas-capital.com` | 記事公開／商用再利用条件・料金未確認 | 公式公開API未確認 | 年次調査に基づく。記事は成長率・NRR・GRRを扱う。([公式記事](https://www.saas-capital.com/blog-posts/benchmarking-metrics-for-bootstrapped-saas-companies/)) | 商用の保存・再配布・派生ベンチマーク提供を認める条項は未確認。調査全体と、資金調達形態・ARRで限定した記事内集団を混同しない。([公式記事](https://www.saas-capital.com/blog-posts/benchmarking-metrics-for-bootstrapped-saas-companies/)) | **A候補・許諾後**。それまではCで原典案内に限定 |
| 日本SaaS市場・比較対象の探索 | One Capital「Japan SaaS Insights」等。日本 | [2025版](https://onecapital.jp/perspectives/japan-saas-insights-2025)、[2026 AI SaaS記事](https://onecapital.jp/perspectives/AI-SaaS-Insights)、`onecapital.jp` | 記事公開／再利用料金未確認 | 公開API未確認 | 年版・記事ごと | 商用キャッシュ・再配布許諾は未確認。確認した紹介記事は市場・調達・上場企業動向中心で、日本のCAC・LTV・NRR全体平均が揃うDBとは確認できない。([2025紹介](https://onecapital.jp/perspectives/japan-saas-insights-2025)、[2026紹介](https://onecapital.jp/perspectives/AI-SaaS-Insights)) | **C**。網羅的KPI表としてA採用しない。許諾後に必要部分のみ |
| 日本SaaS KPI・競合 | 比較企業の公式IR・法定開示。日本企業を選定 | [EDINET API仕様](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/download/ESE140206.pdf)、各社IRドメインは企業ごとに登録 | 公開資料／個社IRの商用許諾・料金は個別確認 | EDINETはAPIキー。各社IRのAPIは個別・未確認 | 提出・決算・個社開示時 | EDINET取得分は同規約、各社IRのPDFは各社規約。NRR・CAC等の項目が全社に揃うとは本調査で確認していない。出典がEDINETか個社IRかを混在させない。([EDINET利用規約](https://disclosure2dl.edinet-fsa.go.jp/guide/static/disclosure/WZEK0030.html)) | **Cから発見→許諾・定義確認後A**。比較企業5～10社から始める設計提案 |
| 法人情報・競合照合 | Gビズインフォ。日本の法人番号に紐づく行政情報 | [公式紹介](https://www.meti.go.jp/policy/digital_transformation/gbizinfo/)、[v2仕様](https://api.info.gbiz.go.jp/hojin/v3/api-docs/v2)、`info.gbiz.go.jp` | 全機能無料。([公式紹介](https://www.meti.go.jp/policy/digital_transformation/gbizinfo/)) | 申請トークンを`X-hojinInfo-api-token`ヘッダーに指定。v2を採用し、旧資料だけで実装しない。([v2仕様](https://api.info.gbiz.go.jp/hojin/v3/api-docs/v2)) | 情報提供元・項目ごと。一律の反映周期未確認 | 商用、複製、公衆送信・加工可。出典・加工表示、第三者権利・外部API由来データは元条件を継承。保存可は複製許諾からの判断。([利用規約](https://help.info.gbiz.go.jp/hc/ja/articles/4795140981406-%E5%88%A9%E7%94%A8%E8%A6%8F%E7%B4%84)) | **C＋直接API**。照合結果は期限付き構造化キャッシュ。全法人B化は不要 |
| 法人同定 | 国税庁法人番号公表サイト。日本の法人番号保有者 | [Web-API](https://www.houjin-bangou.nta.go.jp/webapi/)、`houjin-bangou.nta.go.jp` | ID発行無料。([Web-API案内](https://www.houjin-bangou.nta.go.jp/webapi/)) | アプリケーションID。案内上Ver.4.0まで、全件取得はAPIでなく配布データ。ID発行に2週間～1か月程度。([Web-API案内](https://www.houjin-bangou.nta.go.jp/webapi/)) | API案内で固定更新周期の明文は未確認 | サイトは原則PDL1.0準拠。API規約§6は取得元と非保証の表示、§7はサイト規約準拠。保存・再配布はPDL許諾と例外条件の範囲から判断。([サイト規約](https://www.houjin-bangou.nta.go.jp/riyokiyaku/index.html)、[API規約](https://www.houjin-bangou.nta.go.jp/webapi/riyokiyaku.html)) | **C＋直接API**。法人番号確定用。財務・競争優位の根拠にはしない |

表の日本SaaS候補について、**国内スタートアップ全体を代表し、解約率・CAC・LTV・NRRを一括収録し、商用再利用も明示的に許諾された公開DBは、本調査では確認できなかった**。One Capitalの紹介記事とSaaS Capitalの調査は目的・対象が異なるため、欠落を埋めるために両者を混ぜて「日本SaaS平均」を作らない。([One Capital紹介](https://onecapital.jp/perspectives/japan-saas-insights-2025)、[SaaS Capital調査記事](https://www.saas-capital.com/blog-posts/benchmarking-metrics-for-bootstrapped-saas-companies/))

## 振り分けの理由

### 採用順位と取得方法

以下は設計提案である。APIがあるデータでも、毎回APIへ接続するより、ライセンス条件を満たした定期取得・検証済みスナップショットのほうを優先する。

| 優先度 | 導入対象 | 取得・保持方法 | 選定理由・注意 |
|---|---|---|---|
| 最優先 | e-Stat／中小企業実態基本調査／法人企業統計 | 対象業種・規模の表だけ定期取得。CSV・API応答を正規化し、SQLite通常テーブル＋配布JSONへ | 日本の中小企業のものさしを先に整える。API非対応表はCSV／XLSで代替し、PDFの目視転記は二重確認 |
| 最優先 | 白書・統計の利用上の注意・再生支援要領 | 権利確認済みHTML優先、一部PDFを取り込む | Bは数字を決める装置でなく、定義・政策・背景を説明する装置にする |
| 高 | 法人番号／Gビズインフォ | 社名から候補を出し、所在地等と突合して法人番号を確定。必要時に直接API照会 | 同名企業・ブランド名と運営法人の取り違えを減らす。未収録・欠損を「実績ゼロ」としない |
| 高 | 競合公式サイト、公式IR、許諾済み一次資料 | Cで現況を確認。正規化できた数値だけAへ | 料金・機能・顧客層は変化する。競合数やKBF／KSFを法人データだけで自動確定しない |
| 中 | Jグランツ | 実行時に掲載制度を検索・照会。採用候補は実施機関の最新公募要領へ誘導 | 公募期限・対象地域・対象経費・事業フェーズが重要。採択可否や受給額を断定しない |
| 保留 | Damodaran、SaaS Capital、公庫、TKC、JPX有料情報 | 商用SaaS組み込み・内部保存・派生レポートの範囲を照会後に契約 | 公開・無料と再利用許諾を混同しない。許諾前のベクトル化で回避しない |

### 数値をベクトル検索だけに任せない

業種・年度・企業規模・通貨・連結区分・統計定義を満たす数値をSQLで抽出し、LLMには値と定義をセットで渡す。適合値がなければ`missing`または`not_comparable`を返す仕様を推奨する。次のような設計上の混同を防ぐ。

| 論点 | 推奨ルール |
|---|---|
| 売上高成長率と市場成長率 | 個別企業の成長を市場成長としない。市場範囲・集計対象・名目／実質を保存し、TAM・SAM・SOMを別計算 |
| 集計比率と企業中央値 | 「利益合計÷売上合計」と「企業別利益率の中央値」は別指標。表に中央値がなければ中央値と呼ばない |
| SaaS解約率 | ロゴ／売上、gross／net、月次／年次を分離。月次率の年率化は仮定を明示し、単純に12倍しない |
| CAC・LTV・NRR | CACに人件費を含むか、LTVが粗利ベースか、NRRの対象コホート・期間を定義。異なる定義のランキングを作らない |
| SaaS比較集団 | SMB／Enterprise、ARR、ACV、地域、成長段階、営業モデル、資金調達形態を付す。日本個社値は「参考比較企業」であり国内平均ではない |
| EVと株主価値 | マルチプルの分子・分母、対象期、現預金・有利子負債等の調整を明示。売上ゼロや赤字企業の非適用指標を無理に算出しない |
| WACCと割引率 | 円建てCFには通貨・期間整合を確認した構成要素を使う。上場業種WACCをシード企業の正解とせず、シナリオ入力と感度分析を必須化 |
| EXIT | 希薄化、残余財産分配・優先条件、ネットデット等が不明なら、企業価値と創業者手取りを同一視しない |
| 再生・財務 | 日本の業種・規模別値を優先。黒字企業のみの集団、融資先・税理士関与先等の偏りを明示し、倒産確率に直結させない |
| 社会還元性 | 補助金が存在することを社会的インパクトの証明にしない。受益者・追加性・アウトカム・負の外部性を別途評価 |

例えば、TKC速報版は純資産と当期損益がともにプラスの企業を対象としており、それを全企業の健全性基準にそのまま使うと母集団が異なる。([BAST速報版の対象説明](https://www.tkc.jp/tkcnf/bast/sample/))

Why Meの本人能力や、顧客調査のないKBF・KSFを外部引用で証明済みにしない。VRIOは比較と仮説、社会還元性は政策整合と成果を分け、オペレーションの法規は原典確認し、クロスSWOTは既存根拠を再利用する。

## RAG最小設計

### Phase 1の境界と構成

**提案：公開資料だけを格納する読み取り専用SQLite＋sqlite-vecから始め、顧客入力を共通ベクトルDBへ保存しない。** Aの構造化検索、Bの文書検索、既存の2検索枠をレポート全体で共有する。RAG追加専用のLLM判定・要約呼び出しと、有料rerankerは最初は使わない。

```text
定期更新・ビルド系：顧客入力にアクセスしない
  公式API / 許諾済みHTML・PDF
    → 利用条件・ハッシュ・公表日・統計対象期の記録
    → A：型・単位・業種・定義を検証した通常テーブル / JSON
    → B：権利確認済み本文 → チャンク → 埋め込み
    → 検証・承認 → 版付き research.sqlite → Fly.ioへ配布

レポート実行系
  入力 → 既存ZDRモデル内またはローカル処理で分類
    ├─ A：業種×規模×地域×年度でSQL検索
    ├─ B：抽象化した照会を埋め込み → ローカルベクトル検索
    └─ C：公開・一般化した語だけ → ドメイン制御検索
        ↓
  共通Evidence Bundle → 既存の各セクション生成 → 数値・引用検査
        ↓
  Jinja2：出典URL、対象期、公表日、適合度、仮定を表示
```

検索・埋め込みにも事業秘密を送らない。社名だけでなく未公開のアイデア・単価・顧客・技術も除去し、安全な一般照会にできなければCを止める。

### 対象文書・チャンク・検索

件数・サイズは初期設計値であり実測で変更する。文字数でなく実トークン数で管理する。

| 項目 | Phase 1の提案 |
|---|---|
| 対象範囲 | 中小企業白書・小規模企業白書の必要章、統計の定義・利用上の注意、収益力改善支援要領。直近2～3年版を中心に、制度の現行版を優先 |
| 導入規模 | まず100～300文書・章程度、3,000～10,000チャンク以内を目安。全府省・全業種の一括収集はしない |
| 取得形式 | HTML優先。PDFは見出し・ページを保持して抽出。表・脚注が壊れる文書は隔離し、OCRや人手確認は重要ページだけ |
| チャンク | 見出し・意味段落単位で400～700 tokens、overlap 50～100。見出しパスを先頭に付け、章境界を越えない |
| 表 | 原則Aへ。文書に残す場合も表題・列名・単位・対象期・脚注を一組にし、大きい表は行群分割時にヘッダーを繰り返す |
| 統計の定義 | 平均・中央値・加重平均、分母、標本、対象地域、企業規模を本文とは別の定義レコードでも保持 |
| フィルター | `license_status=approved`、地域、適用期間、文書版、業種、言語を先に絞る。旧制度は明示的な過去照会だけ |
| 検索方式 | ローカル語彙検索＋ベクトル検索。FTS5で日本語が期待どおり分割されると決めつけず、trigram等の利用可否と検索精度を検証 |
| 統合 | 各検索から候補10～20件、RRF等の決定的処理で統合し、重複排除後に4～6件を採用。数値は別途Aから確定取得 |
| コンテキスト予算 | 追加分はレポート全体で3,000～5,000 tokensを初期上限。11セクションに同一根拠を毎回フル投入しない |
| 適合しないとき | 類似度だけで採用しない。対象違いなら「該当根拠なし」。無関係な高類似文書で引用欄を埋めない |

メタデータは、`source_id / document_id / chunk_id / publisher / title / source_url / retrieved_at / published_at / reference_period / valid_from / valid_to / industry_code / geography / company_size_band / section_path / page_from / page_to / license_url / license_status / reuse_scope / third_party_rights / content_hash / corpus_version / embedding_model / embedding_dimensions`を基本とする。未公表項目はnullとし、`reuse_scope`では内部RAGとレポート抜粋等を分ける。

Aの値にはさらに、`metric_id / value / unit / currency / formula / aggregation / sample_size / cohort / period / source_table / cell_or_row / comparability / evidence_status`を持たせる。`evidence_status`は観測事実・加工値・仮定・未確認を分け、サンプル数が非公表ならnullとする。推定値を保存する場合も、出典あり数値と同じ列だけで管理しない。

### 埋め込みモデル比較

学習不使用とZDRは別要件として確認する。OpenAIはAPIデータの学習利用がデフォルトで無効でも、通常の不正利用監視ログは最大30日保持され、ZDRは承認・追加条件が必要である。([OpenAI Data controls](https://platform.openai.com/docs/guides/your-data))

| 候補 | 公式確認した料金・仕様 | 日本語性能の確認範囲 | 学習利用・保持方針 | 採用判断 |
|---|---|---|---|---|
| OpenAI `text-embedding-3-small` | 100万tokensあたり$0.02。([モデル料金](https://developers.openai.com/api/docs/models/text-embedding-3-small)) | 公式は多言語MIRACLで旧モデルより改善と報告。日本の中小企業文書に限定した性能は未確認。([公式発表](https://openai.com/index/new-embedding-models-and-api-updates/)) | APIは明示的共有がなければ学習不使用。`/v1/embeddings`はZDR適格、通常ログ30日・application stateなし。ZDR承認を別途確認。([データ管理](https://platform.openai.com/docs/guides/your-data)) | **第一候補**。既存契約でembeddingのZDRが有効なら追加提供者を増やさずに済む |
| OpenAI `text-embedding-3-large` | 100万tokensあたり$0.13、最大3072次元。`dimensions`で短縮可能。([公式発表](https://openai.com/index/new-embedding-models-and-api-updates/)) | 公式MIRACLではsmallより高い多言語平均。ただし日本語・本用途の優位は別評価。([公式発表](https://openai.com/index/new-embedding-models-and-api-updates/)) | 上と同じエンドポイントの管理条件。([データ管理](https://platform.openai.com/docs/guides/your-data)) | 品質比較の対照。全件再埋め込み前に小標本で判定 |
| Voyage `voyage-4-lite`／`voyage-4` | 各100万tokensあたり$0.02／$0.06。4系は標準1024次元、256／512／2048も選択可。([料金](https://docs.voyageai.com/docs/pricing)、[仕様](https://docs.voyageai.com/docs/embeddings)) | 4は多言語検索向けと記載。日本語固有スコア・日本語文書への適合性は未確認。([公式仕様](https://docs.voyageai.com/docs/embeddings)) | ホストAPIは学習・保存のopt-outでzero-day retentionとFAQに記載。支払方法登録と管理者による設定が必要。対象アカウント・endpoint・利用メタデータの例外範囲は契約時確認。([FAQ](https://docs.voyageai.com/docs/faq)) | 第二候補。無料枠に依存せず比較。設定証跡を残す |
| Cohere Embed 5 Pro／Fast | テキスト100万tokensあたり$0.12／$0.08。Proのコード例IDは`embed-v5.0-pro`。Fastの正確なIDは今回未確認。([公式発表](https://cohere.com/blog/embed-5)) | 100超言語、日本語評価の掲載あり。ただしベンダー内評価を実運用保証にしない。([公式発表](https://cohere.com/blog/embed-5)) | SaaSでは学習opt-outとZDRを別設定。通常プロンプト等ログ30日、ZDRは承認された企業顧客向け。([Enterprise Data Commitments](https://cohere.com/enterprise-data-commitments)) | 品質候補。低予算βでは契約・ZDRの手間から後回し |

非中国系候補として、上表の提供元を選定する。ただし「中国系以外」が本社・親会社だけを指すのか、基盤モデル・全再委託先・資本関係・処理地域まで含むのかは契約審査の定義として残す。VoyageはMongoDBによる買収を公式発表し、Cohereはトロント創業を公式に説明しているが、これだけで全再委託先や全処理地域まで要件適合と証明したことにはならない。([MongoDBによる買収説明](https://www.mongodb.com/company/blog/news/redefining-database-ai-why-mongodb-acquired-voyage-ai)、[Cohere About](https://cohere.com/about))

Phase 1では、既存の承認済みOpenAIアカウントがあるならsmallを起点にし、未承認なら「APIが学習不使用だからZDRも満たす」として導入しない。承認待ちの間はA＋ローカル語彙検索で先行するか、Voyageの設定・契約条件を確認する。中国系か判定できない別モデルへの自動フォールバックは無効化する。

日本語50～100問で、数値・定義・業種／年度違い・根拠なしを評価する。Recall@5、年度一致、引用支持率、未回答の妥当性を測り、改善不足ならモデルを変更し、文書・照会側の埋め込みをそろえる。

### 保存先比較

下表の価格は米ドル、税・為替・ネットワーク・アプリの追加計算資源は別である。**Fly.ioからの実測p50・p95遅延はすべて未確認**であり、「エッジ」「サーバーレス」という名称だけで東京のアプリから最速と判断しない。

| 保存先 | 公式料金・容量の確認 | 運用の手間・制約 | Fly.ioからの遅延・配置 | 判断 |
|---|---|---|---|---|
| SQLite＋sqlite-vec、アプリイメージに同梱 | sqlite-vecはMIT／Apache-2.0を掲示。別のベクトルサービス利用料なし。([公式リポジトリ](https://github.com/asg017/sqlite-vec)) | 読み取り専用の公開コーパスに限定。更新時は新DBをビルドして再配布。各Machineが同じ版のDBを持つ。RAM・イメージ容量・ビルド時間は実測 | 検索自体に外部ネットワーク往復なし。実際の検索時間は件数・次元・CPU次第 | **Phase 1推奨**。全件更新が小規模な間は最も単純 |
| SQLite＋sqlite-vec、Fly Volume上 | Volumeは$0.15/GB/月。snapshotは$0.08/GB/月、10GBまで無料枠の記載。([Fly料金](https://docs.fly.io/about/pricing)) | Volumeは1台のMachineに接続、共有・自動複製なし。単一構成は停止リスク、バックアップを別途管理。([Volume仕様](https://fly.io/docs/volumes/overview/)) | アプリと同一ホストの保存先。検索処理の実測は必要 | 配布より更新頻度が高い段階で検討。冗長化を無料と見積もらない |
| pgvector、自主管理／Fly Managed Postgres | pgvectorは使用・複製・改変・配布を許可するライセンス。Fly Managed PostgresはBasic月$38から、ストレージ$0.28/GB/月。([ライセンス](https://github.com/pgvector/pgvector/blob/master/LICENSE)、[Fly料金](https://docs.fly.io/about/pricing)) | 既存Postgresがあれば有力。新規なら接続、backup、拡張、upgrade等が増える。HNSW／IVFFlat等に対応。([pgvector](https://github.com/pgvector/pgvector)) | 同一リージョン配置を設計上推奨。実測未確認。Managed版の拡張提供バージョンも要確認 | 既存DBがなければPhase 1では過剰。自主管理価格は構成依存で未確認 |
| Upstash Vector | Free：1GB、20,000万vectors×dims、1日10,000 Query/Update、最大1536次元。従量：$0.40/10万requests＋$0.25/GB月相当。Fixedは$60/月。([公式料金](https://upstash.com/pricing/vector)) | 管理負担は小さいが、公開情報だけを格納し、照会は抽象化。自動埋め込みは使わず、承認済み提供者のベクトルを投入する提案 | 公開価格の地域表はN. Virginia、Ireland、Iowa等。東京リージョンの利用可否は未確認。近い地域を選ぶ案内あり。([料金明細](https://upstash.com/pricing/vector.md)、[開始ガイド](https://upstash.com/docs/vector/overall/getstarted)) | 管理の容易さを優先する第二案。無料枠継続条件とリージョンを契約時確認 |
| Cloudflare Vectorize | Free：月3,000万queried dimensions、500万stored dimensions。Paid：月5,000万／1,000万を含み、超過は$0.01/100万 queried、$0.05/1億 stored。([Vectorize料金](https://developers.cloudflare.com/vectorize/platform/pricing/)) | REST API経由でも利用量課金。Workers Paidは最低$5/月。外部アプリからの構成・必要プランを確認し、不要な中継Workerは作らない。([Vectorize料金](https://developers.cloudflare.com/vectorize/platform/pricing/)、[Workers料金](https://developers.cloudflare.com/workers/platform/pricing/)) | Fly東京からの保存先・処理リージョン・p95は未確認。CDNの近さとベクトル索引の配置を混同しない | 既にCloudflareを利用する場合の候補。今回はSQLiteより優先しない |

容量例として、5,000チャンク×1,024次元×float32のベクトル本体は約20.5MB、10,000件×1,536次元なら約61.4MBとなる。これは単純計算であり、原文、メタデータ、索引、SQLiteオーバーヘッド、プロセスRAMを含まない。現在のFly Machineで余裕があることを確認せずに「運用費ゼロ増」と断定しない。

Cloudflareの料金ページではqueried dimensionsを、例示上「索引内ベクトル数＋クエリ数」に次元数を掛ける形で説明しているため、単に「クエリ数×次元数」だけで試算しない。現行課金モデルの解釈は、実装前に公式計算例とダッシュボードで照合する。([Vectorizeの計算式・例](https://developers.cloudflare.com/vectorize/platform/pricing/))

遅延は実際のFly配置から同じfilter・top-kで測り、warm／cold、同時実行、埋め込み込みを分けてp50・p95・失敗率を記録する。国内保存や特定リージョンの保証は推測しない。

### 更新ジョブ・版管理

以下は提案頻度で、公表周期とは別である。別プロセス等からPythonジョブを日次起動し、ロック・リトライを付ける。推論中にPDF取得・全件再埋め込みはしない。

| 対象 | 推奨チェック頻度 | 処理 |
|---|---|---|
| 年次統計・白書 | 通常は月次、公表予定期だけ週次 | 新しい対象期・訂正情報を検知。前年比は同じ定義・版で再計算 |
| 法人企業統計 | 月次チェック、公表月は公表後確認 | 四半期・年次を区別。速報・確報・改定の系列を保存 |
| 制度・再生支援文書 | 週次 | 現行版の差分と施行日を確認。改正文だけで旧本文を置換しない |
| 補助金 | 実行時確認を基本、一般公開データの候補キャッシュは日次等 | 公募終了日時を構造化して除外。詳細は原典の最終確認時刻を表示 |
| 競合料金・機能 | レポート作成時、または短いTTL | 汎用クエリ結果のみキャッシュ。顧客固有入力を共有キャッシュのキーにしない |
| 許諾済みベンチマーク | 元の公表頻度に合わせる | 更新時に定義・母集団・権利条件も再確認。値だけ差し替えない |
| 利用規約・モデル価格 | 月次＋ベンダー通知時 | 規約のハッシュと確認日を保持。許諾変更なら該当資料を検索対象から外せるようにする |

更新は取得→ハッシュ差分→権利審査→抽出・数値検証→変更部分の埋め込み→評価・承認→版公開とする。同梱なら新イメージ配布、Volumeなら検証済みDBへ切り替え、失敗時は前版を維持して更新遅延を表示する。

コーパス・ベンチマーク・生成モデル・プロンプトの版と検索日時をレポートに記録する。削除・訂正を反映し、権利上必要なら保存旧版からも除去できるようにする。

### 出典表示・事実と推測の分離

以下はJinja2側の表示・検証仕様の提案である。LLMには出典IDを選ばせ、URLはサーバーが証拠台帳から付与する方式にし、モデルが新しいURLを作っても採用しない。

| 表示項目 | 仕様 |
|---|---|
| 数値 | 値、単位、対象期、地域、業種、企業規模、指標定義、元表・行列識別子、出典URL |
| 政策・制度 | 発行主体、資料名、章・ページ、適用期間、公表日、最終確認日時 |
| 加工値 | 「原典の○○と△△を用いて本サービスが算出」。計算式と元データを表示 |
| 推測・仮定 | 「評価仮説」「ユーザー入力に基づく試算」「例示シナリオ」を明示し、観測事実の出典を装わない |
| 不適合 | 「海外参考値」「上場企業参考」「対象業種不一致」「未確認」「データなし」を表示 |
| 引用の検査 | 出典IDの存在、当該段落の支持、対象期・単位、引用先の到達可否、旧版制度への誤リンクをチェック |
| 権利表示 | 一般の出典リンクに加え、APIごとの指定クレジット・非保証文をフッター等に常設 |

e-Stat APIにはクレジット表示義務があり、Jグランツも出典・加工表示を求め、法人番号APIは取得元と非保証の表示を要求しているため、一般的な「参考URL一覧」だけで代替しない。([e-Stat API規約§6・§7](https://www.e-stat.go.jp/api/terms-of-use?en/terms-of-use=&terms-of-use=)、[jGrants §5.1](https://fs2.jgrants-portal.go.jp/API%E5%88%A9%E7%94%A8%E8%A6%8F%E7%B4%84.pdf)、[法人番号API §6・§7](https://www.houjin-bangou.nta.go.jp/webapi/riyokiyaku.html))

文書内の命令は実行せず、証拠と指示を分離する。取得層で許可ドメイン・リダイレクトを検査し、内部IPへの接続拒否、サイズ制限、HTML無害化を実施する。

### Perplexityのドメイン指定とZDR

APIにはドメインの許可・拒否フィルターがあるが、確認した公式仕様にドメイン重み付け・優先順位のパラメーターはない。許可リストと拒否リストは混在できず、最大20件である。([Search APIのdomain filter](https://docs.perplexity.ai/docs/search/filters/domain-filter)、[Agent API web_search](https://docs.perplexity.ai/docs/agent-api/tools/web-search))

| 経路 | 指定方法 | 料金の確認範囲 | 保持条件の判断 |
|---|---|---|---|
| Search API `POST /search` | リクエスト直下の`search_domain_filter`、`country: "JP"`。複数クエリは配列 | 標準$0.005／成功リクエスト、fast $0.001。最大5クエリを含む1リクエスト単位。([料金](https://docs.perplexity.ai/docs/getting-started/pricing)、[Search quickstart](https://docs.perplexity.ai/docs/search/quickstart)) | Search Addendumが優先。ZDRを一般API説明から推定しない。([Search Addendum](https://www.perplexity.ai/hub/legal/perplexity-api-terms-of-service-search)) |
| Agent APIの`web_search` | `tools`内の`type: "web_search"`に`filters.search_domain_filter`。地域は同階層の`user_location.country` | 標準$0.0025／invocation、fast $0.001。別途モデルの入出力等が課金。([web_search仕様](https://docs.perplexity.ai/docs/agent-api/tools/web-search)、[料金](https://docs.perplexity.ai/docs/getting-started/pricing)) | アカウントZDR・対象モデル・検索ツールへの適用を個別確認。([Privacy & Security](https://docs.perplexity.ai/docs/resources/privacy-security)) |
| その他の既存経路 | 現行コードがSonar等の別APIなら、そのAPIのSDK・仕様を確認してから設定 | 上記料金を流用しない | 名前が同じでも同一の条件とは扱わない |

Agent APIの設定例は次のとおり。リクエスト全体ではなく、動作未検証の抜粋である。承認済みの非中国系・ZDR適格モデルを指定し、入力には審査済みの一般検索語だけを使う。([公式web_searchの設定例](https://docs.perplexity.ai/docs/agent-api/tools/web-search))

```json
{
  "tools": [
    {
      "type": "web_search",
      "max_tokens": 3000,
      "max_tokens_per_page": 800,
      "filters": {
        "search_domain_filter": [
          "chusho.meti.go.jp",
          "meti.go.jp",
          "e-stat.go.jp"
        ]
      },
      "user_location": {
        "country": "JP"
      }
    }
  ]
}
```

競合分析は政府サイトに限定せず、発見した競合の公式ドメインをレポートごとに選ぶ。Why Nowは政府・業界一次資料、競合は個社公式サイトという役割分担を推奨する。「優先」は専用パラメーターでなく、公式ドメイン限定の検索→根拠不足の場合のみ別の検索、というアプリ側の制御で実現し、既存2検索枠内に収まらない追加検索は上限管理する。

**重要な契約上の注意：Search Addendum §2.2は、InputとOutputであるSearch Dataを保持・複製・配布し、製品・サービス改善等に利用できると規定し、§1.2.2は他製品のZDR義務をSearch Servicesへ拡張しないとする。** 一方、API一般のPrivacy & Securityはデフォルトの生成AI学習不使用と、書面合意によるZDRを説明しているため、Search APIと生成モデルを一括して「学習も保持も一切ない」と説明しない。([Search Addendum §1.1.1・§1.2.2・§2.2](https://www.perplexity.ai/hub/legal/perplexity-api-terms-of-service-search)、[API Privacy & Security](https://docs.perplexity.ai/docs/resources/privacy-security))

推奨する対応は、①現行の実エンドポイント・モデル・契約を特定、②生成入力のZDRを確認、③検索には顧客の機密入力を送らず、公開分類語から作ったテンプレート照会だけを使う、の順である。全外部サービスに内容のゼロ保持を要求する厳格運用なら、検索ツール部分の書面保証が取れるまで実行時検索を停止する。内容を含まない請求・使用量メタデータの保持と、入力本文の保持も契約上区別する。

### 1回あたりの追加コスト

現状の約$0.06はユーザー提示の基準値であり、現行API経路、モデル単価、トークン数、月間件数、検索回数の実ログは未確認である。したがって以下は**仮定付き試算**であり、現在の総額を実測した結果ではない。

試算の前提は、1レポートに照会埋め込み計300 tokens、追加の生成入力4,000 tokens、RAG専用LLM呼び出しなし、出力長は維持、既存2検索枠は増やさない構成とする。OpenAI smallまたはVoyage 4-liteの100万tokensあたり$0.02を使う場合、照会埋め込みは1回$0.000006となる。([OpenAI料金](https://developers.openai.com/api/docs/models/text-embedding-3-small)、[Voyage料金](https://docs.voyageai.com/docs/pricing))

| 追加項目 | 計算・仮定 | 追加費用／レポート |
|---|---|---|
| 照会埋め込み | 300÷1,000,000×$0.02 | $0.000006 |
| SQLite検索 | 既存MachineのCPU・RAMで吸収できる仮定 | 外部従量課金なし。Machine増強が必要なら別 |
| 生成入力：単価$0.10／100万tokensの場合 | 4,000÷1,000,000×$0.10 | $0.0004 |
| 生成入力：単価$1.00／100万tokensの場合 | 同上×$1.00 | $0.004 |
| 生成入力：単価$3.00／100万tokensの場合 | 同上×$3.00 | $0.012 |
| Upstashを使う場合の検索 | 2 requests×$0.40÷100,000 | 約$0.000008、保存料別。([料金](https://upstash.com/pricing/vector)) |
| 実行時検索 | 呼び出し回数・経路を変えない | 増分$0を仮定。実際の単価は上の経路別料金で確認 |
| 追加検索を1回許可する場合 | Search API標準／Agent web_search標準 | $0.005／$0.0025に関連モデル料金等を加算。([Perplexity料金](https://docs.perplexity.ai/docs/getting-started/pricing)) |

追加の生成入力単価が$1／100万tokensで、他の前提を満たすなら、変動費は約$0.004増、合計約$0.064となる。単価が$3なら約$0.072であり、「RAGは埋め込み代が安いからほぼ無料」とは言えない。出力増加、セクションごとの根拠再投入、長い検索コンテキスト、再試行・フォールバックが費用を押し上げるため、実際のモデル利用量で差分を測定する。

初回コーパス150万tokensなら、$0.02／100万tokensの埋め込み費は約$0.03、$0.13なら約$0.195である。これは埋め込みだけの単純試算で、文書抽出・OCR・人手の品質確認・法務確認・ビルド費用を含まない。([OpenAI料金](https://developers.openai.com/api/docs/models/text-embedding-3-small)、[large料金](https://openai.com/index/new-embedding-models-and-api-updates/))

固定費は件数で割って判断する。仮に新規pgvector基盤としてFly Managed Postgres最低$38/月を追加すると、月1,000レポートなら保存料等を除いても$0.038/回となり、低単価βには重い。Volume 1GBの$0.15/月なら同条件で$0.00015/回だが、冗長化やアプリ増強は別である。([Fly料金](https://docs.fly.io/about/pricing))

Phase 1の目標は、追加変動費$0.005／回程度以内を暫定上限として計測し、上回る場合は文脈量・重複投入・追加検索から削ることを提案する。これは予算目標であり保証ではない。文脈を切った結果、根拠が足りなければ無根拠に回答するのでなく「未確認」とする。

### Phase 1で作るもの／後回しにするもの

| 区分 | 作るもの・採用条件 |
|---|---|
| Phase 1：契約ゲート | 現行LLMと埋め込みendpointのZDR、検索経路別の保持条件、非中国系の審査範囲を確認。未充足なら該当経路を無効化 |
| Phase 1：ライセンス台帳 | sourceごとに規約URL・確認日・対象範囲・内部保存・抜粋・再配布・第三者除外を記録。権利不明はデフォルト除外 |
| Phase 1：A | 中小企業実態基本調査と法人企業統計を中心に、利用頻度の高い業種・規模の財務指標。指標数より定義整合を優先 |
| Phase 1：B | 白書の必要章、統計定義、再生支援の現行要領。公開資料だけをSQLite＋sqlite-vecへ |
| Phase 1：C | 現行2検索枠を維持。Why Nowの政府・一次資料限定と、競合の公式ドメイン検索。機密入力を送らない |
| Phase 1：数値・引用 | Python計算、出典ID照合、対象期・単位検査、事実／仮定／未確認の表示、APIクレジット |
| Phase 1：評価 | 日本語50～100問、根拠なしケース、業種・規模・年度の不一致、検索費用・p95の計測 |
| Phase 1：欠けるものを許容 | マルチプル、WACC、CAC等は許諾・根拠不足なら未取得。ユーザー入力の仮定で感度分析し、観測値に見せない |
| 後回し | EDINET全件XBRL正規化、財務KPIの自動再計算、多地域・多通貨、全件法人DB、毎日の大量クロール |
| 後回し | 有料reranker、GraphRAG、agenticな多段検索、11セクション別の独立検索、PDF図表の全面OCR |
| 後回し | マネージドベクトルDB移行、複数拠点レプリカ。可用性要件・同時実行・更新頻度が増えた段階で再評価 |
| 許諾取得後 | Damodaran、公庫、TKC、SaaS Capital、JPX Proのうち、実利用頻度と取得・契約費に見合うものだけ追加 |

## 未確認事項の一覧

「未確認」は不存在・禁止と同義ではない。次を実装前チェックリストとする。

| 未確認事項 | 現状・確認先 | 対応・未解決時の扱い |
|---|---|---|
| 現行Perplexityの経路 | `web_search`という名称のみ。Search／Agent／Sonar等、実リクエストとモデルIDは未確認 | コード・SDK版・請求ログで特定。別APIの料金・パラメーターを転用しない |
| 検索ツールのZDR適用 | 一般APIのZDRだけではSearch Servicesの例外を解消できない。([Search Addendum](https://www.perplexity.ai/hub/legal/perplexity-api-terms-of-service-search)) | 適用契約を照会。保証がなければ公開テンプレート語だけに限定、厳格要件ならC停止 |
| LLM・embeddingの実アカウント承認 | ユーザー環境の承認・設定は未確認 | `/v1/embeddings`等を含む対象endpointと組織設定を証跡化。未承認時は外部照会埋め込みをしない |
| 非中国系の定義 | 本社、親会社、モデル系譜、資本・再委託先・保存地域のどこまで含むか未確定 | 利用ポリシーに定義。未審査モデルへのフォールバックを禁止 |
| Damodaran商用利用 | 商用SaaS内キャッシュ、派生値、顧客レポート、再配布の許諾・料金は未確認 | 目的を明記して許諾照会。回答前はA不採用 |
| TKC・公庫の許諾 | 公開資料でも制限あり。個別許諾の範囲・費用未確認。([TKC条件](https://www.tkc.jp/link)、[公庫条件](https://www.jfc.go.jp/n/guideline/)) | 指標抽出・継続更新・顧客表示まで照会。未許諾でBへ移さない |
| 日銀の商用条件 | 商用転載・複製は事前相談。個別許可・派生値条件・料金未確認。([サイト留意点](https://www.stat-search.boj.or.jp/ssi/docs/info/notice.html)) | 用途を相談し、API利用時の連絡・クレジットも対応 |
| JPX法人契約 | Proの必要データセット・第三者提供・派生指標・料金・認証を未確認 | 見積もり・許諾取得まで不採用。個人版を代替にしない |
| SaaS公開レポートの許諾 | SaaS Capital・One Capitalの商用蓄積・再配布条件は未確認 | 公開記事は原典案内を基本とし、A導入は許諾後 |
| 日本SaaS・個社IR | 代表的な一括KPI DBと、個社ごとの開示・定義・再利用許諾は未確認 | 5～10社の個別資料を確認。日本個社と海外参考、EDINETと個社IRを区別 |
| 政府APIの費用・収録 | 一部APIの無料明文、対象表のAPI対応、gBizINFOの欠損率・更新網羅性は未確認 | 表ID・項目を固定して試験。「公開」を料金・網羅性の保証としない |
| jGrants認証・保存 | 認証不要の明文、商用・保存期間・包括再配布条件は未確定 | 公式概要・窓口で確認。添付資料は別審査 |
| DBの地域・遅延・ログ | Fly配置、Upstash東京対応、Vectorize処理場所、実測遅延、各DBのログ・学習条件は全面確認していない | 実配置から測定し契約確認。公開コーパス・一般化クエリだけに限定し、厳格要件ならローカル保存 |
| 追加コストの実額 | 現行モデル単価、レポート件数、RAM余裕、トークン増、出力増は未確認 | 小規模βで前後比較。月額固定費を含む1回原価を算出 |
| 運用ログ・レポート保存 | Flyアプリ、エラー監視、APM、ジョブ、バックアップ等の保存設定は未確認 | 入力本文をログしない。顧客レポートの保存・削除方針をLLMのZDRと別に定める |

Phase 1では、出典・母集団付き数値、再利用できる文書、外部送信できる検索語を分ける。政府統計・白書中心の小さな基盤から始め、許諾不明の指標を無理に埋めず、追加検索・文脈量を制御する。
