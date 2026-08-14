# V3 Wave B-d 契约：命令面板 + 全局标的上下文（H3）+ Playbook（H4）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **H3 / H4** · Agent-Bd
> 依赖：**B-b 页面重组已合入**（9 主页 + 4 独立页已定）
> 状态：📋 待审阅
>
> **红线**：后端一行不改。`git diff backend/` 为空，backend pytest 保持全绿。

---

## 零、页面集（B-b 已定，直接用）

**9 主页**：`/` 仪表盘 · `/market` 行情 · `/screener` 发现 · `/research` 研究 ·
`/strategies` 策略 · `/backtest` 验证 · `/portfolio` 组合 · `/trading` 交易 · `/settings` 设置
**独立页**：`/risk` · `/alerts` · `/notifications` · `/lab` · `/settings/models`

⚠️ 各主页的 Tab 由 `?tab=` 驱动（B-b 的 `useUrlTab`）。命令面板跳转要能直达
**具体 Tab**，而不是只跳到页面默认 Tab —— 「去组合优化器」应该落在
`/portfolio?tab=optimizer`。

---

## 一、H3 命令面板

`⌘K` / `Ctrl+K` 唤起。三类条目：

| 类型 | 例 | 动作 |
|---|---|---|
| 页面 | 「组合优化器」 | 跳 `/portfolio?tab=optimizer` |
| 标的 | 「AAPL」 | 设为当前标的 + 跳行情 |
| 动作 | 「新建因子策略」「运行完整验证」 | 跳到对应页并预置状态 |

### 1.1 四个容易做错的点

1. **⌘K 不能抢输入框的焦点**。用户在搜索框里按 ⌘K 是想全选（macOS 习惯里
   ⌘A 才是全选，但输入态下劫持全局快捷键仍然烦人）。
   **在 `input` / `textarea` / `contenteditable` 内不触发**。
2. **`Escape` 要能关闭，且不能穿透**到底层页面的其他 Escape 处理。
3. **搜索要能匹配拼音/英文首字母**？—— **本期不做**。中文标签直接子串匹配即可，
   引入拼音库违反「不新增依赖」。在报告里如实说明这个限制。
4. **面板打开时锁滚动、关闭时恢复** —— 否则背景页面会跟着滚，
   而这是所有命令面板都会被吐槽的细节。

### 1.2 全局标的上下文

```tsx
// contexts/SymbolContext.tsx
const { symbol, market, setSymbol } = useSymbolContext()
```

- 初值从 URL 的 `?symbol=` 读（B-b 刚给 Market 补上了这个参数解析）
- 切换时**同步写回 URL**，保持「URL 即状态」的既有惯例
- 跨页面保持：从行情看 AAPL → 跳到验证页，symbol 应该已经填好

⚠️ **不要让全局上下文成为唯一真相**。URL 才是 —— 用户分享链接、刷新、
后退都只有 URL 在。上下文是 URL 的缓存与便捷读取层，不是替代品。

---

## 二、H4 Playbook

现有 `TradingWorkflow` 泛化成 3 条路径：

1. **单标的技术流**（已有）：选标的 → 看行情 → 回测 → 验证
2. **发现→组合流**：筛选 → 多选 → 组合优化 → 预览调仓 → 确认执行
3. **因子研究流**：建公式/选因子库条目 → 适应度 → 回测 → 注册为策略

每条 Playbook 是一串**步骤定义**（标题、说明、目标路由、完成判据）。

### 2.1 「完成判据」不要过度设计

蓝图写「Dashboard 按用户进度推荐下一步」。**本期用 localStorage 记录
「用户点过哪些步骤」即可**，不要去反查后端状态（「他是否真的跑过回测」）——
那需要一堆新端点，而且判错了比不判更让人困惑。

在 UI 上说清楚这是**引导**而不是**进度追踪**：步骤可以手动标记完成/重置。

### 2.2 复用而非重写

现有 `TradingWorkflow` 与 `useWorkflowStorage` 已经有一套存储与渲染，
**泛化它**（把硬编码的步骤抽成数据），不要另起一套并行实现。

---

## 三、验收

```
1. 后端：git diff --stat backend/ 为空；backend pytest -q 全绿
2. 前端 lint / tsc / test / build 全过
3. tests:
   - ⌘K 在 input/textarea 内**不**触发（各一个用例）
   - Escape 关闭面板
   - 页面条目跳转带上正确的 ?tab=
   - 标的上下文：setSymbol 后 URL 同步更新
   - 三条 Playbook 的步骤定义可渲染；标记完成/重置后 localStorage 持久化
4. 手动核对：面板打开时背景不滚动
```

## 四、不做

- 拼音/首字母搜索（需新依赖）
- 模糊搜索排序算法（子串匹配 + 按类型分组即可）
- 从后端反查 Playbook 真实进度（见 §2.1）
- 命令面板里执行写动作 —— 那是 Copilot 的边界（B-c），
  面板只做**导航与预置状态**，不产生订单
