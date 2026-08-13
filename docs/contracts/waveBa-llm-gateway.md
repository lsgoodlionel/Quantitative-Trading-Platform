# V3 Wave B-a 契约：LLM 网关（I0）+ 模型管理页

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **I0** · Agent-Ba
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 零、产品方向（用户已明确指定，不要自行改动）

1. **本地 Ollama 优先** —— 默认走本地模型，**访问地址可配置**
2. **预设主流厂商** —— 内置常见模型清单与密钥配置入口
3. **模型管理页统一配置** —— 一个页面配完所有 provider，而不是散在各处

这三条决定了架构：网关必须是**多 provider 可切换**的，且「无密钥也能用」
（Ollama 不需要 key）是第一等公民而非降级路径。

---

## 一、Provider 抽象

### 1.1 一个关键观察：大部分厂商是 OpenAI 兼容的

Ollama（`/v1/chat/completions`）、DeepSeek、Moonshot、Qwen/DashScope 兼容模式、
OpenAI 本身 —— 全部走同一套 OpenAI Chat Completions 协议。
**只有 Anthropic 用自己的格式。**

所以不要写 N 个 provider 类，写两个：

```
app/core/llm/
    base.py        # LLMProvider 抽象 + ChatMessage / ChatResponse / ToolSpec
    openai_compat.py  # 覆盖 Ollama / OpenAI / DeepSeek / Moonshot / Qwen …
    anthropic.py      # Anthropic Messages API
    registry.py       # provider 预设清单 + 运行期配置解析
    config_store.py   # Redis 读写（沿用券商配置的形态）
```

```python
@dataclass(frozen=True)
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None

@dataclass(frozen=True)
class ChatResponse:
    content: str
    tool_calls: list[ToolCall]
    model: str
    usage: dict[str, int]          # prompt/completion/total tokens（拿不到就留空）

class LLMProvider(ABC):
    @abstractmethod
    async def chat(
        self, messages: list[ChatMessage], *,
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        timeout: float = 60.0,
    ) -> ChatResponse: ...

    @abstractmethod
    async def health(self) -> ProviderHealth: ...
```

### 1.2 预设清单

```python
# app/core/llm/registry.py

PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        id="ollama", label="Ollama（本地）", kind="openai_compat",
        default_base_url="http://localhost:11434/v1",
        requires_key=False,
        suggested_models=("qwen2.5:14b", "llama3.1:8b", "deepseek-r1:14b"),
    ),
    ProviderPreset("openai",    "OpenAI",    "openai_compat",
                   "https://api.openai.com/v1", True,  ("gpt-4o", "gpt-4o-mini")),
    ProviderPreset("deepseek",  "DeepSeek",  "openai_compat",
                   "https://api.deepseek.com/v1", True, ("deepseek-chat", "deepseek-reasoner")),
    ProviderPreset("moonshot",  "Moonshot",  "openai_compat", ..., True,  (...)),
    ProviderPreset("dashscope", "通义千问",   "openai_compat", ..., True,  (...)),
    ProviderPreset("anthropic", "Anthropic", "anthropic",     ..., True,  (...)),
)
```

⚠️ **`suggested_models` 是建议不是白名单** —— 用户填任意模型名都要放行。
本地 Ollama 拉了什么模型只有用户自己知道，写死清单会挡住正常使用。
若 provider 支持列模型（Ollama 有 `/api/tags`），提供一个「拉取可用模型」的动作。

### 1.3 未配置时的行为

蓝图写「未配置时相关端点返 **501**」。照做，但要区分两种情况：

| 情况 | 状态码 | 原因 |
|---|---|---|
| 一个 provider 都没配 | **501 Not Implemented** | 功能未启用 |
| 配了但连不上 / key 无效 | **503 Service Unavailable** | 功能启用了但依赖挂了 |

把两者都报 501 会让「Ollama 没启动」看起来像「平台不支持 AI」。

---

## 二、配置存储

**沿用券商配置的形态**（`app/api/v1/endpoints/broker_config.py`）：
Redis hash，读取时用同款掩码（首 2 位 + 掩码 + 末 4 位）。

⚠️ **不要给 LLM 密钥单独加静态加密。** 项目的券商密钥（能动钱的那些）
目前也是明文存 Redis；只给 LLM 密钥加密会造成「这里是安全的」的错觉，
而真正高价值的凭证反而裸奔。静态加密是个横切关注点，应该单独立项、
一次覆盖所有凭证。**在模块 docstring 写明这一现状与理由。**

⚠️ **写入端点要 `Role.TRADER` 及以上**（与券商配置一致）；
**读取端点永远只回掩码**，完整 key 只在服务端内存里用。

---

## 三、API

```
GET    /api/v1/llm/providers          预设清单 + 各自配置状态（key 掩码 / 是否启用）
PUT    /api/v1/llm/providers/{id}     保存配置（base_url / api_key / default_model）
DELETE /api/v1/llm/providers/{id}
POST   /api/v1/llm/providers/{id}/test    连通性测试（真实发一次最小请求）
GET    /api/v1/llm/providers/{id}/models  拉可用模型（provider 支持才有）
GET    /api/v1/llm/active             当前生效的 provider 与模型
PUT    /api/v1/llm/active             切换
POST   /api/v1/llm/chat               统一对话入口（供 I1 Copilot 与 I4/I5 复用）
```

**`/test` 必须真实发一次请求**，不能只 ping base_url —— key 错、模型名错、
额度耗尽都只有真调一次才知道，而这三样正是用户最容易配错的。

---

## 四、前端：模型管理页

新页面 `/settings/models`（或 Settings 内的一个 Tab，二选一，在报告说明）：

- **provider 卡片列表**：Ollama 排第一且默认展开（它无需密钥、开箱即用）
- 每张卡：base_url 输入、api_key 输入（已配置时显示掩码，留空=不修改）、
  默认模型（下拉 + 可自由输入）、「测试连接」按钮 + 结果指示
- **顶部「当前生效」选择器**：选 provider + model，其余功能都用它
- 未配置任何 provider 时给明确引导：「安装 Ollama 后填入地址即可开始，无需密钥」

⚠️ **api_key 输入框留空 = 保持原值**，不是「清空」。清空要有独立的删除动作 ——
用户改 base_url 时不该被迫重新粘一遍密钥。

⚠️ 不要编辑 `App.tsx` / `Sidebar.tsx` / `types/index.ts`，在报告里给集成片段。

---

## 五、验收

```
1. tests/regression 146 用例全绿
2. tests/test_llm_gateway.py:
   - OpenAI 兼容 provider 的 chat 请求体与响应解析（用 mock HTTP）
   - Anthropic provider 的格式差异（system 独立字段、messages 结构）
   - tool_calls 解析（两种协议各一个用例）
   - 未配置任何 provider → chat 返回 501
   - 配置了但连不上 → 503（不是 501）
   - suggested_models 之外的模型名照样放行
3. tests/test_llm_config_api.py:
   - 保存后读取只回掩码，完整 key 不出现在任何响应里
   - api_key 传空字符串 = 保持原值；删除走 DELETE
   - 写入需要 TRADER 角色，viewer 得 403
4. 前端：模型管理页渲染、测试连接的三种结果态、留空不覆盖密钥
5. ruff check app tests → All checks passed! · 前端 lint/tsc/test/build 全过
6. pytest -q 全绿
```

## 六、不做

- **I1 Copilot**（独立契约，依赖本网关）
- 流式响应（SSE）—— Copilot 需要时再加，本期先把非流式跑通
- 凭证静态加密（横切关注点，见 §2）
- 本地模型的下载/管理（那是 Ollama 自己的事，不要在平台里重造）
- token 计费与配额（等有实际用量再说）
