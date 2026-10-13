"""Offline deterministic translations of synthetic fixtures only; never real papers."""
import re

FIXTURE_TRANSLATIONS = {
    'Synthetic Research Fixture': '合成研究用の検証資料',
    'Abstract': '概要',
    '1 Introduction': '1 はじめに',
    '2 Results': '2 結果',
    '3 Conclusion': '3 結論',
    '1 Architecture': '1 アーキテクチャ',
    'We study a synthetic method for reproducible research. The method maps input to output and preserves the original evidence.':
        '本研究では、再現可能な研究のための合成手法を検討する。この手法は入力を出力に変換し、元の根拠を保持する。',
    'The synthetic method processes input data in two stages. A frozen backbone is evaluated in a few-shot setting. The experiment uses generated data only.':
        'この合成手法は入力データを二段階で処理する。凍結したバックボーンをFew-shot設定で評価する。実験では生成データだけを使用する。',
    'Figure 1. Synthetic input-encoder-output architecture.': 'Figure 1. 合成手法の入力・エンコーダ・出力構成。',
    'Table 1. Synthetic benchmark scores (invented test values).': 'Table 1. 合成ベンチマークのスコア（検証用の架空値）。',
    'As shown in Figure 1, the encoder maps input to output. A frozen backbone is evaluated in a few-shot setting.':
        'Figure 1に示すように、エンコーダは入力を出力に変換する。凍結したバックボーンをFew-shot設定で評価する。',
    'Table 1 contains invented scores for test use only. Shared evidence phrase appears on both pages.':
        'Table 1には検証専用の架空スコアを示す。共通の根拠表現が両ページに記載されている。',
    'This fixture is for offline regression testing only.': 'この資料はオフラインの回帰テスト専用である。',
    'This generated document tests paper registration and reading. It contains original synthetic text with no external paper content. The synthetic method processes an input and returns an output.':
        'この生成文書で論文の登録と閲覧を検証する。外部論文を含まない独自の合成文章を収録する。合成手法は入力を処理して出力を返す。',
    'The synthetic experiment produces a reproducible test result. This second page checks page numbering and navigation. No real benchmark performance is claimed.':
        '合成実験で再現可能な検証結果を得る。この次のページでページ番号と移動を確認する。実際のベンチマーク性能を示すものではない。',
}


def fixture_reply(context):
    blocks = []
    for b in context['blocks']:
        original = b['text']
        text = FIXTURE_TRANSLATIONS.get(original)
        if match := re.fullmatch(r'(LEFT|RIGHT) paragraph (\d+)\. Synthetic column content for reading order\.', original):
            text = f'{"左" if match[1] == "LEFT" else "右"}列の段落{match[2]}。読み順を確認するための合成文章。'
        if match := re.fullmatch(r'1 (LEFT|RIGHT) Column', original):
            text = f'1 {"左" if match[1] == "LEFT" else "右"}列'
        blocks.append({'block_id': b['block_id'], 'translated_text': text or '',
                       'status': 'translated' if text else 'uncertain'})
    return {'blocks': blocks}
