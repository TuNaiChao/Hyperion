# MCP 使用与设置(小白版)

> 一句话:**MCP 是「AI 工具的 USB-C 接口」** —— 工具方按协议做成一个 MCP server,coding agent(opencode 等)插上即用,不用为每个 agent 重写一遍集成。这份文档讲四件事:MCP 是什么、RootRecall 交给 agent 的四层东西怎么配合、为什么别家的 MCP「免接线」而本仓要「接线」、以及从这次调研里沉淀的 MCP server 设计最佳实践。

## 一、MCP 是什么:USB-C 比喻

在没有 USB-C 的年代,每个设备一根专用线:相机一根、硬盘一根、手机一根,抽屉里缠成一团。在 MCP 出现之前,AI 应用接工具也是这样:每个 agent 给每个工具写一套专门集成,写一次只能一家用。

**MCP(Model Context Protocol)** 是 Anthropic 开源的开放协议,把这些「专用线」统一成一根通用线:工具方把能力做成 **MCP server**(一个遵守协议的进程),任何支持 MCP 的 agent —— opencode、Claude Code、Cursor、Codex……—— 都能直接连。OpenAI、Google 相继跟进,MCP 已是事实标准。

三个角色,像一栋办公楼里的分工:

| 角色 | 是谁 | 干什么 |
|---|---|---|
| **Host**(宿主) | opencode | 大管家:接收提问、决定调哪个工具、组织回答 |
| **Client**(客户端) | 管家派出的联络员 | 一个 server 配一个联络员,负责传话 |
| **Server**(服务器) | RootRecall | 工具方:亮出工具清单,按调用干活、返回结果 |

Host 和 server 之间走 JSON-RPC 消息。传输方式两种:

- **stdio**(本仓用的):server 是 host 拉起的一个本地子进程,通过标准输入输出对话。像**上门私厨** —— 厨师直接进自家厨房做菜,快、私密、零网络配置。
- **Streamable HTTP**:server 是一个远程服务,多个客户端可以共享。像**云端餐厅** —— 谁都能点单,适合团队部署。

## 二、四层能力:一个工具型项目交给 agent 的东西

RootRecall 不是一个「装好就能用」的 exe,它交给 opencode 的是四层东西,缺一层都少一块能力:

| 层 | 是什么 | 比喻 | 本仓对应 |
|---|---|---|---|
| **MCP 工具** | 可被模型直接调用的函数,带名字、参数说明、返回结果 | **手** —— 能做的动作 | 16 个工具:`search_codebase` / `memory_recall` / `validate_patch`……见 [mcp-tools.md](mcp-tools.md) |
| **skill** | 一份 `SKILL.md` 说明书,教模型「遇到什么问题、按什么顺序、组合哪些工具」 | **菜谱** —— 知道先切菜还是先热锅 | 8 个 skill:`bug-rca` / `backport` / `onboarding`……路由判据见 [skill-routing-matrix.md](skill-routing-matrix.md) |
| **agent block** | 预制角色:指定模型 + 权限 + 禁令(比如只读 skill 禁 bash) | **工牌** —— 能进哪个车间、能碰哪台机器 | 10 个 block:8 个 subagent(`rootrecall-bug-rca` / `rootrecall-compare`……,要硬门隔离或点名时委派)+ 2 个隐藏内部 stage(`rootrecall-localize` / `rootrecall-repair`,老 delegate 流水线专用) |
| **配置/接线** | 让以上三样被 opencode「发现」的注册动作 | **入职引导单** —— 新员工第一天该去哪报到 | `opencode.json` + [quickstart.sh](../scripts/quickstart.sh) / [wire_opencode.sh](../scripts/wire_opencode.sh) |

业界共识是**工具和菜谱要配套用(use both)**:工具给「能力」,skill 给「流程」。只有工具,模型知道能做什么但不知道标准工序;只有菜谱没有工具,模型知道工序却没家伙可使。philschmid 的总结一针见血:*"Skills complement MCP by teaching agents when and how to combine those tools for specific workflows. Use both."*

两层各吃多少上下文,差别很大,靠**渐进披露**控制:

- 工具的**名字 + 描述(schema)常驻**上下文 —— 模型随时要决定「调不调它」,所以必须一直看得见。这也意味着工具越多,固定开销越大。
- skill 的**元数据(约百来个 token)常驻**,正文只在触发时才读,引用文件按需再读 —— 所以 skill 可以写得很厚而不占日常开销。

## 三、别人家的 MCP 为什么「免接线」

接别的 MCP(比如 Playwright、Context7)通常只在 opencode 全局配置里写一段就完事,在任何目录启动都能用;RootRecall 却要「从本仓根启动」或「先接线」。差别不是玄学,是三件结构性的事:

| # | 别人家的 MCP | RootRecall |
|---|---|---|
| 1. 注册位置 | 在**全局配置** `~/.config/opencode/opencode.json` 注册一次;opencode 的配置是分层合并的(全局层 → 项目层,只覆盖冲突键),所以处处可用 | 按仓注册:本仓根的 `opencode.json`,或接线后 bug 仓里生成的 `opencode.json` |
| 2. 启动命令 | **自足**:`npx -y xxx` / `uvx xxx` 从全局缓存拉起,启动目录在哪都无所谓 | `uv run rootrecall mcp serve` 按**当前目录**找本仓 `.venv` —— 目录不对就拉不起来 |
| 3. 有无家当 | **无状态**:不依赖某个固定目录的数据;真需要路径就当参数显式传(Serena 的 `--project`、code-index-mcp 的 `--project-path`) | 三样**家当锚在本仓根**:`.venv`(运行环境)、`data/`(记忆库 + 索引,绝不能漂)、`.env`(密钥) |

打个比方:**`npx` 型 MCP 像外卖店** —— 店开在全局缓存,不管客人坐在哪个目录点单,都能出餐;**RootRecall 像自家厨房** —— 锅碗(`data/`)、灶具(`.venv`)、秘方本(`.env`)都锁在厨房里,要做菜就得回厨房,或者拉三根线把水电接到饭桌上。

「拉两根线」就是接线脚本干的事(见下节)。顺带一提:本仓 16 个工具都接受 per-call `codebase` 参数,和 Serena 的 `--project` 是同一族思路 —— 路径当参数传,数据不搬家;但 `.venv`/`.env` 这些「厨房基础设施」还是得靠启动位置或 `cwd` 锚定解决。

## 四、本仓的三种接入姿势

### 姿势 ① 默认:从本仓根启动(零接线)

```bash
cd RootRecall && opencode
```

为什么零接线:启动目录 = 厨房本身 —— `uv run` 就地解析 `.venv`,skill 从 `.claude/skills/` 自动发现,`.env` 由 rootrecall 进程启动时自行加载,什么都不用额外做。装好后第一条路就是它,前 5 步见 [quickstart.sh](../scripts/quickstart.sh)。

另外,仓库根本身的 `AGENTS.md` 会被 opencode 注入每个 agent 的系统提示 —— 打开 opencode 停在默认界面直接提问即可,agent 按这张路由表自动载入对应 skill,不需要按 Tab 切换模式(8 个 `rootrecall-*` 模式已从 Tab 列表撤下,改为后台 subagent)。

### 姿势 ② 接线:在 bug/工作仓里直接启动

调试系统软件时,工作现场往往在 bug 仓(比如一份 wpa_supplicant 检出)。不想两头切,就把三根线拉过去:

```bash
bash scripts/wire_opencode.sh /path/to/bug仓
```

- **门 1(skill 发现线)**:opencode 从启动目录沿 git worktree 向上爬找 `.claude/skills/`;脚本给 bug 仓放一个软链,指向本仓的 `.claude/skills`,8 个菜谱就地可见。
- **门 2(路由指令线)**:软链一份 `AGENTS.md` 指向本仓根的同名文件 —— 默认界面直接提问时,agent 靠这张「点单对照表」判断该载入哪个 skill(菜谱目录 opencode 本来就会递给每个 agent,缺的只是这张表)。单源真相:改本仓一份,所有接线过的 bug 仓同步生效。
- **门 3(MCP 锚定线)**:脚本在 bug 仓生成一份 `opencode.json`,里面用 opencode 官方的 `mcp.rootrecall.cwd` 字段把 rootrecall 服务器进程**锚回本仓根** —— 进程回到厨房里跑,`.venv` 找得到、`data/` 不漂、`.env` 照常自加载。

安全性:脚本是幂等的(重复跑无害);bug 仓已有自己的 `opencode.json`(不含 rootrecall)时会**备份成 `.bak` 后跳过**,绝不覆盖别人的配置;也不穿透软链写文件。接完 `cd <bug仓> && opencode`,`opencode mcp list` 应见 `rootrecall ✓ connected`。

### 姿势 ③ 全局注册(备选方案,当前不启用)

也能像外卖店那样装:把 `mcp.rootrecall`(含 `cwd` 字段)合入全局配置 `~/.config/opencode/opencode.json`,8 个 skill 软链进全局目录(opencode 官方支持 `~/.claude/skills/` 等全局位置),8 个 subagent agent block 一并合入 —— 装一次,任何目录启动都可用。

代价在哪:opencode 的**每一个**会话 —— 哪怕和 RootRecall 毫无关系的项目 —— 都会常驻这 16 个工具 schema + 8 个 skill 元数据 + 8 个 subagent block。工具 schema 是常驻上下文(见 §二),这笔「过路费」白交的次数太多。业界共识也是工具要**少而精**(见 §五)。

所以当前拍板:**按仓接线,不设全局** —— 用到哪个仓接哪个仓,干净、可控。真到了「哪哪都想用」的一天再翻案,方案就记在上面。更彻底的长期解法(把 `data/` 路径与启动目录解耦,让 RootRecall 也能 `uvx` 一条命令自足分发)在 backlog 里,触发条件成熟再做。

## 五、设计 MCP server 的最佳实践(调研汇总)

这是对 MCP 官方规范、Anthropic 工程博客、philschmid 实践文的调研沉淀,每条都标了本仓的落地情况。

### 工具怎么设计

- **面向结果,不面向操作**:别把 REST 端点 1:1 包一层;把「查影响面」这类多步操作合成一个高层工具,让模型一句话完成意图。本仓 16 个工具全是这个粒度(`blast_radius` 内部做完 BFS,不暴露走图原语)。
- **数量克制**:业界建议单 server 5–15 个工具、全局 3–5 个 server / 30–50 个工具封顶。工具 schema 常驻上下文,堆多了挤占正事 —— 有实测案例工具定义吃掉约八成上下文;Claude Code 的 ToolSearch 延迟加载就是治这个的,可省约 85% 相关 token。本仓 16 个略超单 server 建议,但全在「代码情报 + 记忆 + 硬门」一个域内、远低于全局上限,🟡 继续加工具时优先合并而不是新增。
- **命名 `{服务}_{动作}`**:opencode 自动给工具加 server 名前缀(`rootrecall_search_codebase`),调用方一眼可辨来源。规范硬性要求:名字 ≤128 字符、仅字母数字与 `_` `.` `-`、server 内唯一;**工具列表顺序保持稳定** —— 顺序一变 prompt cache 全失效,白花钱。
- **description 就是 prompt engineering**:写给「第一天上班的新员工」看 —— 何时该用、何时别用、参数怎么给、返回长什么样。Anthropic 自述**仅靠打磨工具描述**就拿过 SWE-bench 同期最佳;本仓工具描述已达「够用」,🟡 还欠一轮用 opencode e2e 真实调用记录回喂打磨(记 backlog)。
- **错误要教学**:用 MCP 规范的 `isError: true` 返回**可行动**的修正提示(「分页已到末尾,共 N 条」),不甩裸报错。模型读得懂的错误能自我纠正。
- **分页 + 诚实截断**:大返回给 `limit`/`offset` + 是否还有下一页的显式提示;必须截断时说明截了多少、去哪补齐。本仓 `memory_dump` 的分页与五个工具的 `_honest_truncate` 就是这条的落地(静默截断曾真踩过坑:体检 skill 被截掉一半记忆,靠 13 次补捞才救回来)。

### skill 怎么写

- skill 已是**开放标准**(agentskills.io,Claude Code / Codex / Cursor / opencode 等 40+ 客户端都认):一个目录 + `SKILL.md`(frontmatter 写 name/description),可选带 `scripts/`、`references/`。
- 三级渐进披露(元数据 → 正文 → 引用文件)是省 token 的关键;正文超 500 行就该拆引用文件。
- **skill 的受众是模型不是人**:指令式写法(「第 3 步做 X,若 Y 则跳到第 5 步」),别写项目内部八卦(本仓踩坑 #13)。

### 分发与路径锚定

- 传输:本地工具用 stdio(零网络配置),团队共享服务用 Streamable HTTP。本仓两者都支持,默认 stdio。
- 路径锚定三派:CLI 显式参数(Serena `--project`)/ 配置 `cwd` 字段(本仓门 2)/ 数据放仓内目录(本仓 `data/`)。没有对错,按家当多少选。

### 别和宿主比手艺

宿主 agent 自带的工具(read/grep/bash)往往比 MCP 里重新包一遍的更灵活。Serena 的做法是**检测到自己跑在 coding harness 里就禁用自家 read/grep** —— 不抢宿主的活。本仓踩坑 #2 同一教训:RootRecall 只做宿主没有的(记忆 / 结构图 / 硬门验证),读文件改代码的活全归 opencode。

## 六、常见问题速查

- **在 bug 仓启动,为什么别的 MCP 不用接线,RootRecall 要?** → §三:三样家当(`.venv`/`data/`/`.env`)锚在本仓根,两根线就是把「skill 发现」和「进程工作目录」拉过去。
- **接线会不会动到 bug 仓自己的 opencode.json?** → 不会。已有且不含 rootrecall 的配置备份成 `.bak` 后跳过;软链不穿透写;幂等可重跑。
- **忘了接线就在 bug 仓启动了会怎样?** → MCP 拉不起来(`uv` 在 bug 仓找不到 `.venv`)、skill 发现不了 —— 只是「连不上」,没有任何破坏;回本仓根启动或补跑接线脚本即可。
- **全局装行不行?** → 行,方案与代价在 §四姿势 ③,当前拍板不做。
- **`codebase` 参数该传什么名?** → 两套命名:**检索/情报类工具**(search_codebase、blast_radius、call_chain、repo_map、repo_overview、cross_version_diff、merge_eval、when_introduced)传「项目-版本线」名(如 `wpa-v25`,即索引名);**记忆类**(memory_recall / memory_memorize / memory_dump)传「项目名」(如 `wpa`)。原因:记忆按 codebase 标签隔离,传版本名会把教训锁进版本孤岛 —— v20 会话永远翻不到 v25 记下的东西;版本上下文写进 summary / evidence 即可。想裁剪注册的工具数 → `ROOTRECALL_MCP_TOOLS`(见下一条)。
- **16 个工具全注册太占上下文,能只开一部分吗?** → 能。环境变量 `ROOTRECALL_MCP_TOOLS` 门控注册:预设 `minimal`(记忆3+search_codebase+硬门3,纯 bug-RCA 最小集)/ `research`(记忆3+情报8)/ `full`(默认,16 个),或显式逗号清单(如 `memory_recall,validate_patch`)。写在 opencode 的 `mcp.rootrecall.environment` 里即可。没注册的工具不进 tools/list,模型看不见 —— 真省上下文(permission deny 只是调不了,schema 照占位)。
- **16 个工具各是什么?** → [mcp-tools.md](mcp-tools.md);8 个 skill 怎么选 → [skill-routing-matrix.md](skill-routing-matrix.md);配置项详解 → [configuration.md](configuration.md)。

## 参考链接

- MCP 规范与文档:<https://modelcontextprotocol.io>
- opencode 配置(分层合并 / MCP 注册):<https://opencode.ai/docs/config/> 与 <https://opencode.ai/docs/mcp/>
- opencode Skills(发现路径 / 权限):<https://opencode.ai/docs/skills/>
- Agent Skills 开放标准:<https://agentskills.io>
- Anthropic 工程博客(渐进披露 / 工具设计):<https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills>
- philschmid《How to correctly use MCP servers with your AI Agents》:<https://www.philschmid.de/mcp-best-practices>
- cra.mr《MCP, Skills, and Agents》科普长文:<https://cra.mr/mcp-skills-and-agents/>
