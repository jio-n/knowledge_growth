---
id: translate
version: 1
purpose: 選択箇所・段落の日本語訳。専門用語の原語併記。
inputs: selection
output: 訳文テキストのみ
used_by: app/routes_qa.py (translate endpoint)
---
以下のテキストを自然な日本語に翻訳してください。

規則:
- 学術文書として正確に訳す。意訳しすぎない。
- 重要な専門用語は「日本語訳 (原語)」の形式で初出時に原語を併記する。
- 数式・記号・引用番号はそのまま残す。
- 訳文のみを出力する。前置きや注釈を付けない。

---SELECTION---
{selection}
---END---
