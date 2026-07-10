# client/vendor

ベンダリングされたクライアントライブラリ置き場(gitignore対象、`python scripts/fetch_vendor.py` で取得)。

- `pdf.mjs` / `pdf.worker.mjs` — pdf.js 4.5.136(PDF描画+テキストレイヤー)
- `marked.esm.js` — marked 12.0.2(AI回答のMarkdown表示)

CDN参照は禁止(ローカルファースト、ADR-001)。バージョンを上げる場合は fetch_vendor.py のURLを更新し動作確認すること。
