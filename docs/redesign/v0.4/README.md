# knowledge_growth redesign v0.4

Status: implementation target  
Updated: 2026-10-04

Phase 0（T0-01 / T0-02、generated PDF fixtures）は実装・検証済み。
[baseline・テスト結果・残課題](phase0_baseline.md) と
[DB migration / backup / 復旧手順](../../development/db_migrations.md) を参照。
Phase 1とPaper Brief / Import Bridge backend foundationは実装済み。
T2A-04のImport確認UIとT2-05のPaper Brief表示UIは実装済み。
Phase 0AのSubscription-first AI Runtime Foundationは実装済み。日本語版Readerは未実装。

This directory is the source-of-truth package for the next reader MVP.

## Reading order

1. [knowledge_growth_mvp_spec_v0_4.md](knowledge_growth_mvp_spec_v0_4.md)  
   Product behavior, MVP scope, acceptance criteria, subscription-first AI policy.
2. [knowledge_growth_implementation_tasks_v0_4.md](knowledge_growth_implementation_tasks_v0_4.md)  
   Phased implementation task list and recommended build order.
3. [paper_brief_schema_v0_1.md](paper_brief_schema_v0_1.md)  
   Structured Paper Brief schema, especially for AI/LLM/VLM papers.
4. [import_bridge_spec_v0_1.md](import_bridge_spec_v0_1.md)  
   ChatGPT → knowledge_growth `.kgpack` import contract.
5. [../../decisions/ADR-006_subscription_first_ai_runtime_and_import_bridge.md](../../decisions/ADR-006_subscription_first_ai_runtime_and_import_bridge.md)  
   Decision record for subscription-first AI runtime and Import Bridge.

## Important distinction

The July 2026 MVP in `docs/handoff/current_status.md` is the **implemented baseline**.
The documents in this directory describe the **v0.4 implementation target**.

## Development order

Use the task IDs in the implementation task list. Recommended order:

```text
0. Baseline / migration / AI runtime foundation
1. PDF bbox / Anchor
2. Paper Brief + Import Bridge foundation
3. Japanese / Original / Compare
4. AI Question + Conversation
5. Highlight / Memo
6. Visual Clip
7. Understanding Note integration
8. Paper Map / Search
9. Voice
10. Export hardening
```

Do not implement the entire roadmap in one Codex task.

## Implementation records

- [Phase 0 baseline](phase0_baseline.md): migration基盤とgenerated fixtures。
- [Phase 1 PDF / Anchor](phase1_pdf_anchor.md): T1-01〜T1-03、schema v2、検証・制約。

- [Paper Brief / Import Bridge foundation](paper_brief_import_foundation.md): T2-01/T2-04、T2A backend、schema v3、API/CLI契約。

- [Import確認UI / Paper Brief表示](import_brief_ui.md): T2A-04/T2-05、browser smoke、Evidence navigationと制約。

- [Subscription-first AI Runtime Foundation](subscription_first_runtime_foundation.md): T0A-01〜T0A-05、現行protocol、認証、no-AI、検証・manual smoke。

Phase 2のStructured Brief実AI抽出とPhase 3以降は未実装。
