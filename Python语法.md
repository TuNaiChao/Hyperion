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



## src/hyperion/services/code_index/parser.py

*  `@dataclass(frozen=True)` 核心作用（三句笔记）
  1. **自动省力气**：不用手写 `__init__`、`__repr__` 和 `__eq__`，Python 自动帮你把这些基础代码全生成好。
  2. **绝对只读**：对象一旦创建（比如 `s = Symbol(name="run", ...)`），**它的所有属性终生无法修改**。强行赋值 `s.name = "new"` 会直接报错。
  3. **支持哈希（最核心）**：因为属性不可变，这个对象天然成为“可哈希（Hashable）”的，**可以放心拿去当字典（dict）的 Key**，或者塞进集合（set）里面去重。

* `@dataclass` 的核心作用（三句大白话）
  1. **省去写 `__init__` 的体力活**：以前你定义一个包含 `name` 和 `age` 的类，必须手写 `def __init__(self, name, age): self.name = name; self.age = age`。用了 `@dataclass`，**你只需声明 `name: str; age: int`，Python 就自动帮你把构造函数写好了**。
  2. **打印时自动生成“漂亮格式”**：如果你直接 `print(对象)`，以前会打印出 `<__main__.Symbol object at 0x...>`（机器码，人类看不懂）。用了 `@dataclass`，打印时会自动变成 `Symbol(name='run', file='a.py')`，**非常方便你边调试边看数据**。
  3. **自动帮你实现“相等比较”**：以前你要比较两个对象是否内容一模一样，必须手写 `__eq__` 方法。用了 `@dataclass`，直接写 `symbol1 == symbol2` 就能自动按字段内容比对，不用再操心底层内存地址。



## src/hyperion/services/code_index/chunker.py

* BM25算法

BM25 是一种基于概率模型的**字面关键词匹配排序算法**，专为高效检索而设计。它的打分逻辑包含三个核心要素：词在文档中**出现次数越多分越高**、词在所有文档中**越罕见权重越高**、以及**文档越长会对得分进行惩罚**（防止长文靠字数作弊）。

它最大的优点是**速度极快、完全离线且无需 GPU**，但在语义理解上存在硬伤——**只懂字面匹配，不懂同义语义**（例如搜“苹果”绝不会命中“iPhone”）。

因此在现代 Agent 检索链路中，BM25 只负责**“极速一级海选（粗筛）”**，必须与**向量检索（语义召回）**以及**重排序模型（精排）**结合使用。在工程落地时，代码标识符（如 `wpa_supplicant`）必须**提前拆分成词干**喂给它；为了提升召回精度，通常会对**符号名和文档注释进行词频加权**，同时务必剔除 `def`、`self` 这类高频无义的停用词。



## src/hyperion/services/code_index/embed.py

`@property` 的核心作用用三句话就能概括：

1. **优雅伪装**：把“方法”变成“属性”来调用，省去加括号 `()` 的麻烦，代码更直观。
2. **保护配置**：强制字段“只读”（如 `dim` 和 `fingerprint`），防止外部意外篡改导致表结构错乱。
3. **动态计算**：支持在内部写逻辑（如根据远端 API 动态获取向量维度），同时配合 `Protocol` 能强制子类必须实现此属性。



## src/hyperion/services/code_index/store.py

* @staticmethod：**"放在类里面、但不碰实例(self)的工具函数"**。

Python 类里三种方法:

| 写法            | 第一个参数   | 能用啥                              |
| --------------- | ------------ | ----------------------------------- |
| 普通方法        | `self`(实例) | 能读写实例的数据(self._tables 等)   |
| `@classmethod`  | `cls`(类)    | 能访问类本身(做工厂方法)            |
| `@staticmethod` | **啥都不要** | 就是个普通函数,只是逻辑上挂在类名下 |

```python
@staticmethod
def _create_indexes(tbl: Any) -> None:
    from lancedb.index import BTree, FTS
    tbl.create_index("id", config=BTree())
    ...
```

它只需要 `tbl` 这个参数干活,**不需要 `self`**(不碰实例的 `_tables` 缓存等)。为什么还放类里?因为它逻辑上只服务 `LanceDBStore`(只有它建索引),放类下命名清晰(`LanceDBStore._create_indexes`)。用 `@staticmethod` 就是明确告诉读代码的人:"这方法不依赖实例状态,给个 tbl 就能跑"。

- **类比**:类是个工具箱。普通方法是用工具箱里**材料**干活(要 `self`);`@staticmethod` 是工具箱里一个**独立工具**,你递东西给它它就干活,不碰工具箱里的东西。
- 不加 `@staticmethod` 会怎样?那它第一个参数就得是 `self`,调用时 Python 会自动把实例塞进去,但你函数体根本不用 `self`——浪费且误导。加 `@staticmethod` 就免了 `self`,调用 `LanceDBStore._create_indexes(tbl)` 或 `self._create_indexes(tbl)` 都行,都只传 `tbl`。

> 一句话:**`@staticmethod` = "这个方法不需要 self,是个挂在类名下的纯函数"**。



##  src/hyperion/tools/code_nav.py

类型报错:`无法访问类「Sandbox」的属性「workspace」`

> 场景:`code_nav.py` 的 `_workspace()` 里写 `get_sandbox_provider().get_sandbox().workspace`,Pylance 报红。

1. 一句话总结

`get_sandbox()` 的**返回类型标注是抽象基类 `Sandbox`**,而 `workspace` 只在**子类 `LocalSandbox`** 上才有;类型检查器只认"标注",不认"实际对象",所以报"属性未知"。**运行时不报错**,因为实际返回的就是 `LocalSandbox`。

2. 前因:为什么会这样

涉及的三层类(代码关系)

```
Sandbox            ← 抽象基类(ABC),base.py。只有 @abstractmethod
  ↑ 继承
LocalSandbox       ← 具体实现,local.py。__init__ 里 self.workspace = ...
```

- **`Sandbox`(抽象基类)** = 一份**「岗位招聘启事」**:只规定"干这岗必须会 `execute_command`/`read_file`/`grep`……"(那些 `@abstractmethod`)。**启事上没写**"这人带一个叫 `workspace` 的口袋"。
- **`LocalSandbox`(具体实现)** = **真正上岗的人**,身上**确实**有个 `workspace` 口袋(`__init__` 里 `self.workspace = workspace` 装进去)。
- **`get_sandbox()`** 的函数签名是 `def get_sandbox(self) -> Sandbox`——返回类型标的是**抽象基类**,意思是"我只按招聘启事保证他会那些技能"。

报错链条

`get_sandbox().workspace` → 检查器看 `get_sandbox()` 的返回类型 = `Sandbox` → 翻 `Sandbox` 的"启事",找不到 `workspace` → 报:**"属性未知"**。

3. 后果:静态报错 ≠ 运行报错

|                       | 看什么                            | 结果            |
| --------------------- | --------------------------------- | --------------- |
| **静态检查(Pylance)** | 纸面**标注**(返回类型 `Sandbox`)  | ❌ 报红          |
| **运行时**            | 实际**对象**(`LocalSandbox` 实例) | ✅ 正常,口袋真在 |

> **关键认知:静态检查看「说明书」,运行时看「实物」。说明书没写,静态就报警——哪怕实物真有。**
>
> 所以这条报错**不会让程序崩**,但会:(a) IDE 满眼红线、(b) 挡住"类型干净"的标准、(c) 若用 `get_sandbox_provider` 仅为拿 workspace,改完后这个 import 还会变 ruff 未使用导入(F401)。

4. 两种修法对比

✅ 方案 A(采用):从 config 读,不摸实例的口袋

```python
# 改前
from hyperion.platform.sandbox import get_sandbox_provider
def _workspace() -> Path:
    return Path(get_sandbox_provider().get_sandbox().workspace)  # ← 报红

# 改后
from hyperion.platform.config import get_app_config
def _workspace() -> Path:
    return Path(get_app_config().sandbox.workspace)              # ← 干净
```

**为什么值一样**:`provider.py` 造 `LocalSandbox` 时,本来就是把 `cfg.sandbox.workspace` 塞进口袋的——读 config 拿到的是**同一个值**。

❌ 方案 B(否决):在抽象基类补一条 `workspace: Path`

```python
class Sandbox(ABC):
    workspace: Path   # 给基类加个声明
```

**为什么否决**:这样会把"沙箱一定有个本地工作区口袋"这个**本地假设**写进**通用契约**。将来 P6 的 Docker 沙箱别扭——它的"口袋"是容器里的虚拟路径(`/mnt/user-data`),跟宿主真实路径不是一回事(deer-flow 专门搞 `PathMapping` 虚拟路径就是为了这个)。**启事上写死"有个本地 workspace"反而碍事**。

5. 提炼的知识点

1. **抽象基类(ABC)** 是"契约/招聘启事",只规定子类**必须实现什么**;子类可以有自己的额外属性,但那是子类的私事,基类不背书。
2. **函数返回类型标注** 决定了调用方"能看到什么"。标 `-> Sandbox`,调用方就只能看到 `Sandbox` 契约里的东西,**哪怕实际返回的是子类**。
3. **静态 vs 运行时**:类型检查器只读标注(静态),不跑代码;所以"静态报警、运行正常"是常见现象,不能因为能跑就无视红线。
4. **修类型问题的两条路**:① 改标注/加声明让契约诚实(方案 B);② 换一条不依赖该属性的路(方案 A)。**选哪条要看语义**——这里 `workspace` 本质是"配置层的宿主路径",不该赖在沙箱实例身上,所以 A 更准。
5. **依赖方向**:工具层(`tools/`)拿工作区,应走**配置层**(`platform.config`),而不是去抠**沙箱实例**的内部属性——这也符合"配置是单一真相源"。



# 项目框架

### **src/hyperion/services/code_index/parser.py**：拿着代码文件，用 Tree-sitter 解析出**符号卡片（Symbol）**。

`Symbol` 是一个不可变的数据类（`frozen=True`）。它**不包含**整个函数的代码正文，只包含**“定位元数据”**，相当于一张只写了坐标和姓名的卡片：

- **身份ID**：`name`（函数名）、`qualified_name`（带类的全名，如 `Agent.run`）。
- **地址**：`file`（在哪个文件）、`start_line` 和 `end_line`（起止行号）。
- **特征**：`kind`（是函数还是类）、`signature`（参数长什么样，如 `(self, question)`）、`docstring`（功能描述）。
- **作用**：卡片虽小，但包含了该符号在物理世界的**绝对坐标**。

### **src/hyperion/services/code_index/chunker.py**：

拿到 `Symbol` 列表，做两件事：

- **去代码正文**：根据 `Symbol` 里的 `start_line` 和 `end_line`，去源码里把整段**代码正文**切下来。
- **做检索预处理**：根据 `Symbol` 里的 `docstring` 和函数体内标识符，生成给 BM25 用的**词袋（fts_text）**。

最后把“代码正文”和“词袋”打包成最终的业务单元——**CodeChunk**，下一步存入向量数据库。

### **src/hyperion/services/code_index/embed.py**:

`embedder.embed_chunks(chunks)` 接收上述 Chunk，拼接元数据注释头，调用远端 API（如阿里 DashScope）或本地模型（sentence-transformers）→ 产出 **`np.ndarray`（(N, dim) 的向量矩阵）**。

1. **`fingerprint`（模型指纹）联动“全量重索引”**：
   你看到 P1.2 里每个 Embedder 都有 `fingerprint` 属性。`index.py`（索引构建器）在启动时，会读取这个指纹，并写入索引元数据文件。**如果明天你换了模型（比如从阿里云切到本地 Qwen3），`fingerprint` 变了，`index.py` 就会自动清空 LanceDB 表，强制重新执行 P1.0 -> P1.1 -> P1.2**，防止向量空间不一致导致检索出垃圾。
2. **`expand_chunk_text` 打破了“AI 不懂代码出处”的瓶颈**：
   普通 RAG 只是把代码正文投进模型，模型不知道这行代码属于哪个文件。P1.2 这一步拿着 P1.0 和 P1.1 给的元数据，用代码注释的形式“喂”进嵌入模型，让模型在生成向量时，把“文件的命名空间”和“代码的功能类型”一起考虑进去。

### **src/hyperion/services/code_index/store.py**：

1. FTS = Full-Text Search(全文检索)

**就是"按词搜全文"**。你数据库里存了一堆文本(我们存的是 `fts_text` 词袋),想搜 "disconnect",FTS 引擎能快速找出所有含这个词的文本,并按相关度排序。

- 我们用的是 LanceDB 内嵌的 **Tantivy**(Rust 写的全文检索引擎)+ **BM25** 排序算法(BM25 = "Best Matching 25",工业标准的"这个词在这篇文档里有多重要"打分公式,词频越高、越罕见,分越高)。
- **类比**:Ctrl+F 是找精确子串;FTS 是"聪明的 Ctrl+F"——按词索引、按相关度排序、能处理大小写/词形。像图书馆目录,把每本书的每个词都建了索引,你查词它秒给最相关的几本。
- **我们为什么需要它**:hybrid 检索有两条路——FTS(BM25)负责**词法命中**(函数名、宏名、错误码这类精确 token,如搜 `disconnect_cb` 直接命中),向量负责**语义命中**(搜"断连处理"也能命中 `disconnect_cb`)。两条路互补(见下面 RRF)。

2. RRF = Reciprocal Rank Fusion(倒数排名融合)

**把两条检索路的排名"融合"成一个综合排名的办法**。

问题:FTS 给一个排名(按 BM25 分),向量给另一个排名(按 cosine 相似度),怎么合并?**不能直接比分数**——BM25 分可能是 0~20,cosine 分是 0~1,量纲完全不同,直接加或平均是错的。RRF 的妙招:**只看第几名(排名),不看原始分数**:

```
综合分 = Σ 1/(k + 排名)        # k=60 是经验常数
```

排第 1 名 → 1/61,排第 50 名 → 1/110。两条路都把某个 chunk 排前面 → 它的综合分就高。

- **类比**:两个评委各给选手**排名**(不评分,只说第几)。RRF 就是把两位评委的排名综合——两位都把你排前面的选手,最终就靠前。k=60 是平滑常数,让第 1 名不至于独大。
- **我们代码里在哪**:`store.py` 的 `hybrid_search` 里 `.rerank(RRFReranker(K=60, ...))`——就是 LanceDB 把 BM25 和向量两路结果用 RRF 融合,返回带 `_relevance_score` 的候选。后面 `retrieval.py` 再用 cross-encoder 把这批候选精排到 top-5。

> 一句话:**FTS 和向量各搜一路,RRF 把两路排名合一路**。



### src/hyperion/services/code_index/retrieval.py

* 采用**Hybrid（混合检索）** 指的是**同时执行“全文检索（FTS）”和“向量检索（Vector）”，并将两者的结果进行融合（合并与重排）**的检索方式

 

### src/hyperion/services/code_index/eval/runner.py





经验教训：

1. 在委托opencode定位问题时，不要只提供一次prompt让agent一次性完成所有任务，会有概率不收敛，要分阶段逐步提供prompt
