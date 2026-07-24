---
name: backlog-production-grade
description: "最小实现 → 对齐 deer-flow 生产级" 的补齐待办清单(跨阶段)
metadata:
  type: project
---

记录所有"当前为最小实现、后续须对齐 deer-flow 补到生产级"的点位。每条标注:位置、deer-flow 参照、目标阶段。原则见 [[align-to-deerflow-production-grade]]。

**当前清单:**

1. **`grep` / `glob`(LocalSandbox)** — `src/hyperion/platform/sandbox/local.py`。
   - 现状:字面子串 grep、`rglob` 无忽略清单、返回 `list`、无二进制/大文件/ReDoS/符号链接守卫。
   - 对齐:移植 deer-flow [search.py](deer-flow/backend/packages/harness/deerflow/sandbox/search.py) 整文件(`find_glob_matches` / `find_grep_matches` / `GrepMatch` + `IGNORE_PATTERNS` + `should_ignore_name` + `is_binary_file` + ReDoS 行长守卫 + 符号链接 `is_relative_to` 守卫);`local.py` 改成薄包装;返回改 `tuple[list, bool]`(截断标志);grep 支持正则/literal/case/glob 过滤。
   - 目标阶段:**P1**(代码理解服务,搜索是主力)。P0 demo 不直接暴露 grep/glob 给 agent,故可延后。

2. **`str_replace` 大文件截断风险** — `src/hyperion/tools/sandbox.py:str_replace_tool`。
   - 现状:经 `read_file`(50000 字符截断)读整文再写回;>50KB 文件会被截断标记污染、丢数据。
   - 对齐:deer-flow 的 read-before-write hash 门(读全文 + 内容哈希校验 + 同路径串行锁 `sandbox/file_operation_lock.py`)。
   - 目标阶段:**P2**(Bug-RCA 真实改补丁,文件可能较大)。P0 workspace 文件小,暂可接受。

3. **agent 中间件链(生产级)** — `src/hyperion/platform/agent.py:build_middlewares`。
   - 现状:`create_agent(..., middleware=[])` 裸跑,无任何中间件护栏。
   - 对齐:deer-flow 的中间件链(`deer-flow/backend/.../agents/lead_agent/agent.py:build_middlewares`,~30 项)。Hyperion 需要的子集:`ToolErrorHandlingMiddleware`(工具异常转 ToolMessage 不崩)、`ToolOutputBudgetMiddleware`(工具输出进上下文前的预算)、`ReadBeforeWriteMiddleware`(写前哈希门,配合 backlog 第 2 条)、`LoopDetectionMiddleware`(重复工具调用循环检测)、`TokenBudgetMiddleware`(per-run token 上限)、`SummarizationMiddleware`(上下文压缩)。
   - 目标阶段:**P2**(Bug-RCA 多步长链路需要这些护栏)起逐步加,按需逐个挂。

4. **索引构建原子性 + 状态清单(P1.2)** — 见 [P1 报告 v2.2 §10](docs/p1-code-understanding-design.md)。
   - 最小实现会裸写 LanceDB 表;生产级必须:**temp 表 `lancedb_tmp` + 全部成功后原子 rename**(中途崩不留半成品)+ 落盘 `index_manifest.json`(`repo_commit` / `model_fingerprint` / `schema_version` / `file_manifest`),检索前校验。增量更新用 `file_manifest` 文件级对账(非只看 mtime),处理重命名/移动防 chunk id 孤儿。
   - 目标阶段:**P1.2**(`index.py` 落地时直接做,别先裸写后补)。

5. **评测行级映射 + 难度分层(P1.3)** — 见 [P1 报告 v2.2 §11](docs/p1-code-understanding-design.md)。
   - 最小实现会"对父/子提交各跑 parser 再 diff 符号列表"——**错的**:只改函数体几行的 fix,父子符号集合相同,diff 抓不到。生产级:**git diff 行 → 包住的最内层符号**;查询分 L1(含函数名)/L2(行为描述)/L3(跨模块)分档报 recall + 负例报 precision;退出标准定为 **L2 档 top-5 recall ≥ 0.6**。
   - 目标阶段:**P1.3**(搭评测时直接做)。

6. **chunker 超大符号的 AST 切分(C 场景)** — `src/hyperion/services/code_index/chunker.py`。
   - 现状:有 `MAX_CHUNK_CHARS=20000` 阈值,但 `_chunk_one_file` 对超长 chunk 仅整块保留(`CodeChunk.part`/`total` 字段已预留,未实现切分)。Python(deer-flow)函数不触发;C(bluez 200+ 行状态机 / init 函数)会。
   - 对齐:落地 cAST(EMNLP 2025)的 Alg.1 **split 部分** —— 递归拆 body 的子语句、相邻小子语句贪心合并防碎片,每段带 `parent_symbol` + 行范围 + `part N/M`。注意**只拆单个超大符号内部,不跨符号 merge**(chunk 兼任 `read_function` 的单符号语义,合并会破坏)。
   - 目标阶段:**C 场景(bluez)/ P1.5+**(P1.1 Python 不触发,延后)。

7. **前导注释 / doxygen 抽取(C 场景)** — `src/hyperion/services/code_index/parser.py`。
   - 现状:`LanguageGrammar.extract_docstring` 只抽 Python body 首条 string(docstring);不抽"紧邻符号之前的 comment 节点"(tree-sitter 里 comment 在父节点 `.children`、**不在** `.named_children`,当前 DFS 走 named_children 看不到)。
   - 对齐:加 leading-comment extractor —— 从父节点 `.children` 回溯本节点之前紧邻的连续 `comment` 节点(C 的 `/** */` doxygen 是主语义信号;Python `#` 注释次要)。Python 靠 docstring 已够,延后。
   - 目标阶段:**C 场景**。

8. **Embedder 完整三态加载 + 冷却自愈(P1.2 生产级)** — `src/hyperion/services/code_index/embed.py`。
   - 现状:最小实现 `Embedder.__init__` 直连 `SentenceTransformer(...)`,单进程一次加载;sentence-transformers 自带模块级缓存;下载失败只抛普通异常。
   - 对齐:借鉴 deer-flow tiktoken 懒加载模式([prompt.py:190-260](deer-flow/backend/packages/harness/deerflow/agents/memory/backends/deermem/deermem/core/prompt.py))—— 模块级缓存 + Lock、三态 sentinel(未加载 / 加载中 / 失败带时间戳)、加载中去重、**失败 600s 冷却自愈**(transient 网络故障免重启)、启动预热钩子(`warm()`,避免首请求被 ~1.2GB 下载阻塞)。模型下载体积远大于 tiktoken BPE,问题更严重。
   - 目标阶段:**P1.2 之后**(单进程顺序建索引期最小实现够用;转长驻服务 / 并发检索时上)。

9. **embedding CPU ONNX int8 提速(P1.2 生产级)** — `src/hyperion/services/code_index/embed.py`。
   - 现状:最小实现用原生 sentence-transformers(PyTorch),CPU 批编码仍是瓶颈;bluez 几十万行首建几十分钟级。
   - 对齐:`optimum` + `onnxruntime` 把模型导出 ONNX + 动态量化 int8,CPU **2-3x 提速**([sbert.net efficiency 指南](https://sbert.net/docs/sentence_transformer/usage/efficiency.html))。注意量化主要省内存、提速看 batch / 硬件,需实测。
   - 目标阶段:**P1.2 之后 / P6 生产化**(首建耗时成痛点时上;有 GPU 直接 config 切 bge-code-v1 或 `device: cuda`)。

10. **TRF(Tensor-based Re-ranking Fusion)P2 精度升级** — `src/hyperion/services/code_index/retrieval.py`。
    - 现状:P1.3 用 LanceDB 原生 RRF(k=60)融合 BM25+向量。
    - 对齐:[Balancing the Blend (arXiv 2508.01405, 2025-08)](https://arxiv.org/html/2508.01405v2) 提出 TRF——用 ColBERT MaxSim 当"融合器"(而非检索器):hybrid 候选池上跑 [answerai-colbert-small-v1](https://www.answer.ai/posts/2024-08-13-small-but-mighty-colbert.html)(33M 参数)MaxSim 重排。论文实测稳定胜过 RRF +5~8% nDCG,且**解决长文档 cross-encoder 塌陷**(C 长函数场景)。代价:+33M 模型 + token 向量存储。
    - 目标阶段:**P2**(P1.3 RRF 评测达标后作精度升级;挂在 LanceDB `Reranker` 接口上,见 [[align-to-deerflow-production-grade]])。

11. **评测自动金标(CoSQA+ test-driven agent)** — `eval/`。
    - 现状:P1.3 金标靠 git diff hunk → tree-sitter 行级映射(独立、可复现,但需 fix commit 存在)。
    - 对齐:[CoSQA+ (arXiv 2406.11589)](https://arxiv.org/html/2406.11589v7) 的 test-driven agent 自动金标(**93.9% 准确率 > 人工 89.1%**):用可执行验证 + LLM 仲裁判定"该 chunk 是否 fix 该改的位置"。bluez/wpa 可编译可测,这套范式可借鉴扩充评测集规模。
    - 目标阶段:**P1.3 之后**(评测集要扩量时上,降低人工标注成本)。

12. **远端 embedding/reranker provider 硬化(细化 #8)** — `src/hyperion/services/code_index/embed.py` RemoteEmbedder / `retrieval.py` RemoteReranker。
    - 现状:最小实现 `_raw_embed` 只 `sorted(resp.data, key=index)` 无校验;失败普通异常;默认 UA。
    - 对齐:借鉴 code-review-graph `embeddings.py:380-600`(OpenAIEmbeddingProvider):① **响应 index 三分支校验**(全有→0..N-1 置换校验;全无→仅校验 count;混合→拒绝)——DashScope/LiteLLM 网关乱序/丢项硬防御;② **精确 retryable 分类**(RemoteDisconnected/IncompleteRead/BadStatusLine/ssl.SSLError/socket.timeout);③ **4xx body 透传**(解析 JSON error 抛真实原因);④ **自定义 User-Agent**(hyperion/<version>,规避 Cloudflare 拒 Python-urllib);⑤ **provider 身份含 endpoint+model**(规范化 userinfo/默认端口/trailing slash,进 fingerprint)。CRG 有现成实现 + 完备测试(`tests/test_embeddings.py`)。
    - 目标阶段:**P1.3 retrieval.py 落地时**(RemoteEmbedder + RemoteReranker 一并硬化),或 P2 生产化。

(后续每发现一处最小实现就追加一条。)
