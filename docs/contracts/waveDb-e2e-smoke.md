# V3 Wave D-b 契约：Playwright E2E 冒烟（J5 下）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **J5**（E2E 覆盖 3 条 Playbook）· Agent-Db
> 状态：📋 待审阅
>
> **红线**：后端一行不改。`git diff backend/` 为空，backend pytest 保持全绿。

---

## 一、要做的事

引入 Playwright，冒烟覆盖 H4 已定义的三条 Playbook：

1. **单标的技术流**：登录 → 选标的 → 看行情 → 跑回测
2. **发现→组合流**：筛选 → 多选 → 组合优化 → 预览调仓
3. **因子研究流**：建公式 → 适应度 → 回测

```
frontend/
├── playwright.config.ts
├── e2e/
│   ├── fixtures/           登录态、API 桩
│   ├── playbook-technical.spec.ts
│   ├── playbook-portfolio.spec.ts
│   └── playbook-factor.spec.ts
```

`package.json` 加 `"test:e2e": "playwright test"`。

---

## 二、这一项最容易做废，四条硬要求

### 2.1 桩住后端，不要连真实服务

用 `page.route()` 拦截 `/api/v1/**` 返回固定夹具。理由：

- 冒烟测的是**前端流程能不能走通**，不是后端对不对（后端有 2480 个用例）
- 连真后端要起 Postgres + Redis + Celery，CI 里跑一次几分钟，
  而且会因为「今天没有行情数据」这类原因红——**那种红没人会去查，
  查几次之后所有人都开始忽略它，测试就死了**

⚠️ 夹具必须来自**真实响应的结构**。照着 TS 类型手写夹具，会写出后端根本
不会返回的形状，测试全绿而线上全崩。做法：从 `src/hooks/*.ts` 的类型
与 backend 的 Pydantic 响应模型逐一核对，并在夹具文件里注明来源。

### 2.2 断言要盯用户能看见的东西

断言可见文本、可点元素、URL 变化。**不要断言 CSS 类名或 DOM 结构** ——
那种测试在每次样式调整时都红，红的又不是真问题，最后只会被删掉。

### 2.3 不用 `waitForTimeout`

用 `expect(locator).toBeVisible()` 这类自动重试的断言，或
`waitForResponse`。写死的 sleep 在慢机器上必然间歇性失败，
而间歇性失败的测试等于没有测试。

### 2.4 CI 里必须能跑，但**先不进阻塞门禁**

加到 `.github/workflows/ci.yml`，**允许失败**（`continue-on-error: true`）
并上传 trace/截图。

理由：一套刚写的 E2E 必然有几轮不稳定期，直接设成阻塞会让所有人
第一时间学会点 "re-run"。先跑两周、稳定了再收紧 —— 在契约里写明
这是**暂时**的，并在 workflow 注释里写上收紧条件（连续 20 次绿）。

---

## 三、登录态

三条 Playbook 都要先登录。用 Playwright 的 `storageState` 复用登录态，
不要每个用例都点一遍登录表单 —— 但**保留一个单独的登录用例**覆盖登录本身，
否则登录坏了三条 Playbook 会一起红成一片，看不出根因在哪。

---

## 四、验收

```
1. 后端：git diff --stat backend/ 为空；backend pytest -q 全绿
2. 前端既有的 lint / tsc / vitest / build 全过（E2E 不得干扰 vitest：
   playwright 的 e2e/ 目录要排除在 vitest 的 include 之外，否则
   vitest 会去跑 playwright 用例并报一堆看不懂的错）
3. npx playwright test 全绿（本地，桩住后端）
4. 三条 Playbook 各一个 spec，外加一个独立的登录 spec
5. 全仓搜不到 waitForTimeout
6. CI 里 E2E job 存在、continue-on-error: true、上传 trace
7. 夹具文件注明每份数据对应的后端响应模型
```

## 五、不做

- 跨浏览器矩阵（先只跑 Chromium；Firefox/WebKit 等稳定后再加）
- 视觉回归截图对比（噪声大，需要基线管理，单列一项）
- 连真实后端的集成级 E2E（见 §2.1）
- 移动端视口
