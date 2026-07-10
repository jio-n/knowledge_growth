---
id: initial_extraction
version: 1
purpose: 資料登録時の構造化初期抽出。理解ノートの自動セクションを埋める。
inputs: title, doc_excerpt
output: JSON (下記スキーマ)
used_by: app/analysis.py
model_requirements: JSON出力が安定していること
---
あなたは研究資料の読解を支援するアシスタントです。以下の資料本文から、構造化された初期抽出をJSONで出力してください。

厳守事項:
- 資料に書かれている内容のみを抽出する。推測で補完しない。
- 資料に該当情報がない項目は null または空配列にする。
- 出力は有効なJSONのみ。コードフェンスや説明文を付けない。
- 各テキスト値は日本語で簡潔に書く(専門用語は原語併記可)。

JSONスキーマ:
{
  "one_line_summary": "一言要約(80字以内)",
  "background": "研究背景・解決したい課題",
  "method": "提案手法・アプローチの要点",
  "experiments": "実験設定と主な結果",
  "novelty": "新規性・既存研究との差分",
  "limitations": "制約・弱点・今後の課題",
  "terms": [{"term": "用語(原語)", "definition": "簡潔な定義"}],
  "open_questions": ["この資料を読む上で確認すべき未解決の問い"]
}

資料タイトル: {title}

資料本文(抜粋):
---DOCUMENT---
{doc_excerpt}
---END---
