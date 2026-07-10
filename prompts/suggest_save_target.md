---
id: suggest_save_target
version: 1
purpose: 保存する知識の保存先セクションと情報種別を提案する。
inputs: sections, content
output: JSON {"section_key": "...", "info_type": "...", "title": "..."}
used_by: app/routes_knowledge.py
---
ユーザーが研究資料の理解ノートに保存しようとしている内容があります。最も適切な保存先セクションと情報種別を選んでください。

利用可能なセクション(key: ラベル):
{sections}

情報種別の選択肢: fact(原文の事実) / claim(著者の主張) / result(実験結果) / llm_summary(AI要約) / llm_interpretation(AI解釈) / user_thought(ユーザー考察) / open_question(未解決の疑問) / idea(応用アイデア) / term(用語) / action(次のアクション)

出力は有効なJSONのみ:
{"section_key": "セクションkey", "info_type": "情報種別", "title": "30字以内の見出し"}

保存する内容:
{content}
