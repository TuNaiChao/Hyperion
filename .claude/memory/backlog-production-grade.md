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

4. **索引构建原子性 + 状态清单(P1.2)** — 见 [P1 报告 v2.2 §10](../../docs/设计/p1-code-understanding-design.md)。
   - 最小实现会裸写 LanceDB 表;生产级必须:**temp 表 `lancedb_tmp` + 全部成功后原子 rename**(中途崩不留半成品)+ 落盘 `index_manifest.json`(`repo_commit` / `model_fingerprint` / `schema_version` / `file_manifest`),检索前校验。增量更新用 `file_manifest` 文件级对账(非只看 mtime),处理重命名/移动防 chunk id 孤儿。
   - 目标阶段:**P1.2**(`index.py` 落地时直接做,别先裸写后补)。

5. **评测行级映射 + 难度分层(P1.3)** — 见 [P1 报告 v2.2 §11](../../docs/设计/p1-code-understanding-design.md)。
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

13. **评测集扩到生产级(P1.3 之后)** — `eval/sets/`。
    - 现状:P1.3 人工 curate 18 条(8 L1 + 10 L2,Hyperion 自身代码),indicative 非统计 tight。
    - 对齐:扩到 ≥150 条(L1/L2/L3 各 ≥50);L3 跨模块需 P1.5 code_graph;**git-diff 行级金标自动抽取**(`git log --grep="Fixes:"` → diff hunk → tree-sitter 最小包围符号 = gold,见 §11)替手工 curate;跨 repo(holdout)防过拟合。借 CoSQA+ test-driven 自动金标(#11)降标注成本。
    - 目标阶段:**P1.3 之后 / P2**(评测集要上量、要统计 tight 时)。

14. **BM25-only baseline 模式(证语义增益)** — `retrieval.py` / `eval/run_eval.py`。
    - 现状:retrieve 只支持 hybrid+RRF(+可选 rerank),无纯 BM25 路径;退出标准条件 5(BM25 baseline L2 recall ≤ 0.40)未测。
    - 对齐:retrieve 加 `mode="bm25"|"vector"|"hybrid"`(对应 store 的 fts-only / vector-only / hybrid);eval 跑 BM25 baseline 对比,证语义检索真有增益。
    - 目标阶段:**P1.3 之后**(补全退出标准条件 5)。

15. **holdout repo 评测(防过拟合)** — `eval/`。
    - 现状:只在 Hyperion 自身代码评测(单 repo),退出标准条件 6(holdout 衰减 ≤ 15pp)未测。
    - 对齐:在第二个仓库(如 deer-flow 子集)建索引 + curate 评测集,对比 L2 recall 衰减;衰减大说明过拟合 Hyperion 代码风格。
    - 目标阶段:**P1.3 之后 / P2**(有第二个被测仓库时)。

16. **precision 指标校准(P1.3 实测发现)** — `eval/scorer.py` / `runner.py`。
    - 现状:`precision@5 = |top5∩gold|/5`,小 gold 集(1-2 符号)天然封顶 |gold|/5≈0.2-0.4(P1.3 实测 0.240),退出标准 0.40 数学上不可达。
    - 对齐:加 `precision_at_min_k(retrieved, gold, k) = |top-k∩gold| / min(k, |gold|)`(R-precision 风格,单 gold 命中=1.0);退出标准条件 2 改用它。
    - 目标阶段:**P1.3 之后**(scorer/runner 小改,顺手)。

(后续每发现一处最小实现就追加一条。)

---

## 借鉴 oh-my-pi(omp)的新能力项

> 来源:2026-07-27 深读 omp(can1357/oh-my-pi)+ 2026 最佳实践,详见 [docs/调研/后续设计演进报告-oh-my-pi与最佳实践.md](../../docs/调研/后续设计演进报告-oh-my-pi与最佳实践.md)。下列每条标注:位置 / omp 参照(带行号)/ 目标阶段。原则同上:可最小起步,但须排期到生产级。与上文 #1–#16 有重叠处已标"升级 #N"。

17. **代码理解三层栈:LSP/clangd 层(升级检索为精确导航)** — 新增 `src/hyperion/services/code_nav/lsp.py`(暂定)。
    - 现状:P1.3 只有向量+BM25 模糊召回,做不了"这函数被谁调"这种确定性查询;tree-sitter 单文件切块碰不到系统头文件。
    - 对齐:omp [packages/coding-agent/src/lsp/](../../oh-my-pi/packages/coding-agent/src/lsp/)——先做 `references`/`definition`/`hover` 三件套(`index.ts:2510-2556` 的 2 次重试+250ms 退避防索引未完成);定位 `file+line+symbol`;**硬前提 `compile_commands.json`**(bluez `bear -- make`,systemd cmake `-DCMAKE_EXPORT_COMPILE_COMMANDS=ON`)。**Python 用 multilspy(client 库),不要用 pygls(那是写 server 的)**。
    - 目标阶段:**P1.5**(ROI 最高,调用链从模糊变精确)。学术锚点:ChatDBG。

18. **三层栈:DAP 调试器层(lldb-dap/gdb 现场深挖)** — 新增 `src/hyperion/services/debug/dap.py`(暂定)。
    - 现状:无运行时现场能力,不可复现 bug 只能靠日志猜。
    - 对齐:omp [packages/coding-agent/src/dap/](../../oh-my-pi/packages/coding-agent/src/dap/)——`attach(pid)`/`set_breakpoint`/`continue`/`stack_trace`/`scopes`/`variables`(递归展开 `struct *`);attach 流程 [session.ts:357-418](../../oh-my-pi/packages/coding-agent/src/dap/session.ts#L357-L418)(先订阅 stop 再 attach)。gdb/lldb-dap/codelldb 都支持 attach-by-pid。**无成熟 Python DAP client 库,手写 ~600–800 行,复用 LSP 的 Content-Length framing**。门槛:`-g` 符号、`ptrace_scope`、D-Bus 激活时机。
    - 目标阶段:**P2**(可复现 bug 才用得上;门槛高,LSP 先行)。学术锚点:ChatDBG / KernelDiag。

19. **Hashline 替代 str_replace(升级 #2)** — `src/hyperion/tools/sandbox.py:str_replace_tool`。
    - 现状:#2(str_replace 50000 字符截断丢数据、首次命中率低)。
    - 对齐:omp [packages/hashline/](../../oh-my-pi/packages/hashline/)——行锚点补丁 + 整文件哈希 tag,模型**只输出变化行零复述旧文本**(−61% token);三层 fail-cosed:tag 校验([format.ts:85-116](../../oh-my-pi/packages/hashline/src/format.ts#L85-L116))+ seen-line 守卫([patcher.ts:555-587](../../oh-my-pi/packages/hashline/src/patcher.ts#L555-L587))+ recovery([recovery.ts](../../oh-my-pi/packages/hashline/src/recovery.ts))。原作者用 **lark 文法**(Python 直接有 `lark`),`difflib.SequenceMatcher` 对应其 `diffLineRuns`,`tree-sitter-c` 支持 `SWAP.BLK` 块锚。
    - 落地三阶段:MVP(行级 ops+哈希+内存快照+apply+MismatchError,1-2天,根治截断)→ seen-line 守卫+容错解析 → recovery+块锚。
    - 目标阶段:**P2**(Bug-RCA 出补丁时)。

20. **TTSR 流式规则注入(升级 #3 的一部分)** — `src/hyperion/platform/agent.py`。
    - 现状:#3(中间件链空,无护栏)。
    - 对齐:omp [export/ttsr.ts](../../oh-my-pi/packages/coding-agent/src/export/ttsr.ts)+[session/ttsr-coordinator.ts](../../oh-my-pi/packages/coding-agent/src/session/ttsr-coordinator.ts)——规则休眠零 token,流式命中 regex/AST 才中断→注入→重试;`contextMode:discard` 抹 partial。**⚠️"survive compaction" 是盖戳状态存活不是文本存活**(注入消息会被 compaction 删,`branch-summarization.ts:196-205`)。
    - 落地(LangGraph):包在 model call 节点外的 wrapper(`astream_events_v2` 的 `on_chat_model_stream` 累 token→regex→break+cancel→pop partial→append SystemMessage→重入);盖戳存 SQLite/JSONL side-log。只适合高置信/低频/高代价错误。
    - 目标阶段:**P2**。

21. **Advisor 副驾模型(Bug-RCA 第二意见,升级 #3 的一部分)** — 新增 `src/hyperion/platform/advisor.py`(暂定)。
    - 对齐:omp [packages/coding-agent/src/advisor/](../../oh-my-pi/packages/coding-agent/src/advisor/)——独立 model+独立 Agent,主 turn 末送增量 delta(游标+wyhash 指纹);`advise({note,severity})` 三档(nit/concern/blocker);`<advisory guidance="weigh, don't blindly obey">`(主 agent prompt 不提 advisor);完全隔离 ToolSession;**失败永不阻塞主**。**⚠️三层 emission-guard 是 load-bearing**(omp 实测一 advisor 刷 309 次/114×"Stop.",抄 [emission-guard.ts](../../oh-my-pi/packages/coding-agent/src/advisor/emission-guard.ts));**别用比主 agent 更强的模型**。
    - 目标阶段:**P2**(Bug-RCA 配"反方假设"+"修复副作用"两 advisor,不同模型家族避同源盲区;成本警告:按需启用+便宜模型+增量 delta)。

22. **read = tree-sitter BFS 摘要 + elision footer + 二进制守卫** — `src/hyperion/tools/sandbox.py:read_file_tool`。
    - 现状:read 直 dump 全文,无摘要、无二进制守卫。
    - 对齐:omp [read.ts:2056-2192](../../oh-my-pi/packages/coding-agent/src/tools/read.ts#L2056-L2192)+[crates/pi-ast/src/summary.rs:108-160](../../oh-my-pi/crates/pi-ast/src/summary.rs#L108-L160)——**显式 selector(`:N`/`:N-M`)走 verbatim 绕过摘要,无 selector 才摘要**;BFS unfold 到可见行≥50(单次破100 跳过子树);**elision footer 必抄**(末尾给真实 selector 举例捞回正文,否则摘要=丢信息);非对称 padding leading=1/trailing=3。二进制守卫抄 [utils/binary.ts:29-37](../../oh-my-pi/packages/utils/src/binary.ts#L29-L37)(8192B sniff)。复用 P1.0 的 tree-sitter parser,Python ~30 行。
    - 目标阶段:**P1.4**(顺手,半天~1 天,ROI 高)。

23. **grep 升级:正则 + ignore + 二进制守卫 + FS 缓存(具体化 #1)** — `src/hyperion/platform/sandbox/local.py`。
    - 现状:#1(字面子串 grep、rglob 无忽略、无守卫)。
    - 对齐 omp 策略(Python 落地,Rust 内嵌不现实):① 二进制守卫(8192B sniff,最高优先);② ignore 用 `pathspec`(GitWildMatchPattern)+内建 skip(`.git` 永远剪枝);③ FS 扫描缓存([crates/pi-walker/src/cache.rs:16-366](../../oh-my-pi/crates/pi-walker/src/cache.rs#L16-L366),键=`(root,hidden,gitignore,skip,detail)`+TTL 1s+空结果 200ms 重检+写后按路径前缀失效+rename 双侧);④ 正则替代字面子串(`re`+brace sanitize)。**最大延迟收益来自 FS 缓存命中**。
    - 目标阶段:**P1.4**(二进制守卫当天;其余 1-3 天)。

24. **snapcompact 思路嫁接(序列化预算 + pre-compaction pruning;本体不做)** — `src/hyperion/platform/middleware/`(暂定)。
    - 现状:#3 的 SummarizationMiddleware 待加。
    - 对齐:omp [packages/snapcompact/](../../oh-my-pi/packages/snapcompact/)——**⚠️本体不是 LLM summarize,是把丢弃历史渲染成 PNG 喂视觉模型(零 LLM)**。v1 只抄:序列化预算契约(tool result 2000 字符/0.6 头比、tool call 参数 per-value 500/per-call 2000、useless 成对丢弃、Claude 设 `includeThinking=false` 防 reasoning 分类器)+ pre-compaction pruning(保护最近 40k、要求省 20k 才剪、**不剪 <50 token 结果**)+ Archive.text 重算防衰减。
    - 目标阶段:**P2**(序列化预算+剪枝+LLM summarize);本体渲染图**研究级,P6 再议(仅当主攻 vision 模型)**。

25. **记忆与持续学习(P3 主交付)** — 新增 `src/hyperion/services/memory/`。
    - 对齐:omp [packages/mnemopi/](../../oh-my-pi/packages/mnemopi/)+[hindsight/](../../oh-my-pi/packages/coding-agent/src/hindsight/)。① schema 抄 mnemopi [schema.ts:24-426](../../oh-my-pi/packages/mnemopi/src/core/beam/schema.ts#L24-L426)(SQLite+FTS5,字段 source/importance/confidence/veracity/valid_until/event_date/superseded_by);② 心智模型按轴分解([hindsight/seeds.json](../../oh-my-pi/packages/coding-agent/src/hindsight/seeds.json),create-only,拆 bug-patterns/module-architecture/failure-modes/team-conventions/regression-timeline);③ veracity 进 recall 排序([recall.ts:61-70](../../oh-my-pi/packages/mnemopi/src/core/beam/recall.ts#L61-L70));④ 软失效 superseded_by 不删;⑤ 分层降级 30/180 天([consolidate.ts:835](../../oh-my-pi/packages/mnemopi/src/core/beam/consolidate.ts#L835));⑥ polyphonic 4 路 RRF([polyphonic-recall.ts:85](../../oh-my-pi/packages/mnemopi/src/core/polyphonic-recall.ts#L85));⑦ retain 前 stripMemoryTags 防闭环+"repo state wins"。
    - **超越 omp**:溯源到 `file:line`/commit/日志片段(omp 只到 session_id);置信度闭环(被后续 bug 验证为真则上调,用 `validation_count`);跨项目"团队共享 bank"维度。
    - 目标阶段:**P3**。学术锚点:A-MEM / Learn to Memorize / Letta learning-sdk。

26. **PR scheme FS + 改动分析(P4 主交付)** — 新增 `src/hyperion/services/pr_tracker/scheme_fs.py`(暂定)。
    - 对齐:omp [internal-urls/](../../oh-my-pi/packages/coding-agent/src/internal-urls/)——`pr://`/`issue://` 当文件系统,read/grep 透明解析,模型只学一个工具;`pr://N/diff/<i>` 切片治大 PR 爆 context;SQLite 缓存(soft 5min/hard 7天+后台刷新)。Python `dict[scheme,Handler]`。扩展 `commit://`/`agent://`/`rule://`。
    - 改动分析借 [commit/agentic/](../../oh-my-pi/packages/coding-agent/src/commit/agentic/):`getFilePriority` 源码优先+30k 预算([git-file-diff.ts:60-77](../../oh-my-pi/packages/coding-agent/src/commit/agentic/tools/git-file-diff.ts#L60-L77));拓扑+环检测([topo-sort.ts:1-44](../../oh-my-pi/packages/coding-agent/src/commit/agentic/topo-sort.ts#L1-L44))反过来用=PR 改动归类(原子 vs 混合=风险信号);lock+sibling manifest 归组。
    - 目标阶段:**P4**(scheme FS 直接价值最高)。

27. **typed 子 agent fan-out(P5 主交付)** — 新增 `src/hyperion/workflows/deep_research/`(暂定)。
    - 对齐:omp [packages/coding-agent/src/task/](../../oh-my-pi/packages/coding-agent/src/task/)——子 agent 调 `yield` 提交结构化数据**不写散文**;schema 三级优先([structured-subagent.ts:173-185](../../oh-my-pi/packages/coding-agent/src/task/structured-subagent.ts#L173-L185));校验+重试三态 valid/invalid/unavailable([executor.ts:548-678](../../oh-my-pi/packages/coding-agent/src/task/executor.ts#L548-L678));增量 yield(findings 累积)+终结 yield;fan-out `mapWithConcurrencyLimitAllSettled`(不 fail-fast);`AgentOutputManager` 落 `<id>.jsonl`+嵌套命名=`agent://` 可寻址。**别照搬 worktree 隔离**(研究不并发改码),留隔离思想(空白历史+显式 context 契约)。
    - 落地 LangGraph:yield+schema+retry→conditional edge;fan-out 用 superstep+typed reducer+显式 partial-failure。
    - 目标阶段:**P5**。
