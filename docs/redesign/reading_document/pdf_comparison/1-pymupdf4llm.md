# Synthetic Research Fixture

## 1 Architecture

### Input Encoder

#### Figure 1. Synthetic input-encoder-output architecture.

 As shown in Figure 1, the encoder maps input to output. A frozen backbone is evaluated in a few-shot setting.

### y = W x + b  (1)

#### Equation (1) is a synthetic linear mapping. Shared evidence phrase appears on both pages.


-----

## 2 Results

#### Table 1. Synthetic benchmark scores (invented test values).

Dataset | Metric | Setting | Baseline | Proposed

Synthetic-00 | Accuracy | few-shot | 60.0 | 62.0

Synthetic-01 | Accuracy | few-shot | 61.0 | 63.0

Synthetic-02 | Accuracy | few-shot | 62.0 | 64.0

Synthetic-03 | Accuracy | few-shot | 63.0 | 65.0

Synthetic-04 | Accuracy | few-shot | 64.0 | 66.0

Synthetic-05 | Accuracy | few-shot | 65.0 | 67.0

Synthetic-06 | Accuracy | few-shot | 66.0 | 68.0

Synthetic-07 | Accuracy | few-shot | 67.0 | 69.0

Synthetic-08 | Accuracy | few-shot | 68.0 | 70.0

Synthetic-09 | Accuracy | few-shot | 69.0 | 71.0

Synthetic-10 | Accuracy | few-shot | 70.0 | 72.0

Synthetic-11 | Accuracy | few-shot | 71.0 | 73.0

#### Table 1 contains invented scores for test use only. Shared evidence phrase appears on both pages.

## 3 Conclusion

#### This fixture is for offline regression testing only.


-----
