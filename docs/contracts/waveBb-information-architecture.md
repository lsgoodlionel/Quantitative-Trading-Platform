# V3 Wave B-b 契约：页面合并重组（H1）+ 大页面拆分（H6）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **H1 / H6** · Agent-Bb
> 状态：📋 待审阅
>
> **红线**：后端一行不改。`backend/tests/regression` 与全量 pytest 必须保持全绿
> —— 它们是「你没碰后端」的证据。

---

## 零、现状

前端 `src/pages/` 共 16 个顶层页面、9763 行。四个超标：

| 页面 | 行数 |
|---|---|
| `PortfolioOptimizer.tsx` | 1192 |
| `LiveStrategy.tsx` | 1063 |
| `AlgoLab.tsx` | 919 |
| `Market.tsx` | 857 |

问题不只是行数：功能按「后端端点」而非「用户任务」切分，
所以做一件事要在三个页面之间跳。

---

## 一、目标信息架构（9 页）

```
仪表盘   Dashboard
市场     Market       ← Market + MarketEvents
发现     Screener
研究     Research     ← FactorAnalysis + AlgoLab
策略     Strategies
验证     Backtest     （H2 已重构完，本期不动）
组合     Portfolio    ← Portfolio + PortfolioOptimizer
交易     Trading      ← LiveStrategy + Orders
设置     Settings     （含模型管理，见 B-a）
```

外加已有的独立页：通知中心、投研产物库、风险、登录。

---

## 二、三条硬约束

### 2.1 合并是「加 Tab」，不是「重写内容」

被合并的页面内容**原样搬进 Tab**，不要顺手重构里面的业务逻辑。
这一条是本契约能安全落地的前提 —— 一次 PR 里同时改信息架构和业务逻辑，
出了问题无法二分定位。

**内容重构留到后续，本期只动组织方式。**

### 2.2 旧路径必须重定向，不能 404

用户有书签、有分享出去的链接、浏览器有历史记录。

```tsx
<Route path="/market-events" element={<Navigate to="/market?tab=events" replace />} />
<Route path="/portfolio-optimizer" element={<Navigate to="/portfolio?tab=optimizer" replace />} />
<Route path="/algolab" element={<Navigate to="/research?tab=ml" replace />} />
```

⚠️ **`replace` 是必需的** —— 不加的话用户按「后退」会被弹回旧路径再被重定向，
形成一个退不出去的循环。

### 2.3 Tab 状态进 URL

`?tab=xxx`，刷新后停在原 Tab、可分享、可后退。项目已有「URL 即状态」的惯例
（Screener 的 symbols、Backtest 的共享配置都这么做）。

⚠️ **合并后各 Tab 原有的 URL 参数不能丢**。比如 Market 的 `?symbol=AAPL`
在合并后必须继续有效 —— 否则所有指向个股的链接全废。

---

## 三、H6 拆分

四个超标页面拆到 **< 800 行**。拆分方式：

- 按 Tab 拆成 `pages/<page>/<Tab>.tsx`
- 共享的表单/卡片提到 `pages/<page>/components/`
- **纯展示的子块优先拆**，带状态的容器留在主文件

⚠️ **拆分不是搬运行数**。拆完每个文件要能独立读懂：
一个只有 `props` 转发的 200 行文件不算拆分成功。
若某个 Tab 本身就超 800 行，说明它内部还有更小的边界，继续拆。

⚠️ **不要为了达标而把类型定义/常量单独拆成文件** —— 那只是把行数挪走。

---

## 四、验收

```
1. 后端：git diff 对 backend/ 为空（这是「没碰后端」的证据）
   backend pytest -q 全绿（当前 2071 passed）
2. 前端 lint / tsc / test / build 全过
3. tests（前端）:
   - 每条旧路径都重定向到新路径且带 replace
   - Tab 状态进 URL：切 Tab 后 URL 变、刷新后停在原 Tab
   - 合并前各页原有的 URL 参数在合并后仍生效
     （至少覆盖 Market 的 ?symbol= 与 Screener 送来的 ?symbols=）
4. 所有页面文件 < 800 行（用脚本断言，别靠肉眼）
5. 手动核对：9 页导航可达，无死链
```

## 五、不做

- **任何后端改动**
- Backtest 页（H2 刚重构完，再动一次徒增风险）
- 命令面板（H3，独立契约，依赖本期定下的最终页面集）
- Playbook（H4，同上）
- 视觉设计翻新 —— 本期是信息架构，不是改皮肤
