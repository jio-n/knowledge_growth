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

## 現行protocolの確認（2026-10-12 hotfix）

PR #6 merge `3911b57844ed5264f7c557f5eb6b7dc9f2f32975`を基点に、
T0A-02/T0A-03/T0A-05のWindows互換hotfixのみを実施。
Structured Brief、Reader等の新機能・DB変更は含めない。

現在の[GitHub app-server README](https://github.com/openai/codex/blob/6c372fbef45f730b6d8aa727b0ec8332365588db/codex-rs/app-server/README.md)
と実装・生成bindingsを確認した。取得したREADMEは追加契約が中心だったため、
[公式app-serverドキュメント](https://developers.openai.com/codex/app-server#authentication-endpoints)
のauth state / browser flowも併読した。
環境にpreinstallされていたalpha版のschema/TSを比較用に生成した後、
`@openai/codex@0.160.1`を作業用temporary directoryへ導入し、
同じコマンドでJSON schema/TSを再生成した。旧alpha版を正本にはしていない。

`GetAccountParams.refreshToken=true`はtokenのproactive refresh要求。
通常のaccount state確認は`false`。JSON schema自体は両方のbooleanを許容するため、
schema validationだけでは誤ったforce refreshを検知できない。
11 request variantsとtransitive definitionsは既存fixtureと構造的に同一。
現在のGetAccountResponse / LoginAccountResponse schemaをfixtureへ追加し、
現在の全18 PlanTypeを確認した。出典・hashは[fixture README](../../../tests/fixtures/app_server/README.md)を参照。

### Windowsでの再現条件とroot cause

Windows native Codex CLI 0.160.1、OS keyringの既存ChatGPT認証で、
UIはerror、`GET /api/ai/status?refresh=true`は`rpc_timeout`。
実機の個別probeはprocess start成功、initialize約7.0秒、config/read約0.1秒、
account/read false約2.0秒でChatGPT/proliteを取得した。
true約2.6秒ではaccount:nullだった。native起動・app-server・keyring自体は動作していた。

アプリは通常の認証確認でもtrueを固定送信し、不要なOAuth refreshを要求していた。
さらにRPC上限10秒はWindows cold-startの揺らぎに余裕が少なかった。
実機のtimeoutがどのRPCで発生したかは未特定であり、合計probe時間を
単一RPC timeoutの根拠にはしない。falseによる読込と有限の30秒上限で対処する。
authUrlのhost固定も公式browser flow例と非互換だった。
旧plan whitelistは未知値をnull表示にしていたが、認証自体はaccount.typeで判定していた。

### hotfix後の動作

- startup、status、UI「状態を確認」、account/updated、login completed、restart後の
  account再読込はすべて`{refreshToken:false}`。GETのrefreshは最新状態の再読込を指す。
  force token refreshを外部UI/APIへ公開せず、独自token更新・保存は行わない。
- `rpc_timeout_seconds=30`をinitialize / config/read / initial account/read / 通常RPCに共用。
  最小変更としてtimeout設定を分割しない。turnのstream待ち上限は従来の120秒。
  transportはwriteとresponseにそれぞれ有限の待ち上限を持ち、startup全体の一括30秒制限ではない。
  timeout時のprocess停止・回収、error/no-AI表示、ローカルデータ操作は維持する。
- login URLはHTTPS、exact host `chatgpt.com` / `auth.openai.com`、標準portのみ。
  userinfo（空も含む）、fragment、空白/制御文字/backslash、token/credential queryを拒否。
  OAuthのstate / code_challenge / redirect_uri等は受理する。suffix matchやdevice-code追加はない。
- account.typeがchatgptならauthenticated/ready。planは表示metadataのみ。
  proliteは`prolite`、self_serve_business_*は`business`、enterprise_cbp_* / ent26は`enterprise`、
  edu_plus/proは`edu`へnormalize。未知/欠落/不正型は`unknown`。
  email、token、raw account payloadはAPI/UI/logへ渡さない。

### PR #7追加修正: nested MCP disable

追加修正の基点はPR #7 head `7d99d5ad88ff27f4ce74cf33e65e8d5459dc0968`。
Windows実機から以下の確認結果が報告された。これは追加修正前のadapterの結果である。

| Probe | Windows実機結果 |
|---|---|
| startup/auth | PASS。ready / authenticated / prolite / error:null |
| model/list | PASS、7 entries |
| minimal thread/start | PASS |
| 全top-level params、configなし | PASS |
| web_search config | PASS |
| features config（再確認も含む） | PASS |
| legacy dotted MCP disable / full config / full knowledge_growth params | FAIL、rpc_rejected |
| nested mcp_servers disable | PASS、server-selected modelを取得 |

検出されたMCPはtradingview_mcp / node_repl。
通常の認証・起動は初回hotfixで実機成功したが、最小接続テストはthread/startで止まっていた。
原因は`mcp_servers."name".enabled`をthread configへ連結していたoverride生成方式。
top-level / web_search / featuresを分解した実機probeとnested形式の成功から原因を確認できた。

[現在のCodex temporary structured thread実装](https://github.com/openai/codex/blob/6c372fbef45f730b6d8aa727b0ec8332365588db/codex-rs/tui/src/temporary_structured_request.rs#L107)
もeffective configを読み、MCP名を収集して次のnested objectをthread configへ設定している。
現在のmainを再取得し、0.160.1のJSON schema/TSも再生成した。
thread/start / thread/resumeのconfigは自由なJSON object mapで、request schemaは変更不要。
旧dotted形式もgeneric schemaを通過するため、source・実機probe・behavioral fakeを根拠に修正する。

```json
{
  "web_search": "disabled",
  "mcp_servers": {
    "tradingview_mcp": {"enabled": false},
    "node_repl": {"enabled": false}
  },
  "features.shell_tool": false
}
```

これはconfigの抜粋。実requestは従来の全DISABLED_FEATURES、developerInstructions、
baseInstructions、read-only sandbox、approvalPolicy=never、空のcwdも維持する。
modelを固定せず、server側の選択を利用する。reasoning effortも変更しない。

config/readのMCP名だけからnested mapを作り、属性値のURL/token/env/commandを保持・コピーしない。
MCPなしでは空map。dot/dash/quoteを含む名前もJSON keyのままで、config pathとして再解釈しない。
web_search / featuresとnested mapは別のtop-level keyなのでmerge時に衝突しない。
thread/startとthread/resumeに同じoverrideを使用し、parent/userの通常Codex configは書き換えない。

malformed/rejected config discoveryでは起動をerror/no-AIにしてprocessを回収する。
session RPCが拒否されてもMCP overrideを外してretryせず、error/no-AIへ移行しprocessを回収する。
turnは開始しない。PDF/Import/保存済みBrief/Note/Highlight/exportを継続できる。
fakeも旧dotted形式・不足/不正/有効なままのnested mapを拒否し、正しい全無効mapだけでthread→turnへ進む。
fakeの特殊なserver名はliteral key handlingの検証用であり、Codexが全合成名を受理する保証ではない。

今回の追加変更ファイルは`app/ai/codex.py`、`tests/test_ai_runtime.py`、
`tests/fixtures/app_server/fake_server.py`、`tests/fixtures/app_server/README.md`、本書、
`docs/development/testing.md`。新機能・DB変更は含めない。

Linuxの0.160.1 native binaryでも、隔離temporary config / 未認証のstdio probeで
旧dotted形式→rpc_rejected、nested形式→thread/start成功を確認した。
2つの合成MCPに起動markerを設けたが起動せず、parent configは変更されず、process回収も成功。
実account/tokenは使用せず、login/turnは実行していない。これはnative config互換の確認であり、
ChatGPTの推論成功を意味しない。

### 初回Phase 0Aでの検証履歴

2026-10-04、この環境の `/opt/codex/bin/codex`、`codex-cli 0.159.0-alpha.3`を確認。
`app-server --help`、binaryから生成したJSON schema/TypeScript、隔離CODEX_HOMEでの
実binary handshakeを根拠に実装した。これは当時の検証versionの履歴であり、
現在の推奨挙動は上記0.160.1との照合と以下の表を正本とする。

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
| account | `account/read {refreshToken:false}`。account.typeがchatgptのときだけready。planの既知/未知で認証を拒否しない |
| login | `account/login/start {type:"chatgpt"}` → loginId / authUrl。`account/login/completed`、`account/updated`を受信後にaccount/read |
| cancel/logout | `account/login/cancel {loginId}` / `account/logout`（paramsなし） |
| tools | `config/read {includeLayers:false,cwd}`で継承MCP名だけを取得し、nested `config.mcp_servers[name]={enabled:false}`をthread/start・resumeへ渡す。MCP dotted leaf keyは使用しない |
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
| GET `/api/ai/status?refresh=true` | 状態・auth・capabilities。最新account stateを`refreshToken:false`で再読込。OAuth tokenのforce refreshではない |
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

### 初回hotfixの検証結果（2026-10-12、追加MCP修正前）

- baseline: 247 pytest passed。環境の不足していた既存dev依存jsonschemaを導入して実行。
- 変更後: API keyをunsetして全304 pytest passed、122.21秒。
  既存Starlette/httpx deprecation warningが1件。うちruntime関連105件。
- 実stdio fake: initialize 9.5秒 → account/read false → ChatGPT/prolite → ready → runtime-ok。
  initialize / config/read / account/read各31秒遅延は30秒でrpc_timeoutとなり、process/cwd回収。
- AI Runtime browser: 12項目passed（chatgpt.com、prolite、状態再読込、最小turn、restart、no-AI導線）。
- Structured Brief browser: 17項目passed。Import / Brief browser: 34項目passed。
- Phase 1 PDF Reader browser: 2段組/回転crop、selection、Anchor、Highlight再読込、mock Q&A、
  quote/bbox/candidates/page-only passed。全browser smokeでpageerrorsなし。
- offline最小turn: runtime-ok PASS。実Windows / 実ChatGPT accountでの再確認は未実施。

hotfixの変更ファイル:

- Runtime/config: `app/ai/codex.py`, `app/ai/__init__.py`, `app/ai/process.py`, `config/app.config.json`
- Tests/fixtures: `tests/test_ai_runtime.py`, `tests/fixtures/app_server/fake_server.py`,
  `tests/fixtures/app_server/README.md`, `tests/fixtures/app_server/GetAccountResponse.schema.json`,
  `tests/fixtures/app_server/LoginAccountResponse.schema.json`
- Browser smoke: `scripts/smoke_ai_runtime_browser.py`
- Docs: 本書, `docs/development/setup.md`, `docs/development/testing.md`

### 追加MCP修正の検証結果（2026-10-12）

- 最新PR #7 branchのbaseline: 304 pytest passed、122.60秒。
- 追加修正後: API keyをunsetして全326 pytest passed、124.28秒。runtime関連127件。
  既存Starlette/httpx deprecation warningが1件。
- fake stdio: authenticated/prolite → model/list → full thread/start・resume → nested MCP全無効
  → turn/start → runtime-ok。MCPなしとdot/dash/quoteのliteral名も成功。
- malformed/rejected discovery、invalid/incomplete/enabled nested map、server側拒否でfail closed。
  APIは503/safe error、runtimeはerror、process回収、turnやoverrideを外したretryなし。
- AI Runtime browser 12、Structured Brief browser 17、Import/Brief browser 34項目passed。
  Phase 1 PDF Readerの2段組/回転crop、selection、Anchor、Highlight再読込、mock Q&A、
  quote/bbox/candidates/page-onlyもpassed。全browser smokeでpageerrorsなし。
- offline runtime-ok: PASS。0.160.1 native config probeとschema/TS照合もPASS（上記参照）。
- 追加修正後の実Windowsアプリでの最小turn / Brief生成は未実施。次のmanual acceptanceで確認する。

### Windows実機のmanual acceptance（hotfix後）

1. native Codex CLI 0.160.1以上を準備し、`codex --version`を確認する。
   OS keyring認証を使用し、file authへfallback・独自移行しない。
2. hotfix branchで`start_knowledge_growth.bat`（またはvenvから`python run.py`）を起動。
   cold-startの待ち時間を含め、UIがerror/rpc_timeoutにならないことを確認する。
3. tradingview_mcp / node_repl等の通常Codexの設定済みMCPがある状態で確認する。
   アプリはthread-localで全無効にし、通常Codexの設定を変更しない。
   既存keyring認証があれば`● 接続済み`。未認証なら`○ ChatGPT未接続`から
   「ChatGPTで接続」→正規browser URL→ログイン完了で接続済みになることを確認する。
4. 「状態を確認」および`GET /api/ai/status?refresh=true`後もready/authenticatedを維持することを確認。
   proliteの場合もready。account状態だけを確認し、raw RPC/認証payloadのdumpはしない。
5. 「最小接続テスト」→`runtime-ok`を確認する。thread/startでrpc_rejectedにならず、MCPを実行しないことを確認。
6. 別terminalで`.venv\Scripts\python scripts\smoke_ai_runtime.py --manual`を実行し、
   `runtime-ok: PASS`を確認する。
7. アプリを起動したまま`.venv\Scripts\python scripts\smoke_brief_generation.py --manual --base-url http://127.0.0.1:8300`を実行。
   generated PDFのStructured Brief生成・保存とlocal Evidenceを確認する。
   既存仕様どおり複数turnを消費する場合がある。
8. 「再接続」およびアプリ再起動後もkeyring認証が再利用され、状態確認と最小turnが成功することを確認する。
9. 未導入/未認証/no_ai/通信失敗ではPDF、kgpack Import、保存済みBrief、Note、Highlight、exportが利用できることを確認。
   no_aiは`KG_AI_RUNTIME=no_ai`で別起動して確認できる。
10. 終了・timeout後にowned app-serverが残存しないこと、Windows Job objectの子孫回収を確認する。

token、email、raw auth payload、研究本文をlogへ出さない。
報告はCLI version、成功/失敗、safe error code、所要時間のみ。
Linux fake/browser検証は実Windowsのkeyring、OAuth、network、Job objectの確認を代替しない。
readyは推論権限・quotaを保証しないため、最小turnとStructured Briefを実機で再確認する。

## Known limitationsと次の接続点

- Codex app-serverはexperimental。確認version以外の互換性は保証しない。仕様変更はadapter/fixtureを更新する
- ユーザーのWindows実機ではstartup、keyring認証再利用、prolite/ready、nested MCP disableの直接probeが成功。
  追加修正後のアプリの最小turn / Structured Brief / Job object回収は実機manualで再確認する。
  Linux Cloudでは実ChatGPT login・推論を実施していない
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
