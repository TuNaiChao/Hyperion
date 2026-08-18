---
name: backlog-production-grade
description: "最小实现 → 对齐 deer-flow 生产级" 的补齐待办清单(跨阶段)
metadata:
  type: project
---

记录所有"当前为最小实现、后续须对齐 deer-flow 补到生产级"的点位。每条标注:位置、deer-flow 参照、目标阶段。原则见 [[align-to-deerflow-production-grade]]。

> **2026-08-14 pivot lens 复审结论**:用三件事(代码情报/记忆/skill+工具)+ 不编译铁律 + YAGNI 三标准过完全部条目。**10 条判 obsolete**:#3/#18/#20/#24/#27/#43/#47/#51/#54/#55(标 ~~obsolete~~ 在条目首)。**真待办分三档**:① 代码情报 9 条(活跃 MCP 工具,触发再做)② 评测 7 条(单机 indicative 够,要统计 tight 才做)③ 记忆/工具硬化 3 条(主路径但低优)。其余 15 条已覆盖或降级路径。~~唯一即时该做:#52(key 安全,跨机同步前必做)~~ —— **#52 2026-08-14 复核已达成**(实测 apiKey 已是 `{env:UNIONTECH_AI_API_KEY}`,2026-08-11 改好,摘要「待改」过时)。**无即时待办,全部触发式**。下次别再逐条念,看此复审结论 + 各条首部标记。

**当前清单:**

1. **`grep` / `glob`(LocalSandbox)** — `src/hyperion/platform/sandbox/local.py`。
   - 现状:字面子串 grep、`rglob` 无忽略清单、返回 `list`、无二进制/大文件/ReDoS/符号链接守卫。
   - 对齐:移植 deer-flow search.py(本机只读副本 deer-flow/backend/packages/harness/deerflow/sandbox/search.py) 整文件(`find_glob_matches` / `find_grep_matches` / `GrepMatch` + `IGNORE_PATTERNS` + `should_ignore_name` + `is_binary_file` + ReDoS 行长守卫 + 符号链接 `is_relative_to` 守卫);`local.py` 改成薄包装;返回改 `tuple[list, bool]`(截断标志);grep 支持正则/literal/case/glob 过滤。
   - 目标阶段:**P1**(代码理解服务,搜索是主力)。P0 demo 不直接暴露 grep/glob 给 agent,故可延后。

2. **`str_replace` 大文件截断风险** — `src/hyperion/tools/sandbox.py:str_replace_tool`。
   - 现状:经 `read_file`(50000 字符截断)读整文再写回;>50KB 文件会被截断标记污染、丢数据。
   - 对齐:deer-flow 的 read-before-write hash 门(读全文 + 内容哈希校验 + 同路径串行锁 `sandbox/file_operation_lock.py`)。
   - 目标阶段:**P2**(Bug-RCA 真实改补丁,文件可能较大)。P0 workspace 文件小,暂可接受。

3. **agent 中间件链(生产级)** — `src/hyperion/platform/agent.py:build_middlewares`。
   - 现状:`create_agent(..., middleware=[])` 裸跑,无任何中间件护栏。
   - 对齐:deer-flow 的中间件链(`deer-flow/backend/.../agents/lead_agent/agent.py:build_middlewares`,~30 项)。Hyperion 需要的子集:`ToolErrorHandlingMiddleware`(工具异常转 ToolMessage 不崩)、`ToolOutputBudgetMiddleware`(工具输出进上下文前的预算)、`ReadBeforeWriteMiddleware`(写前哈希门,配合 backlog 第 2 条)、`LoopDetectionMiddleware`(重复工具调用循环检测)、`TokenBudgetMiddleware`(per-run token 上限)、`SummarizationMiddleware`(上下文压缩)。
   - 目标阶段:**P2**(Bug-RCA 多步长链路需要这些护栏)起逐步加,按需逐个挂。

4. **索引构建原子性 + 状态清单(P1.2)** — 见 P1 报告 v2.2 §10(本机归档 docs-bak/设计/p1-code-understanding-design.md)。
   - 最小实现会裸写 LanceDB 表;生产级必须:**temp 表 `lancedb_tmp` + 全部成功后原子 rename**(中途崩不留半成品)+ 落盘 `index_manifest.json`(`repo_commit` / `model_fingerprint` / `schema_version` / `file_manifest`),检索前校验。增量更新用 `file_manifest` 文件级对账(非只看 mtime),处理重命名/移动防 chunk id 孤儿。
   - 目标阶段:**P1.2**(`index.py` 落地时直接做,别先裸写后补)。

5. **评测行级映射 + 难度分层(P1.3)** — 见 P1 报告 v2.2 §11(本机归档 docs-bak/设计/p1-code-understanding-design.md)。
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
   - 对齐:借鉴 deer-flow tiktoken 懒加载模式(prompt.py:190-260(本机只读副本 deer-flow/backend/packages/harness/deerflow/agents/memory/backends/deermem/deermem/core/prompt.py))—— 模块级缓存 + Lock、三态 sentinel(未加载 / 加载中 / 失败带时间戳)、加载中去重、**失败 600s 冷却自愈**(transient 网络故障免重启)、启动预热钩子(`warm()`,避免首请求被 ~1.2GB 下载阻塞)。模型下载体积远大于 tiktoken BPE,问题更严重。
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

16. ✅ **已成(2026-08-11)** precision 指标校准 — `eval/scorer.py` / `runner.py` / `tests/services/code_index/test_scorer.py`。
    - 现状(已成):落地 `scorer.precision_at_min_k = |top-k∩gold|/min(k,|gold|)`(R-precision 风格,单 gold 命中=1.0);runner 报 `rprecision@5`(旧 `precision@5` 列保留参考);退出标准 ② 改用 R-precision;11 单测绿(7 R-precision + 4 回归)。旧 P1.3 实测 precision@5=0.240(定义问题非缺陷)不再卡退出标准。**✅ 2026-08-17 实测已补**(改名后首跑,commit 8637e26 索引 747 chunk):**rprecision@5=0.833**(L1 符号直查 1.000 / L2 概念 0.700),同场旧 precision@5=0.222 正好复证"定义问题非缺陷";评测集 4 条死 gold(tools/sandbox.py / tools/registry.py 已删)先行替换为现行等价物(provider/LocalSandbox、mcp_memory build_server/_honest_truncate),18 条全有效零警告。L2 全部 4 条 miss 同一形态——**概念 query 命中正确文件的兄弟符号/模块块、粒度不匹配**(抽象基类 `Embedder.embed_chunks` 压过 `RemoteEmbedder`、`<module>` 块压过 `parse_file` 入口、内部 helper `_symbol_to_chunk` 压过 `chunk_file`);是「查询形态 boosting / 入口符号加权」类改进的现成触发数据点,文件级全对、符号级错位,非检索方向错。
    - **✅ 2026-08-18 触发数据点已兑现**(commit a801ac6):重排池扩满(top_n=top_k → 全候选;远端 rerank 本就全量打分,扩池零成本)+ **符号粒度先验**(module 0.65 / 私有·嵌套 0.80 / 公共入口 1.0,`retrieval._granularity_prior`,extra['rerank_score'] 留原始分,apply_prior=False 消融开关)。干净索引(758 chunk,正确根 src/rootrecall)实测:**L1 mrr 0.854→1.000、ndcg 0.891→1.000**(rprecision 保持 1.000);L2 gold 位次大幅前移(chunk_file 21→7、parse_file 24→6,module/私有压头全消)但 **top-line 持平 0.700** —— 深挖定性:剩余 miss 是**另一失败模式**,①同域公共符号 cross-encoder 平局(如 LanguageGrammar 0.71 vs parse_file 0.49,分差 0.003~0.03,靠调乘数硬掰=过拟合 10 条查询,拒绝);②parse_repo 不在 hybrid 召回 50 池(召回侧,candidate_top_n=100 试验无 top-5 变化且引新噪声,不采纳)。若未来再抬 L2:方向是 rerank 文档表示(结构卡替代 fts_text 词袋)或更强 reranker,不是调先验乘数。

(后续每发现一处最小实现就追加一条。)

---

## 借鉴 oh-my-pi(omp)的新能力项

> 来源:2026-07-27 深读 omp(can1357/oh-my-pi)+ 2026 最佳实践,详见 docs/调研/后续设计演进报告-oh-my-pi与最佳实践.md(本机归档 docs-bak/调研/后续设计演进报告-oh-my-pi与最佳实践.md)。下列每条标注:位置 / omp 参照(带行号)/ 目标阶段。原则同上:可最小起步,但须排期到生产级。与上文 #1–#16 有重叠处已标"升级 #N"。

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

22. ✅ **已成(P1.4, 2026-07-28)** read = tree-sitter BFS 摘要 + elision footer + 二进制守卫 — 落地 `tools/sandbox.py:read_file_tool` + `services/code_index/outline.py` + `platform/sandbox/_search.py:is_probably_binary`(6/6 验证绿)。语句级折叠→#28;:raw/skip→#30。
    - 现状(已成):read 直 dump 全文,无摘要、无二进制守卫。
    - 对齐:omp [read.ts:2056-2192](../../oh-my-pi/packages/coding-agent/src/tools/read.ts#L2056-L2192)+[crates/pi-ast/src/summary.rs:108-160](../../oh-my-pi/crates/pi-ast/src/summary.rs#L108-L160)——**显式 selector(`:N`/`:N-M`)走 verbatim 绕过摘要,无 selector 才摘要**;BFS unfold 到可见行≥50(单次破100 跳过子树);**elision footer 必抄**(末尾给真实 selector 举例捞回正文,否则摘要=丢信息);非对称 padding leading=1/trailing=3。二进制守卫抄 [utils/binary.ts:29-37](../../oh-my-pi/packages/utils/src/binary.ts#L29-L37)(8192B sniff)。复用 P1.0 的 tree-sitter parser,Python ~30 行。
    - 目标阶段:**P1.4**(顺手,半天~1 天,ROI 高)。

23. ✅ **已成(P1.4, 2026-07-28)** grep 升级:正则 + ignore + 二进制守卫 — 落地 `platform/sandbox/_search.py`(内核)+ `tools/sandbox.py:grep_tool` + `local.py:grep()`。**FS 扫描缓存 + .gitignore 解析仍未做** → #30 / #1。
    - 现状(已成):#1(字面子串 grep、rglob 无忽略、无守卫)。
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

## P1.4 落地后遗留的生产级补齐项(#28–#30)

> 来源:2026-07-28 P1.4 实现时,对照 omp 边界处理识别出的、当期有意简化的点。详见 docs/设计/p1-code-understanding-design.md §4.11(本机归档 docs-bak/设计/p1-code-understanding-design.md)。

28. **ast-grep 式结构化 AST 搜索(替 grep_symbol 的名匹配)** — `tools/code_nav.py` 或新 `tools/ast_search.py`。
    - 现状:P1.4 的 `grep_symbol` 只做名/子串匹配;模型要"找所有 `foo(bar)` 形态的调用"得自己揉正则,碰宏/重载易漏。
    - 对齐:omp [ast-grep.ts](../../oh-my-pi/packages/coding-agent/src/tools/ast-grep.ts)+[crates/pi-ast/](../../oh-my-pi/crates/pi-ast/)——AST 模式 + 元变量(`$NAME`/`$$$NAME`),6 档 strictness 含 `signature`;parse 错误=查询失败而非无匹配。
    - Python 落地:tree-sitter 的 `Language.query` 模式匹配(零新依赖)优先;或绑 `ast-grep` CLI(Rust)。
    - 目标阶段:**P2+**(P1.4 名匹配够用;C 宏/函数指针多时再上)。

29. **model 文本 vs display 双轨(给 LLM 的可被工具反向解析)** — `tools/`(read/grep 输出)+ P2 Hashline 一起做。
    - 现状:P1.4 工具输出只有一路文本(给人看=给模型看)。
    - 对齐:omp [grouped-file-output.ts](../../oh-my-pi/packages/coding-agent/src/tools/grouped-file-output.ts)+[match-line-format.ts](../../oh-my-pi/packages/coding-agent/src/tools/match-line-format.ts)——给 LLM 的 model 文本用可解析形状(`LINE:content`/`*LINE:content`),给人看的 display 走独立 gutter 渲染。
    - 目标阶段:**P2**(和 Hashline 行锚一起;锚定编辑依赖可解析行格式)。

30. **read `:raw` 逃生舱 + grep 结果分页 `skip` + FS 扫描缓存** — `tools/sandbox.py` + `platform/sandbox/_search.py`。
    - 现状:P1.4 二进制文件直接拒(指向 bash);grep 截断只给"收窄"提示,不能翻页;无 FS 缓存。
    - 对齐:omp read 的 `:raw` selector(绕过守卫读原始字节,[read.ts:2511](../../oh-my-pi/packages/coding-agent/src/tools/read.ts#L2511))+ grep 的 `skip` 文件级分页([grep.ts:1319](../../oh-my-pi/packages/coding-agent/src/tools/grep.ts#L1319))+ walker FS 缓存([pi-walker/cache.rs](../../oh-my-pi/crates/pi-walker/src/cache.rs),TTL 1s+空结果 200ms 重检+写后失效)。另含 #1 的 .gitignore 解析(`pathspec`)。
    - 目标阶段:**P1.4 补丁**(半天;二进制场景多/大仓慢时再做)。

## P1.5 落地后遗留的生产级补齐项(#31–#37)

> 来源:2026-07-28 P1.5 实现 L2 精确导航(clangd/multilspy)时,对照 multilspy 源码 + clangd FAQ + 调研报告识别出的、当期有意简化的点。详见 docs/设计/p1-code-understanding-design.md §5.12(本机归档 docs-bak/设计/p1-code-understanding-design.md)。**P1.5 主体已成**(fixture 实测 find_references 零漏召)。

31. **get_callees 聚合** — `tools/code_nav.py`。
    - 现状:P1.5 只做 callers(`find_references`);callee(这个函数调了谁)没做。
    - 对齐:对定义体里每个调用点逐一 `goto_definition` → 聚合成 callee 列表;或直接用 #32 的 callHierarchy/outgoingCalls。
    - 目标阶段:**P2 Bug-RCA**(串调用链双向都要时)。

32. **严格 caller/callee 树(LSP callHierarchy)** — `services/code_index/lsp.py`。
    - 现状:P1.5 用 `textDocument/references` 当 callers——对 C 函数够,但会混入"取地址/赋值"等非调用点;multilspy 不暴露 `prepareCallHierarchy`/`incomingCalls`/`outgoingCalls`。
    - 对齐:绕过 multilspy 直接 `self.server.send.call_hierarchy_incoming(...)`(raw JSON-RPC);或等 multilspy main 合入后升版。
    - 目标阶段:**P2**(C 函数指针/回调多、references 噪声大时)。

33. **索引就绪信号(替固定 300ms 重试)** — `services/code_index/lsp.py` `ClangdServer.start_server`。
    - 现状:P1.5 用固定 `index_retry=1` + 300ms 兜冷启动;不准(大仓不够、小仓浪费)。
    - 对齐:抓 clangd 的 `$/progress`(indexing N/M,M==N 即就绪)或 `experimental/serverStatus` 的 `quiescent==true`(main 的 ClangdLanguageServer 就等这个),起 server 时异步等、查询时按需等。
    - 目标阶段:**P2 真实 C 仓**(bluez/systemd,首次索引秒~分钟级,固定重试不够)。

34. **references 渲染给 LLM 的截断策略** — `tools/code_nav.py` `_render_locations`。
    - 现状:P1.5 只做 `(file,line)` 去重 + max_results 截断;大仓 references 上百条会爆上下文。
    - 对齐:按文件分组(每文件首条 + `N more in this file`)+ 每条带 **caller 函数名**(反查 reference 所属函数)+ Top-N(10–20)+ `N more omitted, depth=2 展开`。别 dump 原始 JSON。
    - 目标阶段:**P2**(真实仓高频符号如 `g_dbus_proxy_new` 引用 >100)。

35. **多语言 LSP(rust/python/go)** — `services/code_index/lsp.py` `get_lsp_server`。
    - 现状:P1.5 写死 clangd(C/C++);Hyperion 自身是 Python,LSP 导航用不上 clangd。
    - 对齐:multilspy 自带 python(rust-analyzer 也在 `main`)等 adapter;按 repo 语言分发(检测 majority language 或 config 声明)。
    - 目标阶段:**P5 Deep-Research**(被研仓库非 C 时)。

36. **大仓离线索引(SCIP)+ .cache 持久化 + `.clangd` 跳大目录** — `services/code_index/lsp.py` + 运维。
    - 现状:P1.5 实时 clangd;systemd/kernel 量级首次索引数分钟~小时、内存膨胀。
    - 对齐:[scip-clang](https://github.com/sourcegraph/scip-clang) 离线把全仓索引成 SCIP 再查(Sourcegraph Cody 路线);`.cache/clangd/index/` 持久化(随仓分发 / Docker volume);`.clangd` 配 `Index: Background: Skip` 跳 test//vendor/。
    - 目标阶段:**P2/P6 生产化**(上 systemd/kernel 量级目标仓时)。

37. **compiledb 优选(autotools)+ 交叉编译 `--query-driver`** — `scripts/`(bluez/wpa 落地时)。
    - 现状:P1.5 fixture 手写 compile_commands;setup.sh 装了 bear+compiledb 但没用过真实 autotools 仓。
    - 对齐:autotools 用 `compiledb --parse make -nW V=1`(解析 dry-run,不受 LD_PRELOAD/SELinux/CCACHE 干扰)比 `bear -- make V=1` 稳(bear 4.0.x 有空 JSON bug #660/#656);交叉编译(bluez arm/wpa)加 `--query-driver=/usr/bin/arm-linux-*` + `.clangd` `CompileFlags.Compiler`。`CodeChunk.callers/callees` 空字段清理(确认无害后删)。
    - 目标阶段:**P2 首个真实 C 仓**(bluez/wpa build 环境就绪时)。

## v2 产品重规划后的新借鉴项(#38–#44)

> 来源:2026-07-28 产品重规划(编排 + 记忆 + 委托),高星参考项目调研。**阶段标注从 P→R**(R0 规划→R1 记忆→R2 bug-RCA MVP→R3 深度调研→R4 团队/PR→R5 生产化);上列 #1–#37 的技术债仍有效,只是阶段标签按新路线对齐(原 P2≈现 R2,原 P3≈现 R1 记忆,原 P5≈现 R3)。详见 [[agent-project-overview]] + architecture.md §10(本机归档 docs-bak/设计/architecture.md)。

38. **Aider repo-map(P1 调研最高杠杆单点借鉴)** — ✅**已成(2026-08-11)**,第 12 个 MCP 工具 `repo_map`。
    - 对齐:[Aider-AI/aider](https://github.com/Aider-AI/aider) `aider/repomap.py` + `queries/<lang>/tags.scm`(~48k,Apache-2.0)。tree-sitter `tags.scm` 抽 defs+refs → networkx 建符号引用图 → **PageRank** → 按 token 预算(`map_tokens`)裁剪成"全仓最重要符号"地图。
    - **实际落地(偏离原文,B 路线)**:**不新建 `repomap.py`、不抄 `tags.scm`** —— 精读发现 CRG 已对 C/Python 抽好 CALLS 边、Hyperion 已有 `_pagerank`(route2)。改 `CodeGraph.repo_map()`(code_graph.py):整图 CALLS 子图 → `_pagerank`(全局,非种子邻域)→ 降序贪心填 token 预算 → 按文件分组树。零新依赖、零 tags.scm 维护、和 blast_radius/call_chain 一致(都吃 CRG 图)。理由:重抄 tags.scm = 重复造边抽取,撞踩坑#2「别重造」。
    - **探针实证(wpa 真图)**:149 符号 / 69 文件 塞进 2048 token预算;top = wpa_cli_cmd/wpa_ctrl_command/send_and_recv_msgs(结构核心,合理)。修了两个真 bug:① CRG 存绝对路径 → 渲染剥公共前缀 + 符号行去路径前缀(否则全图被 abs path 淹没);② token 估算须用「显示名」非全长 qn(否则虚高 2.5x 早停,67→149 符号)。
    - **backlog**:函数签名富化(parse_repo 拿 signature 渲 `def foo(args)` 骨架,要解 CRG `::` ↔ parser `.` 格式匹配)/ CLI 子命令(对齐 call_chain 纯 MCP)/ Aider 式「每符号标谁引用它」。
    - 目标阶段:**R3**(代码仓深度调研;v0.1 标"延后",v2 因 P1 调研支柱提前)。详见 [[route5-repomap-handoff]]。

39. **Agentless 分层定位漏斗(bug-RCA 委托前的确定性预筛)** — `src/hyperion/workflows/bug_rca/` localize 步。
    - 对齐:[openautocoder/agentless](https://github.com/openautocoder/agentless)(~2.1k,MIT)——无 agent 循环的 localize→repair→validate,~$0.34/issue。localize 是**分层**:file→class→function→line,每级 embedding 相似 + LLM rerank。
    - 落地:直接建在已有 `code_index` 上(语义检索 + BM25 + chunker 符号边界),产 `[(file,function,line,why)]` 锚点 + code-review-graph blast-radius → 喂委托前的 assemble。**不让 delegate 在整库自由探索**(省 token、可控)。
    - 目标阶段:**R2**(bug-RCA MVP,步骤 2-3)。

40. **mini-swe-agent ACI 工具契约(delegate 工具面规范)** — `src/hyperion/tools/delegate.py` + 提示词。
    - 对齐:[SWE-agent/mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent)(MIT,活跃维护的最小化重写版;原 swe-agent 已停维护)的 ACI(Agent-Computer Interface,概念源自 SWE-agent NeurIPS 2024):`find_file/search_dir/search_file/edit` + 输出上限(100 行窗口/100 匹配 cap)+ 编辑守卫(auto-indent/mismatch 警告/post-edit diff)。消融:这些 search 命令是定位成功最大贡献因子。
    - 落地:Hyperion 给 delegate 的提示词里申明这套工具契约(文档 `docs/background/aci.md`)。
    - 目标阶段:**R2**。

41. **OpenHands 3 层记忆架构(native 后端参考)** — `src/hyperion/services/memory/`。
    - 对齐:[OpenHands/openhands](https://github.com/OpenHands/openhands)(~82k,MIT)`openhands/memory/`:`Condenser → View → ConversationMemory` 三层 + EventStream append-log(`CondensationEvent`)+ `.openhands/microagents/` 仓库级知识(关键词+符号触发)。
    - 落地:Hyperion `MemoryService` native 后端的"工作/情景/语义记忆"分层 + 仓库级知识文件机制参考它。v1 先做语义(`CodebaseFact`/`BugLesson`),工作/情景随 workflow state。
    - 目标阶段:**R1**(记忆核心,架构参考)。

42. **graphiti bi-temporal(记忆时序维度)** — `src/hyperion/services/memory/schema.py`。
    - 对齐:[getzep/graphiti](https://github.com/getzep/graphiti)(Apache-2.0):bi-temporal 知识图(event-time + ingestion-time),`valid_at/expired_at/invalid_at`,能答"这 bug 报时我们对这模块知道啥"。
    - 落地:**先 schema 留位**(`KnowledgeItem.valid_at` 已留),完整实现(含图引擎)后补;需要时也可作 MemoryService 可换后端。
    - 目标阶段:**R1 起步(schema),R5 完整**。

43. **委托后端多档(omp/opencode/claude)+ 无头参数实测** — `src/hyperion/tools/delegate.py`。
    - 现状:仅设计(抽象 `CodingAgentDelegate`,v1 默认 omp)。
    - 对齐/待办:实测 omp `omp -p` 与 `--mode rpc`(NDJSON 流式)、opencode `opencode run` 与 `serve`(HTTP)、claude `-p`/SDK 的精确无头参数与结构化产出契约;锁定 R2 的 JSON schema 字段。URL 已核实(mini-swe-agent→`SWE-agent/mini-swe-agent`(swe-agent 已停维护),agentless→`openautocoder/agentless`)。
    - 目标阶段:**R2**(委托落地时)。

44. **C/RCA 论文必读(无代码,eval 方法论)** — 调研参考。
    - **T2L-Agent**(arXiv 2510.02389):runtime-trace-guided 漏洞定位 module→file→line,**直接在 bluez & wpa_supplicant 上评估**——正是 Hyperion 目标。提炼其 trace 解析 + 模块收敛作为 eval 方法论。
    - **Code Researcher**(arXiv 2506.11060,MSR):deep-research agent 对 C/C++ 系统代码做多步推理 + **commit-log 因果推理**,48% crash 解决率。提炼 commit-log 作 RCA 先验 + 假设-证据报告结构。
    - 目标阶段:**R2/R3**(bug-RCA 与深度调研的 eval/方法论参考;无代码,读论文)。

## ★ runtime harness 自建(2026-07-29 决策修正,系统项)

45. **agent 运行时上下文管理 harness** — 新增 `src/hyperion/platform/runtime/`。
    - 决策来源(用户 2026-07-29):「Hyperion 要有 deer-flow 同等的运行时上下文管理,自己能跑长 agent;但 coding 能力仍委托 opencode/omp」。**边界**:coding 动作(读写/命令/补丁)→ 委托;agent 运行时(压历史/token 预算/截工具输出/并行子任务/断点续跑)→ **Hyperion 自建**。深度调研(R3)是 Hyperion 自己的长 agent、没法委托、上下文必爆,故 runtime 必须自建。
    - 对标:① deer-flow `backend/packages/harness/deerflow/` —— **不自造中间件 ABC**,直接继承 `langchain.agents.middleware.AgentMiddleware[State]`,override `before_model/after_model/wrap_model_call/wrap_tool_call`;`SummarizationMiddleware`(LLM 摘要+`REMOVE_ALL_MESSAGES`+独立 `summary_text` channel+多模型 fallback)、`TokenBudgetMiddleware`(累加 diff+warn+hard_stop 剥 tool_calls+BoundedDict)、`ToolOutputBudgetMiddleware`+`tool_output_synopsis`(阈值外化磁盘+纯函数 synopsis+5MB DoS 守门)、`SubagentExecutor`(持久 isolated loop+`SubagentResult.try_set_terminal` 锁+`MAX_CONCURRENT=3`);复用 LangGraph `SqliteSaver` checkpointer;不自造 ReAct,直接 `create_agent`。② OpenHands `Condenser→View→ConversationMemory` 3 层(R5 双层记忆参考)。
    - 完整对标(带文件:行号):deer-flow-runtime-参考.md(本机归档 docs-bak/调研/deer-flow-runtime-参考.md) + 设计 runtime-harness-design.md(本机归档 docs-bak/设计/runtime-harness-design.md)。
    - 分档:**R3 开场**搭最小骨架 5 件(`create_hyperion_agent` factory + `TokenBudgetMiddleware` 移植 + `ToolOutputBudget`+synopsis 整文件搬 + `HyperionState` schema + `SqliteSaver` checkpointer)—— runtime 的真实场景是 R3 深度调研(跑长 agent),R2 bug-RCA 七步不依赖、不验,故从 R2 末挪到 R3 开场边搭边验(用户 2026-07-30 决策);**R3 中**深度调研上场(Summarization + LoopDetection + DynamicContext + SubagentExecutor 并行查多模块);**R5** 生产化(checkpoint_patches 模式 + OpenHands 双层记忆 + 跨进程 sandbox ownership + alembic hybrid bootstrap)。
    - 与现有项关系:**吸收** #3(中间件链)、#20(TTSR)、#24(snapcompact 序列化预算)为 runtime 子能力;#43 委托的 ToolOutputBudget 是委托前置(omp 大输出召回前截断)。
    - 目标阶段:**R3开场骨架 → R3中上场 → R5补齐**(2026-07-30 从 R2 末挪到 R3:R2 不依赖/不验,边搭边验更踏实)。

46. **bug-RCA 报告渲染精修(对齐 demo2 金标准完整骨架)** — `src/hyperion/workflows/bug_rca/report.py`。
    - 现状(R2 MVP):简化骨架(元数据表 → TL;DR → 线索 → 定位与根因(trigger_chain+evidence)→ 补丁 → 附录)。证据纪律已是签名(evidence 锚 file:line)。
    - 对齐:demo2 金标准 271 行完整骨架 —— 补:环境与影响表(组件/驱动/单 PHY 约束)、故障现象表、**日志时间线表**(时刻→日志行号→事件→含义,日志驱动必备)、修复方案对比表(minimal/medium/v4)、**补丁分析表**(正确性/TOCTOU/覆盖率/兼容性)、验证(编译/apply-revert/**日志回放覆盖率%**)、风险评估与选型、复现&回归用例、附录(代码位置表/日志行号表/术语表/交付清单)。
    - 用户定(2026-07-29):报告格式"后续再讨论",先按简化骨架跑通,按用验调。
    - 目标阶段:**R2 后 / R3**(金标准对照达标后精修)。

47. **bug-RCA workflow R5 编排增强(并行/分支/循环)** — `src/hyperion/workflows/bug_rca/graph.py`。
    - 现状(R2):线性七步 StateGraph。**调研定稿(2026-07-29):R2 线性正确** —— Agentless 基线背书(线性拿 SWE-bench Lite 32%/$0.70)、deer-flow ReAct 不适用专用流水线、LangGraph "start simple"、delegate 节点已嵌 ReAct(opencode)。
    - R5 四增强(蓝图见 workflow-orchestration-参考.md(本机归档 docs-bak/调研/workflow-orchestration-参考.md) §4.2):
      (a) **superstep 并行** recall ∥ localize_pre(localize 拆粗+细后,任务数固定用 superstep 非 Send);
      (b) **localize T2L 式 refinement 循环**(`add_conditional_edges` 自指,`MAX_LOCALIZE_ITER=3`,对齐 T2L arXiv 2510.02389 evidence-guided);
      (c) **delegate multi-candidate**(`Send` 动态 fan-out N 候补丁 + `vote` 投票,N 运行时由 assemble 决定);
      (d) **verify 失败条件分支**(`Command(update={verify_feedback})` 回 assemble/localize,`MAX_VERIFY_RETRY`,产 `failure_type` 字段)。
    - 前提:localize/verify 节点本身成熟(localize 多轮、verify 真跑编译/测试)后再加;**每加一条分支/循环先加测试**。
    - 排雷:不要外层换 ReAct lead、R2 不引入 interrupt、delegate 内部不用 Send(multi-candidate 是 R5 外层的事)、不混用 create_agent 与手写 StateGraph。
    - 目标阶段:**R5**(生产化)。

48. **反向 MCP(delegate 经 opencode 主动查 Hyperion 记忆)** — opencode 配置 + `nodes.py`。
    - 现状(R2):通路① **assemble 注入 recalled**(已实现,`nodes.py` 的 `node_assemble` 把 recall 结果塞进 delegate prompt,delegate 被动看到记忆)——「记忆→委托」闭环已通。
    - 待接:通路② **opencode 主动查** —— `opencode mcp add hyperion -- uv run hyperion mcp serve`(opencode 原生 MCP client 挂 R1 的 hyperion mcp serve,stdio)+ assemble prompt 提示 `memory_recall` 工具。delegate 干活时按需查更多(通路① 是 Hyperion 一次查,通路② 是 delegate 按需多次查)。
    - 用户定(2026-07-29):R2 不接(通路① 够),留 R2 末 / R3。
    - 目标阶段:**R2 末 / R3**。

## ★ workspace(R3 落地,2026-07-29 定稿)

49. **bug workspace 模块(每 bug 一个专用目录七段)** — 新增 `src/hyperion/services/workspace/`。
    - 决策(用户 2026-07-29 认可):bug-RCA 演进为「每 bug 一个 workspace 目录」(`<repo>__<bug-id>__<hash6>/`,七段:code/triggers/delegate/artifacts/patch/report/docs),opencode `--dir` 指此,**读全量代码 + 日志**(非内联片段)。解 R2 内联三痛点(opencode 被动/补丁易错位/日志没法结合)+ 补丁能 quilt apply。
    - 对标:deer-flow per-thread per-user sandbox + Agentless 多候选 + SWE-bench 每实例一容器。
    - 复用 deer-flow:`Sandbox`/`SandboxProvider` ABC + `LocalSandbox`(path mapping+pipe drain+进程组 timeout)+ `env_policy`(scrub key)+ `workspace_changes/{scanner,diff}.py`(前后扫描生成 unified diff)。
    - 隔离:默认本地目录(R2/R3),Docker 作 R5 可选(`AioSandboxProvider`)。Hyperion 场景(本地优先/一人/代码非完全不可信)本地够。
    - 完整设计:workspace-design.md(本机归档 docs-bak/设计/workspace-design.md)。
    - 分档:**R2 末**最简形态(workspace + AGENTS.md 契约 + 方式B 指引,delegate cwd=workspace,可能同时解 delegate timeout);**R3** 完整七段 + candidate_patches + validate + LocalSandbox;**R5** Docker(AioSandbox + warm pool + 多架构镜像)。
    - 目标阶段:**R2末最简 → R3完整 → R5 Docker**。

50. **大日志分层预筛(journalctl/btmon 几 MB~GB)** — 新增 `src/hyperion/services/log_preprocess/`(R3)。
    - 痛点:大日志不能全喂 opencode(爆 token);opencode 自己 grep 多轮也烧 token。
    - 分层(和代码 localize 同模式):**Hyperion 粗筛**(确定性 grep 关键字 panic/OOPS/BUG/Warning/错误码/trigger 符号 + 故障时间窗 + addr2line 符号化 + 堆栈折叠 + LLM 摘要 → 写 `delegate/context.md`)+ **opencode 深挖**(拿预筛关键行后用 grep/read 按需查原始日志)。
    - 复用:原 v0.1 `log_symbolizer`(addr2line/btmon)思路;符号化部分可委托 omp 或系统 addr2line。
    - AGENTS.md 提示「已预筛在 context.md,可自行 grep 深挖」。
    - 目标阶段:**R3**(workspace 完整落地时)。

51. **补丁可 apply 验证 6 步(SWE-bench/Agentless 标准)** — `src/hyperion/services/workspace/validate.py`(R3)。
    - 6 步:clean checkout(`git checkout base && git clean -xfd`)→ `git apply --check`(失败降级 --3way/`patch -p1`)→ revert 验证(应 fail)→ 编译 → FAIL_TO_PASS/PASS_TO_PASS 测试回归 → ~~多候选 rerank(Agentless)~~(已于 2026-07-31 移除,见 bug-rca-design.md §7.6;有 oracle 再评估)。
    - quilt 场景:`final.diff` → `debian/patches/fix-N.diff` + 更新 `series` + `quilt push -a`。
    - diff 生成:复用 deer-flow `workspace_changes`(观察 code/ 前后改动 + `difflib.unified_diff`),不依赖 opencode 吐格式正确的 diff。
    - 现状(R2):只 `bool(patch)` 非空检查(`nodes.py` node_verify);R3 补完整 6 步。
    - 目标阶段:**R3**。

52. **✅ 已完成(opencode.json key 安全/env 替换,2026-08-11 实测达成)** — `~/.config/opencode/opencode.json`。
    - **2026-08-14 复核实证**(脱敏探针,踩坑#20:不轻信交接摘要口头状态):`provider.uniontech-ai.options.apiKey` 当前值 = `'{env:UNIONTECH_AI_API_KEY}'`(**已是 env 引用,非明文**,len=26);全文件唯一 key 字段,无明文残留;项目内 `opencode.json`+`config/opencode_hyperion.json` 明文 apiKey 行数=0;`UNIONTECH_AI_API_KEY` 在 `~/.bashrc` export,`bash -ic 'env'` 验子进程 `CHILD_INHERITS ✓`(opencode 启动能读到);`~/.config/opencode` 不在任何 git 仓。**文件 mtime 2026-08-11 16:05 → 当时已改好**(摘要写「待改」是过时信息)。
    - ~~原条目(2026-07-29 记)~~:明文存 uniontech-ai apiKey;改 `{env:...}`;key 放 `.bashrc`/`.env`。**现已达成**。
    - 目标阶段:**即时**(跨机同步前)——**已达成,关闭**。

53. **问题描述多格式解析 + 关键字抽取(预筛源头)** — 新增 `src/hyperion/services/trigger_parser/`(R3)。
    - 输入:`triggers/issue.{md,txt,pdf}` 或直接 prompt(cli/API)。txt/md 直读;**PDF 用 pypdf/pdfplumber**(demo1 是 PDF 漏洞报告驱动);统一写 `triggers/issue.md`。
    - 关键字抽取(→ `triggers/keywords.json`):规则(错误码/函数名 `[a-z_]+\(\)`/文件路径/内核符号/panic·OOPS 正则)+ LLM 抽(role=locator,「涉及哪些符号/错误码/症状/模块」)。
    - **关键字统一驱动**:① 日志预筛(grep,见 #50)② 代码 localize file-level 检索(方案A,BM25/embedding,替代喂全树 518 文件)。
    - 关键字是 trigger_parser + log_preprocess + localize 的统一纽带 —— 问题描述是 bug-RCA 起点 + 关键字源头。
    - 现状(R2):trigger 是 cli `--trigger` 预摘要字符串,无文件解析/关键字抽取。
    - 目标阶段:**R3**(workspace + log_preprocess 落地时)。

## ★ 多阶段委托(2026-07-30 决策,解 glm-5.2 单 loop 不收敛)

54. **delegate 拆多阶段(localize → repair → verify → 可选 review)** — `nodes.py` + `delegate.py`。
    - 痛点:R2 单次 delegate(opencode 单 agent loop,定位+补丁+报告一次产)→ glm-5.2 跑 97K token 全工具调用,最后 prose「让我阅读...」**不收敛**,无 JSON 产出。= SWE-agent 单 loop 失败形态。
    - 调研铁证(Agentless arXiv 2407.01489):同模型 GPT-4o,**分阶段 32%/$0.70/78K token** vs **单 loop 18.3%/$2.53/498K** —— 分阶段质量/成本/token 三项全胜(不是「贵但稳」,是「又便宜又稳又准」)。消融:**skeleton(698 行,58% 命中)完胜整文件(778 行,53.7%)** = lost-in-the-middle;glm-5.2 内联大片代码过载是不收敛根因。
    - 设计(对齐 Agentless 三阶段 + MASAI 子 agent 元组):
      ① `localize_delegate`(有工具,只定位 root_cause/evidence,**禁补丁**)→ JSON;
      ② `repair_delegate`(根因已锁,只改局部,采 N 候选)→ patch;
      ③ `verify`(Hyperion 自跑,无 LLM:Tier 0 `git apply --check`/编译/apply-revert + Tier 1 repro test rerank);
      ④ `review_delegate`(可选,Tier 2 跨家族对抗审,reviewer 先判 intervene 防重写退化);
      ⑤ report+memorize。阶段间走文件(workspace delegate/artifacts,MASAI「不对话只 input/output 串接」)。
    - 验证分层(路 2 调研):执行信号(repro test F2P)是唯一硬信号(MASAI 证 LLM 单独选不准 patch);LLM judge 弱(偏 gold-like + SWE-bench 7.8% overfit);对抗审 cross-model 数据:reviewer ≥ writer 才涨点(Codex 自审 +12.9pp、Claude 自审 +0、弱审强 **-8.6pp 退化**)。
    - `CodingAgentDelegate` 接口不用改(`run` 调多次,每次不同 schema),符合三锁定决策 #2。
    - 完整设计:bug-rca-design.md §多阶段委托。
    - 分档:**R2 收尾**拆 `node_delegate` → localize + repair 两阶段 + verify Tier 0(tolerant apply);**R3.1** 改迭代 verify-refine(B)(同会话双循环);多候选采样投票 + rerank 已于 **2026-07-31 整体移除**(无 oracle 时平凡白烧 token,见 bug-rca-design.md §7.6);**R5** 加跨模型对抗审 + 2 轮反馈循环 + 退化熔断(有 oracle 再评估 filter+vote)。
    - 目标阶段:**R2收尾两阶段 → R3.1 verify-refine(B) → R5对抗审**。

55. ~~**opencode serve persistent(③,session 续接 + 免 MCP cold boot)**~~ — ⚠️ **2026-08-07 pivot 后 obsolete**(理由见本条末尾)。原计划:`delegate.py` 新 `OpenCodeServeDelegate` 后端。
    - 现状(R3.1):每次 `delegate.run` 新起 `opencode run` 子进程,`--continue` 续最近 session;**每次重拉 `hyperion mcp serve` 子进程 = MCP cold boot**(import hyperion + 加载 sentence-transformers ~1.2GB),bug_rca 一条 bug K1+K2≈4 次 delegate = 4× MCP 冷启。正确性不受影响(e2e GREEN),③ 是**纯性能优化**。
    - **2026-08-06 agent 查实(完整结论见记忆 [[opencode-serve-persistent-research]])**:
      ① `-s/--session <id>` 精确续**早就有**(本机 v1.18.11 实测)—— 但单 bug 单 cwd 流程里已够准,替换近乎零增量,**不是 ③ 的价值点**。
      ② `opencode serve`+`run --attach`/REST 是官方"免 MCP cold boot"路径(官方 CLI 文档原话)。REST 表面:`POST /session` 建→`.id`;反复 `POST /session/{id}/message`(body `{model?,agent?,parts}`→`{info,parts}`);异步 `POST /session/{id}/prompt_async`+`GET /event` SSE;`POST /instance/dispose` 释放;`GET /global/health` 健康检查。鉴权 `OPENCODE_SERVER_PASSWORD`(HTTP basic)。**端口 `--help` 写 0/随机 vs docs 写 4096,有出入 → 显式 `--port`**。
      ③ 生产先例 Critique(E2B 沙箱起 serve + `POST /session/{id}/message` 多轮,正是 Hyperion 场景):**必抄故障回退**(session aged out→返 conflict→降级新 session,宁可起新也不静默 corrupt repo)+ idle/running/completed 状态机 + endSession 主动释放。并发:串行 follow-up,多 bug=多 session.id,别并发写同一 session。
      ④ Python 客户端:**httpx 直连 REST**(端点十几个、全 JSON),不引社区 SDK(官方只背书 JS/TS `@opencode-ai/sdk`)。**别直接读写 opencode 私有 SQLite/storage**(跨版本会变,只走 HTTP 表面)。
    - 实施形态:新 `OpenCodeServeDelegate(CodingAgentDelegate)`(delegate.py 核心,窗口展示区)= 长驻 serve 生命周期(起/健康检查/停)+ httpx 客户端 + session.id 记账(per-bug workspace→一个 session)+ `POST /message`→解析 `{parts}` 拿 assistant 文本→复用现有 `_extract_json` 抠 schema + **失效降级**(serve 挂→降级回 `OpencodeDelegate` 子进程模式);配置 `delegate.backend: opencode_serve`。
    - **归属:单独一轮**(用户 2026-08-06 拍板:本轮先做 P0 记忆闭环 ②[a]+②[b],③ 拆出)。目标阶段:**R3 收尾后单独一轮**(③ 本身)+ **R5**(multi-agent attach / 跨机 Tailscale/mdns)。
    - **⚠️ 2026-08-07 pivot 后 obsolete**:harness 转向(opencode 主驱动 + Hyperion 当 MCP server)后不再需要。① 原痛点(delegate 反复 cold-boot opencode,K1+K2≈4×/bug)前提消失 —— 只存在于已 deprecate 的 legacy `hyperion bug-rca` 命令;新主路径 opencode 用户启动长驻,无 per-stage respawn。② hyperion mcp serve 自身冷启已很低频:重模块全 lazy(embed.py:245 方法体内)、build_server 启动不加载 torch、默认 `openai_compatible` embedder 走 RemoteEmbedder **0 次 torch 加载**。③ D0 streamable-http(cli.py:323-339 warm 长进程)已覆盖 MCP server 侧。前沿对照:MCP cold-start 业界主流 = lazy-load tools by intent + warm 进程 + transport 选择,无 persistent-session 编排层。性能优化转:**按 intent lazy-load MCP 工具**(当前 ~13 工具,YAGNI 等到 20+ 再上)+ ~~推 stdio→http(待 opencode 解注册,踩坑#10)~~ **⚠️ 2026-08-12 obsolete**:不在三件事内(纯 MCP 传输协议优化)+ 踩坑#10 opencode 1.18.11 http MCP 不注册原生工具(agent 看不到 `hyperion_*`,只能 curl 手工握,烧 token —— 上游 bug,Hyperion 改不了)+ cold-boot 痛点已消失(opencode `listTools` 便宜不加载 embedder,stdio 冷启能在 timeout 内注册;首次 recall/search 调用才冷启 embedder,timeout ≥120000ms 够,模板已 10000→120000 `6338e85`)。Hyperion 侧 `cli.py` 早就支持 `--transport http`(streamable-http warm 长进程),等 opencode 上游修了工具注册零改动可切。**不主动做,等上游。**

56. **delegate 可观测性(timeout 存 stdout + 流式 + delegate_log 落盘)** — `delegate.py`。
    - 现状(R2):`subprocess.run` capture_output 跑完拿全部;**timeout 时 `except TimeoutExpired` 丢 stdout**(`delegate.py` 不存,看不到 opencode 跑到哪);`--format json` **块缓冲**(流式观察失败,诊断脚本收不到中间事件);`/tmp/delegate_debug.txt` 是临时诊断(非正式,且 A+C 达标后可删)。
    - 改:① **timeout 也存已收 stdout**(`TimeoutExpired` 前的 proc.stdout 部分落盘);② **流式读 stdout**(`Popen` + 逐行读,实时观察 step/text/tool 事件,不再块缓冲盲等);③ **`delegate/delegate_log/` 落盘**(workspace-design §2 已留目录)+ step_events 持久化(对标 deer-flow `subagents/step_events.py`,供可观测回放)。
    - 目标阶段:**R3**(workspace 完整时 delegate_log 落盘 + 流式)+ **R5**(可观测生产化,Langfuse 串 thread_id)。

## ★ 工具驱动委托(2026-07-31 简化:砍 Hyperion 侧漏斗 → MCP 工具)

57. **bug-RCA 简化:砍 Hyperion 侧定位漏斗,改 opencode 自主定位 + Hyperion 能力做 MCP 工具** — `graph.py` / `localize.py` / `nodes.py` / `tools/mcp_memory.py` / `config/opencode_hyperion.json`。
    - 背景(踩坑 #2,见 `docs/踩坑记录.md`):Hyperion 自建 Agentless 式漏斗(file→function→line)+ regex 关键字 + 方案A 预筛 = 与 opencode **重复定位**(double localization)。三轮调研(全流程体检 / agentic RAG / opencode 能力核实)坐实 + 2026 共识(deer-flow 2.0 / Claude Code / OpenHands = lead agent + 工具,不重造能力)。
    - **砍**:`workflows/bug_rca/localize.py`(file→function→line 漏斗)+ `nodes.py` 的 recall/localize/assemble_localize 三节点;流程从 8 步并到 **5 步**(ingest→delegate_localize_loop→assemble_repair→delegate_repair_loop→report_memorize)。`trigger_parser/parser.py:extract_keywords`(regex)弃。
    - **建**(能力做 MCP 工具,opencode 按需调):`hyperion mcp serve` 暴露三工具 —— `hyperion_recall`(记忆,扩 `tools/mcp_memory.py`)+ `hyperion_search_codebase`(包 `code_index.retrieve`,回真实符号带 provenance,**防幻觉**)+ `hyperion_filter_logs`(包 `trigger_parser/log_filter.py`)。opencode 对此**原生支持**(已核实源码:`mcp/index.ts` + `catalog.ts:42-63`,MCP 工具注册成 `dynamicTool` mid-session 可调)。
    - **接线**:`config/opencode_hyperion.json` 加 `mcp` 段(stdio `hyperion mcp serve`)+ `hyperion-localize` agent prompt 加"优先调 hyperion_* 工具"nudge + permission 放行 `hyperion*`;`delegate.py:_parse_stream` 加收 `tool_use` 事件(审计 opencode 调了哪些工具,~3 行)。
    - **防幻觉契约**:`search_codebase` 只回索引里真实存在的符号(validated against `parser.py` Symbol 表);**抽概念不抽标识符**(opencode 查询 → 工具回真实候选 → opencode 选)。
    - **token 取舍**:永远相关的廉价件(召回教训)可预进 prompt(0 turn);重/钻取件做工具。工具廉价 + 回手术级摘录 = 守住 Agentless "固定漏斗便宜 ~6×" 红利,又不重复 opencode。
    - 现状:**✅ 已完成**(commit `6e0a039`,2026-08-03):代码 8→5 节点 + 三 MCP 工具上线 + e2e verified=True(patch `git apply --check` 过)。委托重试(A+C,commit `282fc8d`)。
    - 完整设计:`bug-rca-design.md` §1(流程)/ §2(为什么砍)/ §6(MCP 工具+接线)。
    - 目标阶段:**R3.1**(MCP 工具落地 + 砍旧漏斗节点 + e2e 跑通 demo2)。✅ 已完成。

- **#58** ✅ 已修(2026-08-03,R3.2):`_symbol_to_chunks`(符号路径)+ `_module_chunks`(模块路径,这才是真正肇事点——vendor 头无被解析符号 → 整文件落 module chunk)按行区间二次切分,复用预留 part/total + 独立 content_hash + 真实行号;`MAX_CHUNK_CHARS` 改总字符数=16000(留 DashScope 33000 安全余量);`_SKIP_DIRS` 加 `.pc`;6 单测绿 + wpa 全仓扫描 max=16000/0超限。
    - 原详情(留档):现象 = `hyperion index wpa` 挂 400 "Range of input length should be [1, 33000]";根因 = chunker 按 Symbol 整块切无 max-chunk-size 兜底。**离线扫描发现 driver_nl80211.c 其实没事(符号各<12K),肇事的是 qca-vendor.h(300KB 无符号头 → 单 module chunk 304044 字符)→ module 路径(`_module_chunk`)也得切,光修符号路径不够。**

## ★ recall→定位 反馈闭环:价值未证实(2026-08-06 实测复盘,待验证)

59. ✅ **已成(2026-08-11 验证:正向)** recall→bug-RCA 定位 的反馈闭环价值 —— `node_recall_lessons`(②[b])+ delegate 调 `hyperion_recall`。**结论:类似 bug recall 有增益(3-4× 提速收敛,质量持平/略胜);老偏负实测是"同 bug"最弱场景,不矛盾。详见 [[recall-validation-handoff]]。** 下一步建议:上 B(P1 自动 query)前补 1 轮确认(N=1/臂局限)。
    - 背景:R3 收尾建了 ②[b](确定性 recall 预注入)+ 填 BugLesson 字段(②[a]),roadmap([[similar-bug-recall-roadmap]])背书"该做"。但 2026-08-06 用 demo2 真实数据复盘:**"能动"但"有用"未证实,且唯一实测偏弱/偏负**(同 bug 场景)。
    - 实测发现(demo2 同 bug 反复跑):
      ① **机制成立**:recall 正确返回先验(top1 带根因+file:line),delegate 也调了 hyperion_recall。
      ② **但没带来增益**:delegate 照样 26 read+17 grep、撞步数上限被强制收尾、**照样产次优补丁**(35s 超时兜底,非金标 `scan_only_handler` 落点)。recall 既没省工、也没提质量。
      ③ **同 bug 是 recall 价值最弱场景**:先验=上次答案 → 要么冗余(delegate 本就能定位)要么**锚定**(先验和最终结论几乎逐字一致;上次次优修法的痕迹可能代代相传)。recall 真正卖点"类似 bug"我们**从没测过**(只有 demo2)。
      ④ **去重失效**:memory 里 8 条同 demo2 bug 的近似 lesson 没合并(content-addressed id 对"同 bug 不同措辞"太严),conf 都停 0.35(从未同 id 重提 bump),memory 在积近似项。
      ⑤ **②[b] 在 demo2 上 dormant**:demo2 纯日志驱动(trigger="")→ recall_lessons 按设计跳过;recall 实际只靠 delegate 自觉调 MCP 工具(软 nudge)。
    - **结论(诚实)**:基础设施(store/recall 4 路/memorize)合理且参考仓最强;但 **recall→定位 这条具体反馈的价值是"未证实的假设"**,我们 ahead of validation 地把注入做出来了(踩"先建后证"边)。
    - **该做的(验证优先,别再加机关)**:找个**和 demo2 不同但相关**的 wpa bug(如另一个 radio work 泄漏 / p2p 扫描别的症状),清空或隔离 memory 跑基线 vs 带 demo2 lesson 跑,对比定位步数/根因准确度/补丁质量。**有增益 → 设计证实,B(P1 自动 query)才值得做;无增益 → recall 用途转向(喂报告/补丁选型而非定位)或降级 dormant。**
    - **暂不做(避免重复过度设计)**:B(P1 自动 query 构造)—— 给未证实机制加更多机关 = 沉没成本驱动加复杂度,先验证。A(给 demo2 加 --trigger)—— 只让机关动起来给观测用,不改善同 bug 质量、反增锚定,别常驻(最多一次性观测)。
    - 目标阶段:**R3 收尾后的验证实验**(在动 P1 自动 query / 加 recall 机关之前必做)。关联 [[r3-memory-closure-handoff]] [[similar-bug-recall-roadmap]] [[avoid-overengineering]] [[pitfall-log]]。
60. **merge_eval 的 apply 检查 worktree → merge-tree(生产级)** — `src/hyperion/services/code_index/code_graph.py:merge_eval`。
    - 现状(MVP,2026-08-11):逐 commit apply 检查用 `git apply --recount --check`(strict 一步)对**当前 worktree**。故 SKILL 硬性要求调用前 `checkout fork_ref` + worktree 干净,否则三态失真;且不能并发(共享一个 worktree)。
    - 升级:`git merge-tree --write-tree <fork_ref> <upstream_commit>`(git 2.38+)在内存里对两个 ref 做 three-way merge,**不 touch worktree** —— 无需 checkout、可并发、对脏 worktree 安全。`--name-only` 出冲突文件清单 → `applies_cleanly` 判定更准。
    - 优先级:低(MVP strict 检查 + SKILL 的 checkout 约束已能用);触发条件 = 需并发扫多仓 / 或 fork_ref 不能 checkout(HEAD detached 场景)。关联 [[upstream-merge-handoff]]。

61. ✅ **已成(2026-08-12)** 摘要触发改 token 感知(architecture-review §五 建议 B)— `src/hyperion/platform/runtime/factory.py`。
    - 现状(旧):`SummarizationMiddleware(model, trigger=("messages",50), keep=("messages",20))` —— 按消息条数触发,50 条大消息(塞 diff)过晚压、50 条小消息过早压;消息数 ≠ token 数。
    - 已落地:`trigger=("tokens", 32000)`(对齐 deer-flow `config.example.yaml:1563` 生产默认)+ 新 `SummarizationConfig` dataclass(enabled/trigger_tokens/keep_messages)。langchain 1.3.14 原生支持 token trigger,**直接生产级**(零自写子类,踩坑#2)。实测 `("fraction",F)` 排除:三模型 ChatOpenAI profile=None,用 fraction 构造 raise ValueError 让 agent 崩。不进 config.yaml(token_budget/tool_output yaml 未 wire,对齐 turn_budget 先例)。2 单测 + 全 runtime 26 绿。详见 [[suggestion-b-token-summarization-handoff]]。

62. ✅ **已成(2026-08-13)** 记忆巩固自转(architecture-review §五 建议 D)— `src/hyperion/services/memory/backends/native/service.py`。
    - 现状(旧):`consolidate()` + `hyperion memory consolidate` CLI + recall bump 都已实现且 work(e2e 实测 GREEN),但 recall 不顺手 consolidate,得手动敲 CLI。
    - 已落地:`NativeMemoryService.recall` 命中 memory 路条目时 `asyncio.create_task` fire-and-forget 跑 `_safe_consolidate`(对标 Cognee self-improving)。`NativeMemoryConfig.auto_consolidate: bool = True` 开关,复用 `promote_access_count` 阈值。不挂 search()(bump=False 无信号)。**更正 architecture-review「无人调」误判**:CLI 早 wire + recall 早 bump,真缺口只是自转。3 单测(e2e 全链 + 自转 + disabled)+ 全记忆 37 绿。详见 [[suggestion-d-memory-consolidation-handoff]]。

63. ✅ **已成(2026-08-13)** 补 wrap_model_call 兜底历史漏网大消息(architecture-review §五 建议 C)— `src/hyperion/platform/runtime/middlewares/tool_output.py`。
    - 现状(旧):`wrap_tool_call` 只处理新工具结果;历史 ToolMessage 漏网大消息(断点续跑/改阈值/旧 checkpoint)无人兜底。原 TODO 推 R3.2。
    - 已落地:`_budget_content`/`_patch_tool_message` 加 `externalize: bool = True` 参数(历史路径 False → 跳外化只走 fallback head+tail);新 `_is_over_fallback` + `_patch_model_messages`(抄 deer-flow:539-565 预扫描模式,无超阈值返 None 不重建 list);`ToolOutputBudgetMiddleware` 加 `wrap_model_call`/`awrap_model_call`(抄 loop_detection:233-238 `request.override`)。**不改 factory 装配**(中间件已在链)。3 单测(截断漏网大消息 / 不外化无磁盘文件 / 干净历史返 None)+ 全 runtime 29 绿。**校正 architecture-review 短板 2 误判**:原「synopsis 不二次压缩」表述错 —— deer-flow 生产级也不二次压 synopsis(靠摘要治累积 = 建议 B);真缺口是 wrap_model_call 兜底钩子。详见 [[suggestion-c-tool-output-wrap-model-handoff]]。

64. ✅ **已成(2026-08-13)** 记忆向量换 sqlite-vec ANN 渐进式(architecture-review §五 建议 A,优先级最高)— `src/hyperion/services/memory/backends/native/store.py`。
    - 现状(旧):`search_vector` Python 逐行 O(N) cosine(fetchall 拉全量 → for 每行 np.frombuffer+np.dot),N 上千后 p95 劣化。DDL 注释自承「ANN 在 backlog」。
    - 已落地:**渐进式双路径阈值切换** —— `count(scope)>ann_threshold(默认 500)` 切 sqlite-vec vec0 KNN,否则现状 loop(benchmark 实测 N<200 loop 更快、N>500 vec0 快 2-4×)。延迟建表 `_ensure_vec_table(dim)`(镜像 code_index `_open_or_create` 维度探测);`upsert` 同事务双写 `_vec_upsert`(DELETE+INSERT,vec0 无 ON CONFLICT);partition_key=owner+codebase 硬隔离 KNN;`_search_vec0` cosine metric(distance=1-sim)+ active/repo 后过滤(over_fetch=limit×4)。`auto_index:bool=True`+`ann_threshold:int=500` 开关,加载失败降级纯 loop(绝不崩)。**调研三校正**:① 原「迁 LanceDB」→ 改 sqlite-vec(store docstring 拒绝 LanceDB;同栈零冲突,0.1.9 已在 site-packages 零新依赖);② deer-flow 生产级纯 BM25 零向量(这规模段 ANN 易过度设计但仍做);③ numpy 向量化死路(逐行解码 BLOB 主导)。**探针盲点修正**:sqlite-vec 默认 metric 是 L2 非 cosine → 建表须显式 `distance_metric=cosine` + 跳过零向量(cosine 未定义)。5 单测(延迟建表+双写/阈值分流/KNN↔loop 召回一致误差<1e-4/partition 隔离+active 过滤/auto_index=False 降级)+ 全记忆 42 绿。详见 [[suggestion-a-sqlite-vec-ann-handoff]]。
