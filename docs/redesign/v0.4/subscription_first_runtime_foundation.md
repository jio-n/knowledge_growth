# Phase 0A: Subscription-first AI Runtime Foundation

実装対象はT0A-01〜T0A-05のみ。基点はPR #4のmerge commit
`28e4645d869c5b376686f891444ed87e69b0823a`。
日本語版Reader、ConversationThread、Visual Clip、実AIによるStructured Brief生成、
全文翻訳、Note/Q&A候補のImport反映は追加していない。DB migrationも不要。

## 対応表

| Task | 実装 |
|---|---|
| T0A-01 | `app/ai/base.py`のAIRuntimeとvendor非依存DTO。Codex / mock / no_ai factory。既存LLMProviderとの互換層 |
| T0A-02 | Codex child process、NDJSON RPC、initialize、thread create/resume、turn、delta、中断、timeout、異常終了、再起動、catalog |
| T0A-03 | ChatGPT browser login、完了通知＋account/read、cancel/failure、refresh、logout、keyring認証の再利用 |
| T0A-04 | defaultをcodex_chatgpt_planに変更。MVP factory/UI/起動/テストから従量課金APIへの依存を除去 |
| T0A-05 | AI unavailable/errorでも原PDF、Import済みBrief、kgpack、Note、Highlight、exportを利用可能 |

## AIRuntime

同期型interfaceを維持し、FastAPIのsync routesと既存LLMProviderから利用する。
`send_turn`はTurnEvent iterator。Codex固有methodやresponseはadapter内部だけに置く。

- `start / close / restart`
- `status(refresh=False)` → RuntimeStatus（runtime / state / auth / safe error code）
- `capabilities()` → 実装能力。利用権限や利用可否の保証には使わない
- `models()` → ModelInfo（id / label / entitlement）。Codex catalogは常にunverified
- `create_session(instructions, model=None) / resume_session(session_id)` → Session
- `send_turn(session, text, hint=None)` → started / delta / completed / cancelled
- `cancel(session_id, turn_id)`
- `login / cancel_login / logout`

将来API providerを実装する場所は同じAIRuntime境界。旧providerクラスは保持するが、
MVP factoryでは選択しない。未知のruntime typeもno_aiとなり、API billingへ移行しない。
`KG_AI_RUNTIME=mock`と従来の`KG_LLM_PROVIDER=mock`はoffline開発・テスト用。

`RuntimeProvider.complete(system, user)`がtrusted instructionsとuser入力を分離する。
既存Question、選択箇所translate、legacy初期抽出、Note保存先提案はこの互換層を利用できる。
将来のStructured Paper Brief生成も同じ層へ接続する。今回その生成器は完成させていない。
既存mock出力と初期抽出のheuristic fallbackを維持した。

## 現行protocolの確認

2026-10-04、この環境の `/opt/codex/bin/codex`、`codex-cli 0.159.0-alpha.3`を確認。
`app-server --help`、binaryから生成したJSON schema/TypeScript、隔離CODEX_HOMEでの
実binary handshakeを根拠に実装した。ブログ記事由来のRPC名は使っていない。

```sh
codex --version
codex app-server --help
codex app-server generate-json-schema --out /tmp/kg-codex-schema
codex app-server generate-ts --out /tmp/kg-codex-ts
```

| 項目 | 確認したwire contract |
|---|---|
| 起動 | `codex app-server --listen stdio://`（stdioは既定値） |
| framing | UTF-8 newline区切りJSON。requestはid/method/params、responseはid/resultまたはid/error。Content-Length framingは使わない |
| handshake | `initialize`のclientInfoとcapabilities:null → `initialized` notification |
| account | `account/read {refreshToken:true}`。account.typeがchatgptのときだけready |
| login | `account/login/start {type:"chatgpt"}` → loginId / authUrl。`account/login/completed`、`account/updated`を受信後にaccount/read |
| cancel/logout | `account/login/cancel {loginId}` / `account/logout`（paramsなし） |
| tools | `config/read {includeLayers:false,cwd}`で継承MCP名だけを取得し、各serverのenabledをthread configでfalseにする |
| session | `thread/start` / `thread/resume {threadId,...}`。responseのthread.idとmodelのみを正規化 |
| inference | `turn/start`のinputは`{type:"text",text,text_elements:[]}` |
| streaming | `item/agentMessage/delta`のthreadId/turnId/itemId/delta。`turn/completed`のturn.statusで終了判定 |
| 中断 | `turn/interrupt {threadId,turnId}` → interrupted終端通知 |
| models | `model/list {cursor,limit,includeHidden:false}`。dataとnextCursorでpagination |
| persistence | 独自token保存を作らず、Codexのcredential storageを使用。`cli_auth_credentials_store="keyring"`を強制 |

生成ClientRequestの必要11 methodと参照定義をfixtureに保存し、adapterが送信した
requestをJSON schemaで検証する。出典・元schemaのSHA-256は
[fixture README](../../../tests/fixtures/app_server/README.md)に記録。
本物のbinaryでinitialize / config/read / account/read → auth_required、および子process終了を確認済み。
本物のaccountでlogin/inferenceはこのCloud作業では実行していない。

## Process lifecycleとsecurity

FastAPI lifespanがアプリごとにruntimeを所有する。PATHまたは`KG_CODEX_EXECUTABLE`で
native executableを発見し、引数配列＋shell=Falseで起動する。Windowsのnpm shell shimは
実行せず、package内のnative codex.exeを探す。見つからなければunavailableで起動を続ける。

stdout専用readerがRPC responseをFutureへ、stream通知をthread別のbounded queueへ振り分ける。
同一sessionの並行turnはrejectする。request timeout、2MiB超のframe、JSON/envelope破損、
EOF/crashを固定error codeへ正規化し、pending requestとstreamを解除する。
framing破損・RPC timeout時は子processを停止する。restartは古いprocessを回収して再initializeする。

writerも専用thread＋bounded queueとし、stdinへの書き込みが詰まった場合もtimeoutで停止する。

終了時はstdin EOFでgraceful shutdownし、応答しなければterminate/killする。
POSIXでは独立process group、WindowsではKILL_ON_JOB_CLOSEのJob objectで子孫も回収する。
子processが正常終了していても残存する子孫を停止する。reader/pipeと一時cwdも閉じる。
WindowsのJob確立失敗時はprocess起動を取り消し、no-AI利用を継続する。

credential handling:

- CodexのOS keyringのみ使用。独自token storage、SQLite/configへのtoken保存、raw auth payload永続化は作らない
- OS keyringが使えない環境は認証失敗を表示する。平文file保存へfallbackしない
- 既存Codexがfile保存していた認証情報は自動移行しない。keyringでChatGPT接続し直す
- account APIではemail/access token等を返さず、account stateと既知plan labelだけを返す
- loginのauthorization URLだけはそのPOST responseと表示中のDOMで一時保持し、完了/cancel/logoutで表示を消す
- stdout/raw RPCをlogに出さず、stderrもreaderで消費して全内容を破棄する。Authorization/tokenを含む任意のvendor diagnosticsをログへ流さない
- child環境をallowlist化し、OpenAI/Anthropic等のAPI keyと無関係なsecret環境変数を継承しない
- cwdは空の専用temporary directory。read-only、approvalPolicy=never、turnのnetworkAccess=false、shell環境inherit=none
- shell/snapshot/apps/plugins/remote plugin/multi-agent/browser/computer use/web searchと継承MCPを無効化。serverからの実行・承認・token refresh要求はreject
- untrusted paperはuser inputへ渡す。`prompts/ai_runtime.md`がruntimeのtrusted guardrailを定義する
- AI controlの変更POSTはcross-site Origin/Sec-Fetch-Siteをrejectする

## Runtime status UI/API

Library/Reader共通のheaderにAI状態メニューを追加。
Codex未インストール、ChatGPT未接続、起動中、接続済み、errorを表示する。
ChatGPTで接続 → browser認証リンク → 完了検出、cancel/failure、logout、再接続、
状態再確認、最小接続テスト、stream中断が操作できる。API key入力やprovider選択はない。

| Endpoint | 内容 |
|---|---|
| GET `/api/ai/status?refresh=true` | 状態・auth・capabilities。refreshはCodexへ委譲 |
| POST `/api/ai/login` | browser authorization URL。credentialは返さない |
| POST `/api/ai/login/cancel`, `/api/ai/logout`, `/api/ai/restart` | 認証・process操作後の状態 |
| GET `/api/ai/models` | unverified catalog。UIでmodelを決め打ち・選択しない |
| POST `/api/ai/smoke` | AIRuntime経由で固定1 turn。正規化TurnEventをSSE配信 |
| POST `/api/ai/sessions/{session_id}/turns/{turn_id}/cancel` | stream中断 |

readyはChatGPT account確認済みという意味。model利用権・残quota・network到達性を保証しない。
利用不可は実turn結果で判定する。保存済みBrief/翻訳/Note等の読取はruntime statusから独立する。

## 検証とmanual smoke

```sh
.venv/bin/python -m pytest tests/ -q
.venv/bin/python scripts/smoke_ai_runtime.py
.venv/bin/python scripts/smoke_ai_runtime_browser.py
.venv/bin/python scripts/smoke_import_brief_browser.py
.venv/bin/python scripts/smoke_phase1_browser.py
```

pytestはfake subprocessと生成schema/PDF/kgpackを使用し、実Codex/account/keyring/ネットワークは不要。
browser smokeのPlaywright/Chromiumは開発用の任意依存で、frontend/buildには不要。
Phase 1 scriptは別途起動したdisposable mock serverのURLを`--base-url`で指定する。

最終検証（2026-10-04）:

- baseline: 150 pytest passed
- 実装後: 196 pytest passed（既存150＋runtime関連46）。既存Starlette/httpx deprecation warningが1件
- Import/Paper Brief browser: 34項目passed、pageerrorsなし
- AI Runtime browser: 11項目passed、pageerrorsなし
- Phase 1 PDF browser: 2段組/回転crop、selection、Anchor、Highlight再読込、mock Q&A、quote/bbox/candidates/page-only passed、pageerrorsなし
- offline最小turn: runtime-ok PASS
- 実Codex未認証probe: initialize/config/read/account/read → auth_required、close後child終了
- 実ChatGPT accountの認証・推論: 未実行。下記optional/manual smokeで確認する

実ChatGPTのmanual smoke（CI対象外）:

1. ローカルでCodex native CLIとOS keyringを準備し、`python run.py`を起動する
2. headerのAI状態 → ChatGPTで接続 → browser認証リンクからログインする
3. account/readで「接続済み」になることを確認し、「最小接続テスト」でruntime-okを確認する
4. 「再接続」またはアプリ再起動後に認証が再利用されることを確認する
5. logout後に「ChatGPT未接続」になること、PDF/Brief/Note/Importが利用できることを確認する

既存keyring認証でCLIからも確認可能:

```sh
.venv/bin/python scripts/smoke_ai_runtime.py --manual
```

これは実ChatGPTの1 turnを消費する。未認証時は接続方法を表示して終了し、token入力や
自動loginはしない。出力は状態とruntime-okのpass/failのみ。

## Known limitationsと次の接続点

- Codex app-serverはexperimental。確認version以外の互換性は保証しない。仕様変更はadapter/fixtureを更新する
- Windowsのnative discoveryはfixtureで検証、Job objectの実行とOS keyring OAuthは実Windows未検証。Linux Cloudでは実ChatGPT loginを実施していない
- Codexのread-only設定はOS全体を隔離する仕組みではない。tool無効化と空cwdを併用する。組織側の強制設定と競合した場合はerror/no-AIで運用する
- keyring保存の既存認証を再利用する。平文auth.jsonの移行やremote OAuth revokeは独自実装しない。logoutはCodexへ委譲する
- catalogはAPIで提供。model chooserは未実装。利用上限/network/権限によるturn失敗は安全なturn_failedへ集約する
- 旧LLMProviderのmax_tokensはCodexの同等parameterがないため転送しない
- Session/turn IDsはruntime内部またはsmoke中に使う。ResearchSource/DBへCodex固有schemaやConversationThreadを追加しない
- Codexは自身のthread履歴をcredentialとは別に保持し得る。研究テキストの機密性はローカルCodexの運用方針に従う
- 既存Question/選択translateはcomplete()互換層を使う。QA画面へのstreamingや失敗質問の先行保存はPhase 4で扱う
- 次はT2-02/T2-03/T2-06のStructured Paper Brief抽出をこのRuntimeProviderへ接続する。field validator、provenance、user-edited保護を維持する

## 変更ファイル

- `README.md`
- `app/ai/__init__.py`
- `app/ai/base.py`
- `app/ai/codex.py`
- `app/ai/compat.py`
- `app/ai/offline.py`
- `app/ai/process.py`
- `app/ai/windows_job.py`
- `app/analysis.py`
- `app/config.py`
- `app/llm/__init__.py`
- `app/llm/mock.py`
- `app/main.py`
- `app/routes_ai.py`
- `app/routes_knowledge.py`
- `app/routes_qa.py`
- `app/routes_sources.py`
- `client/css/app.css`
- `client/js/components/aistatus.js`
- `client/js/main.js`
- `config/app.config.json`
- `docs/development/setup.md`
- `docs/development/testing.md`
- `docs/redesign/v0.4/README.md`
- `docs/redesign/v0.4/knowledge_growth_implementation_tasks_v0_4.md`
- `docs/redesign/v0.4/subscription_first_runtime_foundation.md`
- `prompts/ai_runtime.md`
- `requirements.txt`
- `scripts/smoke_ai_runtime.py`
- `scripts/smoke_ai_runtime_browser.py`
- `tests/fixtures/app_server/README.md`
- `tests/fixtures/app_server/client_requests.schema.json`
- `tests/fixtures/app_server/fake_server.py`
- `tests/test_ai_runtime.py`

T2-02/T2-03/T2-06の接続は[Structured Brief生成](structured_brief_generation.md)で実装済み。上記はPhase 0A時点の記録。
