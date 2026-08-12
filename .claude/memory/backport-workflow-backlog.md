---
name: backport-workflow-backlog
description: 待建功能「跨版本 backport 工作流」——v25 已修→参考改 v20;两独立发行版线(Gerrit v20/GitHub v25)场景;小跨度 fix 范围;实测出的设计要点
metadata:
  type: project
---

**2026-08-11 用户提出,记 backlog(用户拍板:稍后做,不插队;先建 v20 索引)**。用户场景:**两条独立发行版线**(Gerrit 内网 v20 bluez 5.50 / GitHub v25 bluez 5.69+),要「发现某 bug v25 已修、v20 未修 → 参考 v25 代码改 v20」。范围:**小跨度 fix**(目标函数在 v20 里还在,能 cherry-pick 或小幅手改);大跨度(函数变了/没了)不支持。

## 实测出的关键事实(设计前必读,别重复踩)

1. **v20/v25 是两个独立仓库,共同祖先极远** → git 确定性工具大量失效:
   - `cross_version_diff` 能跑(列 commit + diff 文本),但 `git cherry` patch-id 等价摘要返 None(无共同祖先,patch-id 匹配不上)。
   - `merge_eval` 跨独立线判定**不可信**:实测 5 个 v25 commit 全判 `conflict`(其实是因为 apply 检查在 v25 仓 HEAD 上 apply v25 自己的 commit diff,worktree 用错 + 等价检测失真)。**「判 v20 有无此 bug」不能靠现有 merge_eval**。
2. **大版本跨度现实**:v20(5.50)和 v25(5.69+)隔好几个大版本。实测 `btd_device_set_gatt_db` 函数 v25 有(7686 行)、**v20 根本没有** —— GATT db 那套重写了。这种 fix 在 v20 无的放矢,不是"v20 有 bug"而是"v20 代码长不一样"。**故范围限定小跨度 fix**。
3. **理想样例**:`c50c7ea`(sdp_extract_seqtype 整数溢出 fix)—— 改 `lib/bluetooth/sdp.c` 的 `sdp_extract_seqtype`,v20 里该函数**存在**(在 `src/sdp-*.c` 调用),补丁只加 `val32 > INT_MAX` 检查几行、签名没变。但**文件路径变了 + hunk 上下文行可能微调** → `git apply` 大概率失败,需语义适配(opencode 读 v20 源码改路径/上下文)。这是 backport 工作流的典型 case。
4. **真补丁在 debian/patches/*.patch 里**(v25 是 debian packaging repo,实际 fix 是 quilt patch,不是直接改源码树的 commit)。backport 要从 .patch 文件取真 diff,不是 commit diff。`git show <sha>:debian/patches/<name>.patch`。

## 工作流拆解(4 步)+ 现有工具 gap

| 步 | 能力 | 现状 | gap |
|----|------|------|-----|
| 1 找候选 fix commit | 列 v25 上游 fix | ✅ cross_version_diff | 够 |
| 2 **判 v20 有无此 bug** | 确定性 patch-id 判 | ❌ merge_eval 跨独立仓不可信 | **最大设计决策点**(见下) |
| 3 取 v25 fix 精确补丁全文 | git show .patch | ✅ opencode 做 | 够(踩坑#2 不内置) |
| 4 生成 v20 版补丁 | coding 适配路径/上下文 | ✅ opencode + Hyperion 给上下文 + validate_patch | 缺 skill 串联 |

## 最大设计决策点:「判 v20 有无此 bug」怎么做(待调研选一)

确定性 git patch-id 在两独立线场景废了。候选方案(设计时要调研 + 实测选):
- **A. 函数名粗筛 + opencode 精判**:grep 目标函数在 v20 在不在 → 在的话 opencode 读两边代码判"v20 这版有没有这个 bug"(语义)。最现实。
- **B. v20/v25 双图函数体相似度**:CRG 两图,找同名函数,比对函数体 AST/diff 判"v20 是否已含等价修复"。准但重(双图 + AST 比对)。
- **C. 试 apply 三态扩展**:`git apply --3way` 到 v20,看冲突类型(路径变/上下文变/函数没了)分档。但 v25 补丁 apply 到 v20 源码树,文件路径都不同,基本全冲突,信号弱。

倾向 **A**(对齐 pivot:opencode 推理 + Hyperion 给精确上下文;确定性工具只做粗筛)。B 作将来精度升级。

## backport skill 形态(倾向)

镜像现有 `upstream-merge` skill(pivot 对齐:1 薄工具 + 1 skill,不建 workflow、不重造 delegate 能力)。skill 负责:列 v25 fix → 粗筛函数在不在 v20 → 取 v25 补丁全文 + 召回 v20 该函数源码(用 search_codebase)+ blast_radius → 交 opencode 适配生成 v20 patch → validate_patch(apply 到 v20)。opencode 自驱,Hyperion 当 MCP 工具提供情报(踩坑#2)。

## 地基(已就绪)

- v25 索引(codebase=`bluez`)+ v25 图:`data/code_index/bluez` + `data/structgraph/bluez`。
- **v20 索引(2026-08-11 建,codebase=`bluez_v20`)**:向量索引 + 结构图。backport 时 search_codebase + blast_radius 对 v20 用。
- 跨独立仓 fetch 已验证:`git remote add v20local ../../v20/bluez` + `git fetch` 进 v25 仓(cross_version_diff/merge_eval 要同仓两 ref,故 v20 得 fetch 进 v25 仓当 ref)。

## 诚实不做(范围外)

- **大跨度语义移植**(函数变了/没了):不支持,skill 明确提示"目标函数在 v20 不存在/结构差异大,需人工"。确定性工具只能给线索,真正的语义移植是 opencode 推理 + 人工确认。
- **批量自动 backport**:单 fix 人在环(skill 跑到 generate v20 patch 交人验),不做全自动批量(同 patch-report 的 apply 封顶 + 真机 oracle 原则)。

## 触发条件(什么时候启动)

用户拍板"稍后做"。触发 = 用户说做 / 或 recall 验证等手头事收尾后。启动时先进 plan mode 把「判 bug」方案(上 A/B/C)调研 + 实测定,再设计 skill。

关联 [[upstream-merge-handoff]](merge_eval,同源场景) [[cross-version]] [[route3-cross-version-handoff]] [[pitfall-log]](踩坑#2 不重造) [[avoid-overengineering]] [[similar-bug-recall-roadmap]]。
