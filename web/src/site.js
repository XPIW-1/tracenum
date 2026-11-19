// Site-wide facts shown to visitors. Keep every statement here measured and dated.

// Filing number of the generative model service, copied from the official public list.
export const FILING_NUMBER = ''

// Notes on featured questions whose live behaviour has changed since they were recorded.
export const FEATURED_NOTES = {
  'WSI-2025-008': '这条是 2026-11-11 的真实录制（1 轮答对 22%）。2026-11-18/19 用同样配置重跑，模型三轮后拒答，换用论文的本地重排也一样；这道题对模型侧的变化较敏感。',
}

// How the live service differs from the paper's experiment (also in the README).
export const LIVE_DIFFERENCES = [
  '重排：线上调用托管的同一模型 BAAI/bge-reranker-v2-m3。与论文本地分数对照 98 轮检索，76.5% 的轮次前 5 名顺序完全一致；其余差异都出现在分数几乎并列（相差小于 0.01）的位置，其中 18 轮只是前 5 名内部换位。精选题端到端复核中，托管重排答错的题，论文本地重排同样答错。',
  '生成：DeepSeek 返回的服务指纹自 2026-11-11 起与论文正式实验时不同（模型名未变），同一道题的结果可能与论文记录不同。',
]
