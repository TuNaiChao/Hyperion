# 服务 · issue 解析(trigger parser)

> `services/trigger_parser/parser.py` —— 把 issue / bug 报告文档读成纯文本。给 bug-RCA、ingest 报告路用。

## 概览

bug-RCA 的"线索"(trigger)常来自 issue 文档(`.md` / `.txt` / `.pdf`)。本解析器只做一件事:**把文档读成纯文本**,不做 regex 抽符号、不做结构化猜测。

> [!NOTE]
> 早期版本曾尝试用 regex 从 issue 里抽符号当定位起点,事实证明是偏差源(模型照着错锚走偏)。现在只给纯文本,把"抽什么"交给 LLM / agent。

## 源码

| 文件 | 职责 |
|---|---|
| `services/trigger_parser/parser.py` | `parse_issue` + `IssueDoc` |

## API

```python
@dataclass
class IssueDoc:
    text: str          # 纯文本
    source: str        # 来源路径

def parse_issue(path) -> IssueDoc
    # .md/.txt 直读;.pdf 走 pypdf(扫描件返空 text)
```

## 流程

1. 按扩展名分流:`.md` / `.txt` → 直接 `read_text`;`.pdf` → `pypdf` 提取文本层。
2. 扫描件 PDF(无文本层)→ `text=""`(调用方据此降级,如改走 OCR 或换源)。
3. 返回 `IssueDoc(text, source)`。

## 配置

无配置。依赖 `pypdf`(已在 `uv.lock`)。

## 边界与限制

- **不做 regex 抽符号**(设计决策,见上)。
- 扫描件 PDF / 图片型 issue 返空文本 —— 本解析器不做 OCR。
- 不做任何语义解析,只读文本。

## 示例

```python
from hyperion.services.trigger_parser.parser import parse_issue

doc = parse_issue("example/demo2/issue.md")
print(doc.text[:200])
```

## See Also

- [../services/memory.md](../services/memory.md) §ingest — 报告路复用 `parse_issue`
- [../workflows/bug-rca.md](../workflows/bug-rca.md) — trigger 来源
