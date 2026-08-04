---
name: verify-arxiv-cites-before-commit
description: "design doc 引 arXiv 论文前,先 WebFetch arxiv.org/abs/<id> 核验标题/数字/章节吻合再 commit(上轮初稿 4 篇里 2 篇描述错)。"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-04T04:25:51.381Z
---

写设计文档引 arXiv 论文时,**commit 前先用 WebFetch `https://arxiv.org/abs/<id>` 逐条核验**:论文真实存在 + 标题/数字/章节与所述吻合。别只信调研 agent 的转述。

**Why**:2026-08-04 R3.3 P3 方案的上轮调研初稿引了 4 篇 arXiv,逐一 WebFetch 核验发现**全真(非编造 ID),但 2 篇描述错**:2512.12117 把「92% citation accuracy」夸成「100% precision」;2607.00895 张冠李戴(初稿说它有「§4.3 引用接地先于判假」,原文 §4.3 是实验结果,该文实为 span-level 幻觉检测 benchmark)。不核验就会把错误描述 commit 进文档、误导后续。

**How to apply**:凡设计文档/报告引了具体 arXiv id + 具体数字/章节,commit 前派 agent(或自己 WebFetch)逐条核 `arxiv.org/abs/<id>`;核对标题、关键数字、所引章节是否真讲那个观点。核不出或对不上的,标「疑似编造/张冠李戴」并换成真实文献或删掉。关联 [[research-deerflow-first]] [[avoid-overengineering]]。
