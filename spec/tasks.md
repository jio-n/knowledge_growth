# Tasks — Reading Workspace

Kiro spec-driven 規約に沿った改良タスクの起点リスト。**このファイルは改良を進めるたびに更新** してください(完了項目にチェック、着手時に owner 記載、完了時に PR / commit 参照を残す)。

- タスク ID: `T-<領域>-<番号>`(削除しない、廃止時は `~~取り消し線~~` + 理由)
- 優先度: `P0`(統合ブロッカー) / `P1`(強い体験改善) / `P2`(あればよい) / `P3`(将来)
- 影響範囲: どの spec / コードエリアを触るか
- 完了条件: 「これができていれば完了」の判定

改良に着手する前に必ず `design_philosophy.md` の「不変条件」と「6原則」を読んでください。

---

## 現状(2026-07-11 時点)

- MVP は動作しており、E2E スモークテスト(pytest 4件)グリーン
- ブラウザで手動 E2E 検証済み(PDF/URL/テキスト登録、質問、保存、ノート、翻訳、アンカージャンプ、リロード復元、エクスポート)
- 詳細は本リポジトリ内 `docs/handoff/current_status.md` にあります(この spec/ 外の詳細補足)

このタスクリストは、この状態から他システムへ組み込む・改良する第三者/Kiro が着手するための起点です。

---

## セクション 1: 組み込み時に最初にやること(P0)

### T-INT-01: 動作確認と受け入れ基準の再現
- **完了条件**: 移植先環境で `python run.py` → `pytest tests/` 4件パス、README のクイックスタートが動く
- **参照**: `../README.md`, `../CLAUDE.md`, `spec/requirements.md` REQ-NFR-01, REQ-NFR-02
- [ ] Python 3.11+ で venv 作成
- [ ] `pip install -r requirements.txt`
- [ ] `python scripts/fetch_vendor.py` で pdf.js / marked を取得
- [ ] `python -m pytest tests/ -q` が 4 passed
- [ ] `python run.py` で `http://localhost:8300` が開く

### T-INT-02: LLM プロバイダーの選択と検証
- **完了条件**: 移植先で使う LLM プロバイダーで初期抽出・質問・翻訳が動作
- **参照**: `spec/design.md` §6, `spec/api_reference.md` (Meta), `../config/app.config.json`
- [ ] 使用するプロバイダーを決定(mock / anthropic / openai_compat / 新規追加)
- [ ] `config/app.config.json` の `llm.provider` を設定
- [ ] 必要な環境変数(`ANTHROPIC_API_KEY` 等)を設定
- [ ] 実 LLM で初期抽出 → 質問 → 保存の 1 経路を通す
- [ ] `answers.context_summary` に期待した内容が入っていることを確認(監査目的)

### T-INT-03: データディレクトリと持ち出し方針の決定
- **完了条件**: `data/` の保存先が定義され、バックアップ運用が決まっている
- **参照**: `spec/requirements.md` REQ-PERSIST-01, `spec/design_philosophy.md` §2.5
- [ ] `KG_DATA_DIR` 環境変数の値を決める(既定 = リポジトリ内 `data/`)
- [ ] バックアップ運用(定期 cp / GET /api/export/all.json)を決める
- [ ] 原本ファイルの削除ポリシー(DELETE /api/sources/{id} 時に `data/files/` を消すか)を決める。**現状は消さない**

### T-INT-04: セキュリティ境界の追加検討
- **完了条件**: 移植先での公開範囲に応じた対策方針が決まっている
- **参照**: `spec/design.md` §13
- [ ] localhost のみで運用するか、LAN / 公開するかを決める
- [ ] LAN/公開する場合、認証層追加のチケットを起票(このリストに `T-SEC-*` として)
- [ ] URL 登録機能を有効にする場合、SSRF リスクの評価(内部 IP へのアクセスを許すか)

---

## セクション 2: 統合・組み込みタイプ別のタスク

移植先での組み込み方式によって着手すべきタスクが異なります。

### 2A. HTTP API 経由で組み込む場合(既存の FastAPI をそのまま使う)

#### T-HTTP-01: 認証ミドルウェア追加
- **前提**: T-INT-04 で LAN/公開が決まった場合
- **完了条件**: 全 `/api/*` エンドポイントが認証を要求する
- **参照**: `spec/design.md` §13
- [ ] FastAPI middleware で API キー / セッション / OAuth 等を検証
- [ ] `/api/meta` は認証必須にするか任意にするか決める(既定: 必須)
- [ ] `spec/api_reference.md` にヘッダー要件を追記

#### T-HTTP-02: CORS 設定
- **前提**: 別ドメインのフロントエンドから叩く場合
- [ ] `fastapi.middleware.cors.CORSMiddleware` で許可 origin を明示

#### T-HTTP-03: レート制限
- **前提**: 公開環境 or マルチテナント化
- [ ] LLM 呼び出しを含むエンドポイントに rate limit を追加(slowapi 等)

### 2B. コード一式を切り出して別リポジトリへ移植する場合

#### T-PORT-01: リポジトリ切り出し
- **完了条件**: `app/` / `client/` / `prompts/` / `config/` / `scripts/` / `tests/` / `spec/` / `requirements.txt` / `run.py` / `CLAUDE.md` / `README.md` を新リポジトリへコピー
- [ ] コピー対象: 上記ディレクトリ・ファイル
- [ ] コピーしないもの: `data/`, `.venv/`, `docs/` (研究開発文脈を切りたい場合は省略可)
- [ ] 新リポジトリの LICENSE を確認・追加
- [ ] `spec/README.md` の目次リンクが新リポジトリで正しく解決することを確認

#### T-PORT-02: 命名の変更(必要なら)
- **前提**: 移植先のプロダクトブランドが異なる場合
- [ ] タイトル文字列(HTML `<title>`, README ヘッダー等)の変更
- [ ] パッケージ名(`app` → 別名にする場合)は import 影響が広いため慎重に

#### T-PORT-03: 独自の設定・secrets 管理への統合
- [ ] `config/app.config.json` の場所を移植先の慣習に合わせる
- [ ] API キーの持たせ方(env / secret manager / vault)を統一

### 2C. 別プロセスから subprocess として呼ぶ場合(小規模統合)

#### T-EMB-01: 起動スクリプトの提供
- [ ] 移植先アプリのビルド / 起動時に `python run.py` を並列プロセスとして起動する仕組み

---

## セクション 3: 拡張(P1 — 体験を有意に上げる改良)

### T-EXT-01: ストリーミング応答(SSE)
- **完了条件**: 質問応答が Server-Sent Events で逐次配信される
- **参照**: `spec/design.md` §15(トレードオフ)、`spec/requirements.md` REQ-QA-01/02
- [ ] `POST /api/sources/{id}/questions/stream` を追加(既存 `POST /questions` は残す)
- [ ] LLMProvider に `complete_stream` を追加(mock は 5 チャンクに分けて返す)
- [ ] クライアント側で受信・逐次描画
- [ ] `answers.context_summary` の保存タイミング(完了時)を確認
- [ ] `spec/api_reference.md` に追記

### T-EXT-02: DOI / arXiv API メタデータ補完
- **完了条件**: DOI / arXiv ID が検出された資料の書誌が API から取得・保存される
- **参照**: `spec/design_philosophy.md` §5 原則1(拡張ポイント優先)
- [ ] Crossref API (`https://api.crossref.org/works/{doi}`) 呼び出しヘルパを追加
- [ ] arXiv API (`http://export.arxiv.org/api/query?id_list=...`) 同上
- [ ] 資料登録後の後処理として非同期実行(現行 `analysis.run_analysis_async` と同じパターン)
- [ ] 取得失敗時は登録を成功のまま残す(要件: 登録は絶対に成立)
- [ ] `spec/design.md` の「拡張ポイント」表に「メタデータプロバイダー」を追記

### T-EXT-03: 保存後の後処理フック(Obsidian 同期など)
- **完了条件**: `POST /api/knowledge` 成功時に登録済みフックが呼ばれる
- **参照**: `spec/design.md` §11 拡張点 #10
- [ ] `app/hooks.py` にフックレジストリを実装(関数リスト + 例外は握りつぶす)
- [ ] `config/app.config.json` にフック有効化フラグ
- [ ] 例: Obsidian フックが `data/vault/<source_title>.md` に export.md を書き出す
- [ ] フック内エラーはログのみ(本フローは絶対にブロックしない)

### T-EXT-04: タスク別モデル設定
- **完了条件**: 翻訳・批判的検討・要約が別モデルで実行可能
- **参照**: `spec/design.md` §11 拡張点 #3
- [ ] `config/app.config.json` の `llm` を task→provider/model のマップに拡張
- [ ] `get_provider(task="translate")` のようなオプション引数を追加
- [ ] 既定は現行の単一プロバイダー動作(後方互換)

### T-EXT-05: 意味検索(embeddings)
- **完了条件**: `GET /api/search?q=` の裏側が意味検索に切り替わる
- **前提**: API 契約は変えない、mock でも動作する
- **参照**: `spec/design.md` §11 拡張点 #11
- [ ] ローカル embedding モデル or API 埋め込み計算の選択
- [ ] 知識項目・ブロックへの embedding 生成タイミング(非同期)
- [ ] SQLite での近傍検索(sqlite-vec 拡張 等)
- [ ] mock モードでは LIKE 検索にフォールバック

### T-EXT-06: PDF レイアウト強化(2 段組・スキャン)
- **完了条件**: 2 段組 PDF の見出し・段落抽出精度が改善
- **参照**: `spec/design.md` §15
- [ ] PyMuPDF の block 座標を使ってレイアウト解析
- [ ] スキャン PDF は OCR オプション(pytesseract 等)。既定オフ
- [ ] 既存の PDF 抽出テストは引き続き通ること

---

## セクション 4: 品質・保守(P2)

### T-QA-01: ingest 単体テスト追加
- **完了条件**: PDF / Web / textfile の各 ingest 実装に fixture ベースの単体テストが存在
- [ ] `tests/fixtures/` にサンプル PDF, HTML, MD を配置
- [ ] `tests/test_ingest.py` を新設
- [ ] block 数、見出し検出、DOI/arXiv 抽出の期待値を assert

### T-QA-02: API スキーマ検証(OpenAPI エクスポート)
- **完了条件**: FastAPI の自動生成 OpenAPI 定義を CI で `spec/api_reference.md` と齟齬チェック
- [ ] エンドポイント一覧・パラメータ・型の対照ツールを軽く実装
- [ ] 齟齬時は CI で警告

### T-QA-03: フロントエンドの smoke test
- **完了条件**: Playwright 相当の E2E 自動化
- **注**: Node 依存を持ち込まない前提の維持と衝突する可能性あり。Python の Playwright (`playwright-python`) を検討

### T-QA-04: マイグレーション機構
- **完了条件**: DB スキーマの後方互換のある変更手順が定式化されている
- **参照**: `spec/design.md` §15
- [ ] 軽量マイグレーションレイヤー(バージョン列 + 手続き)を追加
- [ ] 現行 schema を version 1 として登録

---

## セクション 5: 将来(P3)

### T-FUT-01: 複数資料比較ビュー
### T-FUT-02: 資料横断の知識グラフ
### T-FUT-03: Zotero 連携
### T-FUT-04: Web ページ再取得と差分検出
### T-FUT-05: 再読リマインダー
### T-FUT-06: PDF bounding box アンカー
### T-FUT-07: 図表画像の抽出・保持
### T-FUT-08: 理解度の自己評価と履歴

これらは思想的に本ツールと整合するが、実装コストが大きい。P0/P1 の充実後に個別評価する。

---

## タスク着手時のワークフロー

1. 該当タスクを選び、`- [ ]` を `- [x]` に、`owner: <name>` を追記
2. 影響範囲を **契約** と **実装** に分けて把握
3. 実装 → `spec/requirements.md` / `spec/design.md` / `spec/api_reference.md` の関連セクション更新 → テスト
4. `git commit` 時、コミットメッセージに `T-XXX-YY` を含める
5. タスク完了時、本ファイルの該当 `- [x]` の後ろに `(PR #123 / commit abc123)` を追記
6. 新しいタスクが派生したらこのリストの適切なセクションに追加

## タスクを追加するときの原則

- 既存タスクの拡張で表現できないか検討(ID 増殖を避ける)
- 完了条件を **観測可能な形** で書く(「〜が良くなる」ではなく「〜のテストが通る」「〜のエンドポイントが応答を返す」)
- 影響する requirement / design 節を必ず参照
- 破壊的変更を伴うなら `spec/decisions/ADR-*.md` の作成もタスクに含める
