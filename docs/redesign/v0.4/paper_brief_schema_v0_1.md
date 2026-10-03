# Paper Brief Schema v0.1

knowledge_growthで論文を読み込んだ直後に表示する構造化要約のスキーマ。
AI/LLM/VLM系論文を優先ユースケースとして設計するが、Core Schemaは一般論文でも使用できる。

---

## 1. UXの基本

Paper Briefは長文要約ではなく、以下の2層で表示する。

### 30秒Brief
- 一言要約
- 研究目的
- 主要課題
- 対象タスク
- 新規性
- 主な結果

### Structured Brief
- モデル
- 学習設定
- Dataset
- Metric
- Score
- Architecture
- Limitation
- Figure/Table
- Reproducibility

各fieldは `value + confidence/status + evidence[]` を持つ。

---

## 2. JSON概念形

```json
{
  "schema_version": "paper-brief-0.1",
  "paper_type": {
    "value": "method",
    "status": "confirmed",
    "evidence": []
  },
  "one_line_summary": {
    "value": "...",
    "status": "derived",
    "evidence": []
  },
  "research_objective": {},
  "background": {},
  "problem": {},
  "target_task": [],
  "target_domain": [],
  "model": {
    "family": [],
    "base_models": [],
    "backbones": [],
    "modalities": [],
    "trainable_parts": [],
    "frozen_parts": [],
    "parameter_count": null
  },
  "learning": {
    "regimes": [],
    "shots": null,
    "supervision": [],
    "adaptation_methods": [],
    "pretraining": []
  },
  "architecture": {
    "summary": "...",
    "components": [],
    "data_flow": [],
    "important_figures": []
  },
  "evaluation": {
    "datasets": [],
    "settings": [],
    "metrics": [],
    "key_results": [],
    "baselines": [],
    "ablation": []
  },
  "novelty": [],
  "limitations": [],
  "failure_cases": [],
  "reproducibility": {
    "code": null,
    "weights": null,
    "data": null,
    "compute": null
  },
  "suggested_reading_order": []
}
```

---

## 3. Key Resultの形

スコア値だけを保存しない。

```json
{
  "dataset": "MVTec-AD",
  "task": "image-level anomaly detection",
  "setting": "one-shot",
  "metric": "AUROC",
  "score": 94.1,
  "unit": "%",
  "split": null,
  "comparison": "...",
  "status": "confirmed",
  "evidence": ["page:8", "table:1"]
}
```

これにより、後で「94.1という数字が何のスコアだったか」が分からなくなるのを防ぐ。

---

## 4. Learning Regime vocabulary

最低限:

- zero-shot
- one-shot
- few-shot
- many-shot
- training-free
- in-context learning
- supervised
- self-supervised
- full fine-tuning
- fine-tuning
- PEFT
- LoRA
- adapter
- prompt tuning
- prompt learning
- instruction tuning
- distillation
- RLHF
- RLAIF
- frozen backbone
- synthetic-data training
- retrieval-augmented

複数選択可。

---

## 5. Architecture表示

テキスト要約だけではなく、可能なら重要Figureを関連付ける。

表示例:

```text
Architecture
- Vision Encoder: CLIP ViT-L/14 [frozen]
- Prompt Learner: trainable
- LLM: Vicuna-7B [frozen]
- Image Decoder: anomaly localization

[Fig.2 Architectureを開く]
```

`重要Figure` はPaper MapとVisual Clipへ接続する。

---

## 6. Status

- confirmed: 原文に明示
- derived: 原文から要約・統合
- uncertain: 曖昧
- not_reported: 記載なし
- not_applicable: 非該当

UIでは `uncertain` / `not_reported` を明示する。

---

## 7. Paper Typeによる表示差

### Method Paper
モデル / 学習設定 / Architecture / Results を優先。

### Benchmark Paper
Benchmark目的 / Dataset構成 / Task taxonomy / Metrics / Evaluated models / Findings を優先。

### Survey
Scope / Taxonomy / Included methods / Trends / Open problems を優先。

### Dataset Paper
Dataset composition / annotation / split / license / baseline / bias を優先。

Paper Typeに応じて項目順を変えるが、保存形式はCore Schemaを維持する。

---

## 8. Paper Briefからの操作

各fieldに:

- 📍 原文へ
- 関連Figure/Table
- AIに質問
- ノートに採用

を設ける。

最上部:

- 日本語版で読む
- 原文PDFを見る
- 推奨箇所から読む
