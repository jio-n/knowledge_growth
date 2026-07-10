# AI 処理フロー

最終更新: 2026-07-11 / 状態: 確定(v1)

## プロバイダー抽象(ADR-003)

```
app/llm/base.py    LLMProvider.complete(system, user, max_tokens=None, hint=None) -> LLMResult(text, provider, model)
                   LLMProvider.complete_json(...)  # JSON強制のバリアント
app/llm/anthropic_provider.py   Anthropic Messages API(素のhttpx、SDKなし)
app/llm/openai_compat.py        /chat/completions 互換(OpenAI/Ollama/LM Studio/vLLM)
app/llm/mock.py                 オフラインモック(回答に明示ラベル、翻訳はhintで分岐)
```

- 選択: `config/app.config.json` の `llm.provider`、または環境変数 `KG_LLM_PROVIDER`(テスト用オーバーライド)。
- APIキー: 環境変数(名前は設定の `api_key_env`)。設定ファイルにキーを書かない。
- `hint` はモックが出力を分岐するためだけの任意タグ。実プロバイダーは無視する。
- 新プロバイダー追加 = base実装1ファイル + configエントリ + `app/llm/__init__.py` の分岐1行。

## プロンプト管理(§26)

`prompts/<id>.md`、frontmatter(id/version/purpose/inputs/output/used_by)+ 本文(`{placeholder}` 置換)。ローダーは `app/prompts.py`。プロンプト変更時は version を上げ、answers.prompt_version に記録されるため回答の再現条件が追える。

| id | 用途 | 出力 |
|----|------|------|
| initial_extraction | 登録時の構造化抽出 | JSON |
| answer_question | 選択箇所起点の質問回答(事実/解釈の区別を指示) | Markdown |
| translate | 日本語訳(原語併記規則) | テキスト |
| suggest_save_target | 保存先セクション・情報種別・タイトル提案 | JSON |

## パイプライン1: 初期構造化抽出(app/analysis.py)

```
登録 → 別スレッド起動 → analysis_status: pending→running
→ doc_excerpt(先頭80% + 結論部、上限24k字)
→ initial_extraction プロンプト → complete_json → JSON parse
→ 失敗 or mock("{}") → ヒューリスティック抽出へフォールバック
   (先頭長段落=背景/要約、見出し一覧=構成、フォールバック実行の旨を open_questions に明記)
→ knowledge_items へ origin='auto_extract' で保存(既存 auto_extract は置換、他originは不可侵)
→ analysis_status: done / error(analysis_error に理由)
```

## パイプライン2: 質問応答(app/routes_qa.py + app/context.py)

コンテキスト構築(§9「全文を無制限に投入しない」に対応):
```
選択ブロック ± surrounding_blocks(既定2)   ← config/context
+ 見出し階層(heading_path)
+ ノートダイジェスト(最近の知識項目 12件を1行ずつ)  ← ユーザーの現在の理解をモデルに伝える
+ 質問文(prompt_type別の定型 or 自由文)
上限: max_context_chars(既定8000字)
```
answer_question プロンプトは「資料の事実」「AIの解釈」の区別、原文引用の包含、不明の明示を指示する。
記録: answers に provider/model/prompt_id/prompt_version/context_summary(何を送ったかのJSON)。

## パイプライン3: 翻訳

ブロック単位はDBキャッシュ(translations)。ユーザー修正は user_edited=1 でAI訳と区別(§15)。

## パイプライン4: 保存先提案

ヒューリスティック(prompt_type→セクション対応)を即時返却の基本とし、LLMが利用可能なら suggest_save_target で精緻化。**このAPIは失敗してもヒューリスティック結果を返す**(保存操作をブロックしない — §22.4)。

## 失敗時の振る舞い(設計原則)

| 障害 | 挙動 |
|------|------|
| LLM API エラー | 質問=502で明示エラー(UIに再試行)。抽出=ヒューリスティックへフォールバック。提案=ヒューリスティック返却 |
| JSON parse失敗 | 抽出/提案ともフォールバック(アプリは落ちない) |
| APIキー未設定 | anthropicは起動時でなく呼び出し時に日本語エラー。mockなら全機能動作 |

## コスト方針

- 全文をLLMへ送らない(抽出は24k字上限、質問は8k字上限)。
- 翻訳・提案はキャッシュ/ヒューリスティック優先。
- モデルはタスク別に将来分離可能(設定に per-task モデル指定を追加する拡張余地 — roadmap)。
