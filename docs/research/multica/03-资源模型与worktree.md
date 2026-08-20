# 资源模型与 worktree 机制

> 源码锚点 `v0.4.31-2-g8c9b7503a`;file:line 相对 `multica/`。核心结论先行:**Multica 有两套互不相干的
> worktree 体系**(github_repo 的裸缓存体系 / local_directory 的用户仓体系),选型时不可混为一谈。

## 一、资源类型:只有两种,server 不碰文件系统

- API 边界硬校验(handler/project_resource.go:72-84):`github_repo` / `local_directory`,其他直接
  `unknown resource_type` 报错。迁移注释里规划过 notion_page/gdoc/url(file)—— 均未落地
  (migrations/065_project_resources.up.sql:1-4)。
- 存储:单张多态表 `project_resource(id, project_id, workspace_id, resource_type, resource_ref JSONB,
  label, position, …, UNIQUE(project_id, resource_type, resource_ref))`;`resource_type` 建后不可改
  (project_resource.go:59-62)。
- ref 形状:
  - `github_repo`:`{url, default_branch_hint?, ref?}`,URL 校验 http/https/ssh/git/scp 形
    (project_resource.go:411-446);
  - `local_directory`:`{local_path, daemon_id, label?, execution_mode?}`。**server 侧只做拼写级校验**
    (绝对路径样子、daemon_id 非空、mode 枚举;「a typo guard, not a filesystem check」,:269-298)——
    真正的文件系统校验全在 daemon 侧任务时做。
- 「一个项目每台 daemon 至多挂一个 local_directory」是**应用层**约束(findLocalDirectoryConflict,
  :767-799,注释明说 DB UNIQUE 挡不住改个 label 的重复);daemon 领任务时再核验,双匹配硬错
  (local_directory.go:131-141)。

## 二、local_directory 的路径防线(daemon 侧)

`validateLocalPath`(local_directory.go:207-257):

1. 非空、绝对路径、存在且是目录;`.multica-rwcheck-*` 探针文件确认可写(:369-381)。
2. **字面黑名单**(等值匹配,非前缀):盘符根、`/`、`/Users`、`/Users/Shared`、`/home`、`/root`、`/var`、
   `/etc`、`/tmp`、`/usr`、`/opt`(POSIX;:357-362)+ `$HOME 本身** —— 「/Users/<user>/code/proj 应当
   放行」,只有家目录本身被拒。
3. **符号链接感知复查**(isBlacklistedRealPath,:293-321):黑名单项逐个 EvalSymlinks 再比 —— macOS
   `/private/tmp`、用户自建 `~/home-link -> /Users/me` 一律 fail-closed。

注意:黑名单没含 `/mnt` `/media` `/proc` `/dev`(等值语义下的漏网,接入时别往这些地方挂)。

## 三、in_place(默认,Direct/队列模式)

- 旧资源无 execution_mode 一律按 in_place(:113-127)。
- **串行锁 = 进程内按真实路径的互斥**:锁键是 `filepath.EvalSymlinks` 后的 real path ——
  `/Users/u/proj` 与指向它的 symlink 任务合并成一把锁(:44-51);锁持有范围 = 整个任务
  「claim → 写上下文 → agent 执行 → 上报」(:398-404)。
- 争用:后来者状态翻 `waiting_local_directory` + `wait_reason="local_directory <path> (held by task
  <short-id>)"`(daemon.go:5368-5403;migration 109 加列;事件 pkg/protocol/events.go:37);等待期 5s
  取消监视 + prepare 租约续期;锁获取可被 daemon 停机打断。等待数进健康指标
  (health.go:229 ResourceWaitTaskCount)。
- 未知 execution_mode → 任务失败而非静默 in_place(:71-85,「refusing to run in place, since that
  would modify a directory the resource asked to isolate」);squad-leader 任务永不绑 local_directory
  (:91-96)。
- workdir 就是用户路径;envRoot 仍会建(装 output/logs/GC 元数据)但 **GC 永不整删**,只做工件级清理
  留取证现场(execenv.go:238-247,gc.go:400-431)。

## 四、worktree(Parallel 模式)—— 用户仓的一次性 worktree

实现:`server/internal/daemon/execenv/local_worktree.go`。

- **来源是用户自己的仓,不是 .repos 缓存**:`git rev-parse --show-toplevel` 定根;非 git 仓 → 致命错
  「is not a git repository… switch the resource back to in_place」(:573-587);无 commit
  (`rev-parse --verify HEAD` 失败)同样致命(:193-197)。
- worktree 建在 `{envRoot}/worktree`;agent cwd = worktree 内对应资源子目录的等价深度(:150-167, 225)。
- 分支名 `agent/{agent 名净化的}/{task 短 id 8 字符}`(:216);重派冲突加 `-<unix>` 后缀重试一次。
- **脏态回放**(「agent 看到的和你看到的一致」,:16-33):
  1. 已跟踪改动:`git stash create`(只造 commit 对象、不动用户索引/工作树)→ 在 worktree 里
     `stash apply` 成未暂存改动;
  2. 未跟踪文件:逐个拷贝,**上限 2000 文件 / 200 MiB,且 fail-closed 不截断** —— 超限/遇到未跟踪
     symlink 整个 prepare 失败并报可操作错误(:52-53, 264-267, 609-683);
  3. 基线 commit `"chore(agent): baseline — uncommitted work from the local directory"` 把用户 WIP 与
     agent 改动分开 —— 只读任务在脏仓上跑完不会产出分支。
- **Finalize**(:322-419):收尾把遗留改动 `git add -A` + `commit --no-verify`(注释明说 --no-verify
  **不**禁 commit.gpgSign);**commit 失败(gpgSign 无密钥/磁盘满/丢引用锁)→ 故意保留 worktree 并让
  任务失败**,错误信息带 `git worktree list` 找回指引(:377-390);只读结果删分支;成功分支留在用户仓
  里当交付物(「The branch is deliberately left alone」,:521-523)。sidecar 清理失败 → Abort,不 commit。
- 并发:worktree 任务**跳过整任务路径锁**(daemon.go:5332-5335)但快照阶段短暂持锁(同一路径可能被
  别的项目 in_place 挂着);每仓 gitRootLocks 串行化 worktree add/remove/prune;陈旧注册 `git worktree
  prune` 自愈。

## 五、github_repo 资源与 `multica repo checkout`(+#2925 全解)

- github_repo 任务的 workdir **默认为空**(见 02 §9),agent 按需 `multica repo checkout <url>`:
  agent 面 CLI,要求任务 env(localhost 端点 + 任务 token 鉴权,workdir 必须任务所有),cwd 即检出目标,
  503+Retry-After 退避重试(cmd/multica/cmd_repo.go:335-430)。
- **裸缓存**:`{WorkspacesRoot}/.repos/{workspaceID}/{host+org+repo.git}`(repocache/cache.go:353-413);
  检出建在 `{workdir}/{repoName}`,分支 `agent/{agent}/{短 task}`(:833);复用检出 `reset --hard +
  clean -fd` 重开分支(:1422-1452);可选 prepare-commit-msg 加 Co-authored-by 钩子。
- **#2925 事故全解**:Codex CLI 的 workspace-write 沙箱(Landlock)把 workdir 之外的一切判只读,而 linked
  worktree 的真 gitdir 在 .repos 缓存里 → `git add`/`commit` 全挂。Multica 的修复是
  `WorktreeParams.IsolatedGitMetadata`(cache.go:722-733):改用 `git clone --local --no-checkout`
  建「.git 在 workdir 内」的本地 clone(注释原文点名 #2925 与 #6449),按平台仅对 codex 开
  (`MULTICA_REPO_CHECKOUT_MODE=isolated`,daemon.go:95-119)。**与 opencode 无关**(opencode 任务不设
  此变量、本身无沙箱)。
- `.repos` 维护(GC):worktree prune、删无 worktree 挂靠的 agent/* 分支、30 天 `reflog expire +
  gc --prune=30d`(无活动任务时)、逐出需 TTL 到期 + 无观察中工作区 + 零链接 worktree(gc.go:1000-1348)。

## 六、能力门(worktree 模式的四层闸)

1. **保存时**:daemon 最新注册行必须广告 `local-worktree-v1` 能力,否则 422
   `daemon_version_unsupported`(project_resource.go:161-230;取最新行是因为注销会留旧行)。
2. **领取时**:claim 请求头 `X-Client-Capabilities` 无此能力且任务钉在自己身上 → 任务**取消**(终态,
   不重排 —— 重排只会再发给同一台旧 runtime,handler/daemon.go:3002-3106;动机事故 MUL-5707:旧版
   daemon 「sailed through the floor and ran two tasks in the user's own directory」)。
3. **任务时** execution_mode 枚举校验;4. **任务时** 仓存在 + ≥1 commit。

`MinLocalWorktreeCLIVersion = "0.4.24"` 只是展示用,真正闸门是能力头(pkg/agent/version.go:39-53)。

## 七、对 RootRecall 选型的含义

- **bug 工作目录场景 → local_directory + in_place**,与 02 §9 的推荐一致:workdir 即用户目录,无
  worktree、无 .repos 参与,RootRecall 自己的 ephemeral checkout(`repo checkout` CLI)完全接管代码
  生命周期 —— 两套 worktree 体系都不碰。
- 不建议把 bluez 仓挂成 github_repo 资源:那会引入 .repos 缓存 + agent/* 分支 + GC 三套陌生生命周期,
  与 RootRecall 注册表/baseline/ephemeral 三角色模型重叠且互不知晓(同病灶:多子系统共享实体无登记处)。
- in_place 串行是**按目录**的:不同 bug 目录天然并行;同一目录多 issue 排队(waiting_local_directory
  可见),符合预期。
- 若哪天要把某个 git 仓工作副本交给 Multica 隔离(worktree 模式),记住:未跟踪文件 >2000/200MiB/
  含 symlink 会 fail-closed;gpgSign 配置会让任务失败但保留 worktree。
