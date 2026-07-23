## src/hyprion/platform/sandbox/base.py
* @dataclass(frozen=True) 的三句总结：
  1. 锁定只读：属性实例化后不可修改，强行赋值会直接报错，能有效防止核心配置（如路径映射）被篡改。
  2. 支持哈希：自动生成 __hash__，实例可作为字典（dict）的 Key 或存进集合（Set），方便缓存与去重。
  3. 正确修改法：切忌直接改属性，必须用 dataclasses.replace(old_obj, 属性名=新值) 克隆生成新对象。

* ABC + @abstractmethod 四句总结：
  1. ABC 是“图纸”：不能被直接实例化（不能写 s = Sandbox()），它只用来规定“子类必须长什么样”。
  2. @abstractmethod 是“强制任务”：凡是带这个装饰器的方法，子类必须全部实现。如果你写了 DockerSandbox(Sandbox) 却没写 read_file，Python 会直接报错，防止你漏写核心功能上线。
  3. 核心价值（解耦和多态）：你的 Agent 工具层只跟抽象接口打交道（sandbox.execute_command()）。它不需要知道当前跑的是本地（P0）还是 Docker（P6），底层实现彻底隔离，想换沙箱后端随时换。
  4. 兜底机制：这种设计保证了所有沙箱实现类的行为绝对统一，避免因为某个沙箱漏写了 grep 或 list_dir，导致 Agent 在不同环境下行为不一致甚至崩溃。

* ```
  def glob(self, path: str, pattern: str, *, max_result: int = 200) -> list[str]:
  # 方法签名里的 *:它之后的参数(max_results)必须用关键字传,防止调用方按位置误填
  ```



## src/hyprion/platform/sandbox/env_policy.py

* `frozenset` 的核心作用（3句总结）：
  1. **绝对的“只读常量”**：它是**不可变集合**。一旦定义，里面的元素**永远不能增删改**。如果代码里试图 `_BLOCKED_EXACT_NAMES.add("NEW_KEY")`，Python 会直接抛出异常强制中断，防止这个**安全黑名单**被意外篡改。
  2. **支持哈希（可当 Key）**：因为不可变，它是**可哈希（Hashable）**的。这意味着它不仅可以在集合里做 O(1) 的快速查找，还可以直接作为**字典的 Key** 使用。
  3. **天生的线程安全**：由于只能读不能写，在多线程并发环境下，`frozenset` 是不需要加锁的，读取绝对安全。

* `any(fnmatch.fnmatchcase(...) for pat in ...)` 核心作用：
  1. **模糊匹配**：`fnmatch.fnmatchcase` 支持通配符（`*` 匹配任意字符，`?` 匹配单个字符）。结合 `upper = name.upper()`，实现了**大小写不敏感的模糊匹配**（即使别人写成 `OpenAI_Api_Key`，也能被 `*API*KEY*` 拦截）。
  2. **惰性生成器**：`for pat in _SECRET_NAME_PATTERNS` 是生成器表达式，**不会一次性生成列表**，内存极其友好。
  3. **短路中断（性能极高）**：`any()` 只要在迭代中**遇到第一个匹配成功的模式**，就会**立即返回 `True` 并停止后续检查**。如果黑名单有 100 个模式，前 3 个就命中了，后 97 个根本不会执行，效率极高。



## src/hyprion/platform/config.py

*  `model_config = ConfigDict(extra="allow")` 的作用

**一句话总结：允许配置文件中出现当前代码未预定义的额外字段，但不报错。**

**默认行为**：Pydantic 默认是 `extra="forbid"`。如果你在 YAML 里写了 `logging:` 或 `metadata:` 等字段，但 `AppConfig` 类里面没有定义这个属性，程序会直接报错崩掉。

**你的配置（`extra="allow"`）的优势**：**赋予配置极强的向前兼容性**。你以后想在 YAML 里加新的自定义配置段（如实验性开关、元数据），完全不需要修改 `AppConfig` 类，也能顺畅解析。这些未定义的字段会被 Pydantic 自动收集到 `model.__pydantic_extra__` 字典里。

* ```
  # YAML 会把"键下面只有注释"的空段(如 config.yaml 现在的 tools:)解析成 None;
  # 而 pydantic 把显式 None 当成"有值"而非"用默认值",会校验失败。
  # 这里在赋值前把 None 强转成空集合,让空段也能正常解析。
  @field_validator("models", "tools", mode="before")
  @classmethod
  	def _coerce_none_to_list(cls, v: Any) -> Any:
          return v if v is not None else []

* `from __future__ import annotations` 的核心作用：
  1. **解决“前向引用”（自引用）问题**：
     - **不用它时**：`class AppConfig(BaseModel):` 内部写了 `models: list[ModelConfig]`。如果 `ModelConfig` 这个类在代码里**写在 `AppConfig` 的后面**，解释器此时还不认识它，会直接报 `NameError`。
     - **用了它之后**：注解会**暂时被存成“字符串”**。Python 不会立刻去查找 `ModelConfig` 到底是啥，直到真正用到这个类型（比如运行时 Pydantic 做校验）才会解析，**彻底解决了类之间互相引用的死锁问题**。
  2. **提升加载性能**：如果类型标注里涉及复杂的泛型嵌套（比如 `list[dict[str, set[int]]]`），Python 解释器本来要在**导入模块那一刻**就生成这些类型对象。使用它之后，这些计算被推迟到**真正运行时**，大大加快了模块的启动速度。
  3. **兼容新旧语法**：允许你在旧版本（如 Python 3.8/3.9）中，直接写 `list[str]` 这种**现代风格**（免去 `from typing import List` 的麻烦），而不会报语法错误。



## src/hyprion/platform/sandbox/local.py

* **`threading.Thread(..., daemon=True)` 的作用（三句总结）**：

1. **启动新线程**：`target=self._run` 告诉 Python 在后台新开一个线程，去执行 `_run` 这个方法。
2. **守护标记（`daemon=True`）**：**这是灵魂设置。** 它把这个线程标记为“守护线程”。
3. **防卡死铁律**：**只要主程序（主线程）结束了，无论这个守护线程是否运行完毕，都会被 Python 强制咔嚓（强行终止）。** 主程序无需专门去等待它。

**为什么一定要加** `daemon=True`？

如果去掉 `daemon=True`，当子进程产生海量输出时，会发生如下**致命场景**：

1. 你的 Agent 执行了 `execute_command`，开启了子进程和这个后台读取线程（`_BoundedCapture`）。
2. 突然，**主程序因为某种原因（比如配置错误、网络异常）抛出错误，准备退出了**。
3. 但 `_BoundedCapture` 的读取线程是**非守护线程**。Python 进程的退出规则是：**只要有一个非守护线程没跑完，主进程就永远无法退出，会死死卡在后台一直读管道。**
4. 加上 `daemon=True` 后，只要主程序报错退出，这个读取线程会自动跟着“暴毙”，**进程能干净利落地退出，不会遗留垃圾进程**。

**实战避坑提示**（新手极易犯错）：

**加了 `daemon=True` 虽然省事，但它存在一个隐患：**

> 如果在 `_run` 里面正好写到了文件，且文件尚未 `flush()` 到磁盘，主程序突然退出，守护线程被强杀，会导致**文件内容丢失或损坏**。

**所以，在生产级的代码中，标准的操作流程必须是这样的：**

1. 开启线程：`start()` 
2. **确保主程序在正常退出前，必须调用 `self._thread.join()`**
3. `join()` 的作用是：**“主程序先不退出，必须等着这个后台线程彻底把管道抽干，存好数据，干干净净地死后，主程序才退出”**。



* `"".join(self._chunks)` 的用法（三句总结）：

1. **大白话**：`分隔符.join(列表)`，它把列表里的所有字符串元素**缝成一整根线**。这里分隔符是空字符串 `""`，意味着把元素**无缝拼接**在一起。
2. **性能王者（核心价值）**：在循环里用 `+=` 拼接字符串（如 `result += chunk`），每次都会开辟新内存复制旧数据，耗时呈平方级增长。用 `"".join()` 会在底层**一次性**计算好总长度，仅申请一次大内存并完成复制，速度极快且内存友好。
3. **避坑条件**：列表 `self._chunks` 里的**每一个元素都必须是 `str` 类型**。如果包含数字或字节（`bytes`），必须先强制转换成字符串，否则会报 `TypeError`。



## src/hyperion/tools/sandbox.py

```
@tool("bash", parse_docstring=True)
def bash_tool(description: str, command: str) -> str:
    """在沙箱里执行一条 bash 命令。

    长时间运行的任务(如启动服务)务必放到后台并重定向输出,例如:
    `your-command > server.log 2>&1 &`。
    命令默认在沙箱工作区里执行;超时会被终止进程组;输出有长度上限。

    Args:
        description: 用几个字说明为什么要跑这条命令(必填,先于 command 给出)。
        command: 要执行的 bash 命令。文件路径尽量用绝对路径。
    """
    return _sandbox().execute_command(command)
```

- `@tool(..., parse_docstring=True)`:docstring 的 `Args:` 段被自动解析成工具的 JSON schema——所以**注释即接口**,改参数要同步改 docstring。
- 首参 `description`:模型必须填(deer-flow 约定),代价是多吃点 token,换来每步操作有"理由"可追溯。



















