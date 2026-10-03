# ADR-006: Subscription-first AI Runtime と ChatGPT Import Bridge

- Status: Proposed for MVP v0.4
- Date: 2026-10-04

## Context

knowledge_growthではPaper Brief、翻訳、AI質問、会話などにAI推論が必要になる。一方、主要ユーザー方針は、OpenAI API等の従量課金を日常利用の前提にしないことである。

また、ChatGPT上では論文PDFを直接読ませ、knowledge_growthのPaper Brief schemaに沿う高度な解析を実施できる。その成果をローカルアプリへ持ち込みたい。

## Decision

1. MVPの第一候補AI Runtimeは **Codex app-server + ChatGPTプラン** とする。
2. APIキーを用いる従量課金providerはMVP対象外とする。
3. provider abstractionは残すが、MVPのセットアップ・受入条件にAPIキーを含めない。
4. **ChatGPT Import Bridge** をMVPへ追加する。
5. Import Bridgeは `.kgpack` を用い、原則PDF原本を含めない。
6. AI RuntimeやImport元にかかわらず、ResearchSource / Evidence / Noteのデータモデルはベンダー非依存にする。
7. Codex app-serverが利用できない場合でも、Import Bridgeとno-AI modeでアプリの主要データを利用可能にする。

## Rationale

- 月額プランの範囲を最大限利用できる。
- API従量課金の予測不能性を避けられる。
- ChatGPTでの深い論文読解をアプリ資産へ変換できる。
- app-serverの仕様変更リスクをadapter境界内に閉じ込められる。
- Import BridgeがAI runtimeから独立したフォールバックになる。

## Consequences

### Positive
- APIキーなしでMVPを成立させられる。
- ChatGPT上の解析とローカルの原PDF管理を分離できる。
- Import PackageにPDFを含めないため、PDF配布を避けやすい。
- 将来MCP direct integrationへ拡張可能。

### Negative / Risk
- Codex app-server + ChatGPT planは外部仕様に依存し、変更される可能性がある。
- ChatGPTプランには利用上限がある。
- Import時のEvidence再解決には曖昧性がある。
- `.kgpack` schema versioningとmigrationが必要。

## Guardrails

- 外部AI成果は原文事実と区別する。
- Import Previewを必須にする。
- 未解決Evidenceをverifiedにしない。
- user-edited項目を自動上書きしない。
- API従量課金providerをMVPの隠れ依存にしない。