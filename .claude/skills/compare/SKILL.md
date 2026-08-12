---
name: compare
description: 对比两个版本/两个仓库的某个流程或模块有什么差异——锚定两版各自的流程入口函数,逐节点读函数体语义对照,聚成流程级差异报告。用户问"v20、v25 蓝牙在连接流程上有什么差异"、"这两个版本的 X 功能实现有什么不同"、"新版本改了哪条流程"时用。
allowed-tools:
  - hyperion_search_codebase
  - hyperion_repo_map
  - hyperion_call_chain
  - hyperion_memory_recall
  - hyperion_memory_memorize
  - hyperion_export_report
  - hyperion_ensure_repo
  - read
  - grep
  - glob
---

# 跨版本代码对比调研

你负责对比**两个版本**(或两个仓库)在某个流程/模块上的实现差异。比如 v25 和 v20 两条独立发行版线,用户问「蓝牙连接流程有什么差异」。读码、配对函数、对照差异都是你的活;Hyperion 工具负责取代码、查调用链、落盘、记忆。

**两个边界**(必须守):
- **只调研不改代码** —— 你不改任何仓库(不 edit / 不 git apply / 不写源码),只读码出对比报告。和 upstream-merge/patch-review 一样是 read-only。
- **对比事实读码即记,不需等用户验证** —— 对比调研是纯读码事实(读函数体对照两版),不依赖编译/真机验证,**读完即可 memorize**。这跟 backport/bug-rca/patch-review 不一样(那三个涉及「bug/补丁是否真修对」,要等用户真机验证才能记)。对比结论本身就是读码坐实,记下来让下次同类问题直接 recall 命中秒答。

**核心难点**:把两版的函数配对起来是**语义判断** —— v20 的 `foo` 和 v25 的 `bar` 可能职责相同但改名了,也可能一个函数在 v25 被拆成两个、或合并成一个。**没有确定性工具能自动配对**(各 codebase 结构图独立,无跨版本联合图)。这步靠你 `read` 函数体推理 —— 同名直接配;名字不同就读实现看是否同职责。这是整个 skill 最吃判断力的一步。

## 运行模式

1. **确认两版 + 流程主题**:问清两个 codebase 各代表哪版(如 `bluez` = v25 新版、`bluez_v20` = v20 旧版)+ 用户关心的**流程主题**(「连接流程」/「配对流程」/「SDP 服务发现」/「GATT 发现」...)。本地没仓 → `ensure_repo`(只读 clone)。**先把主题词想成一个代码概念**(「连接流程」→ connection establishment / connect / pair / link),后面检索用概念不用文件名。
2. **锚定流程入口【核心·阶段 A】**:对**两版各跑一次** `search_codebase(query=<流程概念>, codebase=<各>)` + `repo_map(codebase=<各>)`。拿到**两版各自的入口函数群 + file:line**(工具只回索引内真实符号,防幻觉)。流程跨多个函数 → 用 `call_chain(symbol=<入口函数>, codebase=<各>)` 从入口多跳展开,看清整条流程涉及的函数链。
3. **建立两版函数对应【语义判断·核心】**:把两版入口函数群**配对** —— 同名直接配(`bt_connect` ↔ `bt_connect`);名字不同就 `read` 函数体判**是否同职责**(v20 `bt_connect` ↔ v25 是否拆成了 `bt_connect`+`att_connect`?)。配不上的标「v20 无 / v25 新增」。**这是语义判断,无确定性工具**,靠读函数体推理。
4. **逐节点对照【阶段 B】**:对配上的每对函数,`read` 两版完整函数体,讲清差异 —— 逻辑分叉 / 参数变化 / 新增校验 / 删除的步骤 / 重构。流程上的每个关键节点(入口、状态转换、资源释放...)都对照一遍。`memory_recall(query, codebase=<各>)` 两 codebase 各查一次,翻历史调研事实补充上下文(可能之前调研过相关模块)。
5. **聚流程级结论【阶段 C】**:把节点差异聚成**流程级差异** —— 入口差异 / 状态机差异 / 新增环节 / 删除环节 / 重命名映射。给出因果解读:为什么 v25 多了某个环节(如新协议层)/ 为什么改名(职责拆分)。不要只罗列文件差异,要讲清流程层面变了什么。
6. **落对比报告**:`export_report` 落盘对比报告 .md。**每条结论必须附双源 file:line**(v25 的 + v20 的),对齐 cited-reporter 防幻觉。
7. **memorize(读码即记)**:`memorize(kind=codebase_fact, kind_detail=architecture, summary=<两版流程差异 + 因果>, evidence=[<双源 file:line + 代码片段>], codebase=<标注两版>, confidence=<你的把握>)`。这条事实读码即坐实,**不需等用户验证** —— 下次有人问同类问题(「v20/v25 连接差异」)直接 recall 命中秒答。

## 工具(按需调)

| 工具 | 何时调 | 要点 |
|---|---|---|
| `hyperion_search_codebase(query, codebase?)` | step 2 锚定入口 | 传**概念**别传文件名(如"蓝牙连接建立流程");两 codebase 各跑一次;只回真实符号 |
| `hyperion_repo_map(codebase?)` | step 2 俯瞰两版骨架 | Aider repomap 式 PageRank 符号地图,找流程入口模块;两 codebase 各跑一次 |
| `hyperion_call_chain(symbol, codebase?)` | step 2 流程展开 | 从入口种子多跳展开,看流程涉及的函数链;两 codebase 各跑 |
| `read` / `grep` / `glob` | step 3/4 读两版函数体 | **核心**:step 3 配对判同职责 + step 4 逐节点对照全靠 read 两版函数体 |
| `hyperion_memory_recall(query, codebase?)` | step 4 / 全程 | 翻历史调研事实(两 codebase 各查);下次同类问题命中即秒答 |
| `hyperion_memory_memorize(...)` | step 7(读码即记) | kind=codebase_fact,kind_detail=architecture,带双源 evidence;**不需用户验证** |
| `hyperion_export_report(content, repo_path, out_dir)` | step 6 落盘 | 写对比报告 .md |
| `hyperion_ensure_repo(name)` | 本地没仓 | 只读 clone |

## 硬约束

- **只调研不改代码** —— 不 edit / 不 git apply / 不写源码;read-only 调研(和 upstream-merge/patch-review 一个标准)。
- **两版函数配对是语义判断** —— 没有确定性工具能自动配对;各 codebase 结构图独立无跨版本联合图,`cross_version_diff` 也只支持同仓两 ref(两个独立仓无效)。必须 `read` 函数体判同职责。
- **不用 cross_version_diff** —— 它是「同一个 git 仓的两个 ref」对比,v20/v25 这种两独立仓无效;两版差异靠各 codebase 检索 + read 对照。
- **结论必须附双源 file:line** —— 每条差异结论都要标 v25 的 + v20 的 file:line,防幻觉,对齐 cited-reporter。
- **对比事实读码即记** —— 不像 bug/补丁要等真机验证;对比结论读码坐实,step 7 可直接 memorize,下次秒答。

## 对比卡(你的输出格式)

```
对比: <流程主题>     v25: <bluez @ ref>   ↔   v20: <bluez_v20 @ ref>

入口函数:
  v25 <func@file:line>  ↔  v20 <func@file:line>   状态: 同名 / 重命名 / 仅一版有

流程节点差异:
  节点          v25                              v20
  连接入口      <func:line>                      <func:line>
  ATT 建立      <func:line>(v25 新拆分)          —(v20 尚无独立 ATT 层)
  ...           ...                              ...

结论: <一段话——流程级差异 + 因果解读(为何 v25 多了某环节 / 为何重命名)>
sources: 每条结论附双源 file:line
report:  <export_report 落盘路径>
memorize: 已记 kind=codebase_fact(读码即记,下次同类问题 recall 秒答)
```

`状态` = 你 step 3 语义配对的结论(同名 / 重命名 / 仅一版有);`流程节点差异` 每行取自 step 4 读两版函数体的对照;`结论` 是 step 5 的流程级因果解读。

## 不要

- **改任何仓库的代码** —— 只读调研,不 edit / 不 git apply / 不写源码。
- **拿 cross_version_diff 比两个独立仓** —— 它只支持同一个 git 仓的两个 ref,两个独立仓(v20/v25)无效;两版差异靠各 codebase 检索 + read 对照。
- **指望有确定性工具自动配对两版函数** —— 没有;各 codebase 结构图独立无联合图。必须 `read` 函数体判同职责。
- **只罗列文件差异不讲流程级结论** —— step 5 要把节点差异聚成流程变了什么 + 为什么,不是 git diff 堆栈。
- **结论不带双源 file:line** —— 每条差异都要标两版的 file:line,防幻觉。
- **等用户验证才 memorize** —— 对比是读码事实,读完即记(区别于 bug/补丁型 skill);不记就丢了「下次秒答」。
