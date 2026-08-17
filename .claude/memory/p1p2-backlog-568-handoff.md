---
name: p1p2-backlog-568-handoff
description: "2026-08-17 🟡#5/#6/#8 三项落地:AGENTS.md opt-in / merge-tree 零 touch 判冲突 / 删 deep 空壳参数"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9c2c0db8-4586-4c04-8e41-3770bae44cfd
  modified: 2026-08-17T02:22:58.101Z
---

# 🟡#5/#6/#8 落地交接(2026-08-17)

🟡 中优先三项同日收口(P1/P2 backlog 只剩 ⚪ 触发级)。全量 287 测绿 + ruff clean。

## #5 AGENTS.md 产出(export_report 加 opt-in 参数)

`export_report(..., agents_md: bool = False)`:传 True 时同源报告内容 + 生成头注释写 `<repo_path>/AGENTS.md`(仓根,agents.md 惯例 —— opencode/claude code/cursor 原生读)。**两个保护**:① 默认关(不问自写入用户仓 = 最小惊讶违背);② **已有 AGENTS.md 拒写不覆盖**(手写/别的工具产物)。docstring 写明「蒸馏 ≤60 行,精不要全」(ETH Zurich 2026 arXiv 2601.20404:冗长 AGENTS.md 拖累 agent)。onboarding/compare SKILL step6 + agent prompt 同步:用户显式要求才传;compare 只写用户指定的目标仓。3 断言单测(默认关仓根无文件/opt-in 写+头注释+同源/已有拒写)。

**没做成「模板渲染」而是「content 直写」的取舍**:原 backlog 设想「报告渲染加模板」,实现改为 agent 在 content 里蒸馏(agent 拿着报告自己写 ≤60 行 agent 版比死模板准);工具只负责 opt-in 门 + 不覆盖保护 + 落盘。

## #6 merge_eval 升 git merge-tree --write-tree(原 backlog #60)

**痛点**:apply 检查对当前 worktree 跑,三态押在「agent 先 checkout fork_ref + 干净树」的自觉上。**修**:`git merge-tree --write-tree <fork_ref> <commit>` 在对象库试合并(不碰 worktree/索引/ref),rc=0 干净/rc=1 冲突/rc>1 uncertain。merge-tree 探测一次性做在循环外(用 fork_ref 对自身 merge 探,rc=0 即可用);老 git(< 2.38)→ 回退老 apply --check + note 明示「三态可能失真」。

**探针坐实的 git 行为(git 2.50)**:① 冲突 rc=1、干净 rc=0,**但管道会吃 rc**(`| head` 后 `$?` 变 0,`$?` 要直读);② 输出首行是合并树 oid,冲突文件列表在其后(`--name-only` 同);③ 三方合并按 hunk 上下文重叠判冲突 —— fork 改行首 + upstream append 行尾**同文件也判 conflict**(夹具设计别踩:要「干净」臂必须用不相交文件)。

**文案三处同步**(全降级「checkout 硬门」→「rev-parse 可解析即可」):工具 docstring / upstream-merge SKILL step3+硬约束 / agent prompt(step③ + 关键约束 + bash 工具行去 checkout)。单测:脏树 + 停 main + 不 checkout fork → recommend_merge 判对(老路此姿势必失真)。**夹具坑**:建 branch fork 忘先 checkout main → fork 建在了 upstream 头上,U2 被 patch-id 判 already_fixed(排查思路: leftover tmp 仓 `git branch -a -v` 一眼看出)。

## #8 删 deep 空壳参数

五接触点全清(graph.run 签名+docstring / nodes `deep = bool(state.get("deep"))` / _analyze 形参 / state.py `deep: NotRequired[bool]` / CLI `--deep` argparse+透传),三处注释留痕(真需求来了按 deep_research 子 agent 模式实现)。patch_report 16 测回归绿。剩余 "deep" grep 全是 deepin 打包无关词。

## 验证姿势(可复用)

/tmp/mt-e2e 三分支仓(main/upstream/fork)+ 停 main + echo DIRTY >> f.c 制脏 → merge_eval 探针三态;老路对照同仓 checkout fork4 干净树手动跑 apply --check 对拍结论一致。改 SKILL/prompt 后 `python3 -c "json.load(open('config/opencode_hyperion.json'))"` 验 config 合法。

关联:[[when-introduced-handoff]](同 backlog 上一项)/ [[upstream-merge-handoff]](#6 改的就是它的硬门前提)。
