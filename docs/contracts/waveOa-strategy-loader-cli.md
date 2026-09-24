# Wave O-a 契约：用户策略热加载（O2）+ CLI 工具链（O3）+ 补齐通知事件（O4 尾巴）

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **O2 / O3 / O4** · Agent-Oa
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 一、O2 用户策略热加载

### 1.1 现状

`STRATEGY_REGISTRY`（`app/strategy/presets/__init__.py`）是硬编码的 16 个 preset。
用户想跑自己的策略，只能改源码重启。

### 1.2 ⚠️ 这是本契约最危险的部分，先说安全边界

**加载用户提供的 Python 文件 = 执行用户提供的代码。** 没有任何「沙箱校验」能真正
挡住恶意代码 —— `import os; os.system(...)` 可以藏在任意表达式里，AST 白名单也能被
`getattr(__builtins__, ...)` 绕过。

**因此本契约的立场是**：

1. **不承诺沙箱。** 模块 docstring 必须写明「加载 `user_data/strategies/` 下的文件
   等同于以服务进程的权限执行它；只放你自己写的或已审阅过的策略」。
   **不要写「沙箱校验」这种会给人虚假安全感的措辞。**
2. **默认关闭**：`settings.user_strategies_enabled = False`。
3. **不提供 HTTP 上传端点**。蓝图原文提到「策略上传端点」——
   本期**不做**：一个能上传即执行的 HTTP 端点等于远程代码执行漏洞。
   用户把文件放进目录即可，部署方自己控制那个目录的写权限。
4. 目录路径从 settings 读，**不接受请求参数**（否则又变成任意路径读取）。

### 1.3 设计

```python
# app/strategy/resolver.py（新文件）

def discover_strategies(root: Path) -> dict[str, type[StrategyBase]]:
    """扫描目录下的 .py，加载其中 StrategyBase 的子类。

    - 同名策略：用户策略**不覆盖** preset，改为报错并列出冲突名
      （静默覆盖会让「我明明跑的是 macd」变成一个谜）
    - 单个文件加载失败：记录并跳过，不影响其他文件
    - 加载失败的原因要能查（返回体带 errors 列表，不只是日志）
    """
```

`STRATEGY_REGISTRY` **保持不变**。新增一个合并视图函数
`available_strategies()` = preset + 用户策略，端点改用它。
这样 preset 的加载路径完全不受影响。

---

## 二、O3 CLI 工具链

```
python -m app.cli list-strategies
python -m app.cli backtest   --strategy double_ma --symbol AAPL --start ... --end ...
python -m app.cli download   --symbols AAPL,MSFT --market US --start ... --end ...
python -m app.cli new-strategy --name my_strategy     # 生成骨架文件
```

**四个务实的点**：

1. **用 stdlib `argparse`**，不要引入 typer/click（红线：不新增依赖）。
2. **CLI 是薄壳**：每个子命令都调既有的服务层函数，**不要复制业务逻辑**。
   `backtest` 调 `BacktestEngine`、`download` 调 M-a 的归档任务函数。
3. **CLI 需要 DB/Redis 的子命令要能优雅失败** —— 用户在没起 docker 的机器上跑
   `list-strategies` 不该看到一屏 traceback。连不上就给一句人话 + 退出码 1。
4. **退出码要对**：成功 0，用户输入错误 2，运行失败 1。脚本会依赖这个。

---

## 三、O4 补齐通知事件（尾巴）

V3 Wave A-c 已建好事件总线并加了 5 类（现共 12 类）。**只差 3 类**：

```python
RETRAIN_DONE = "retrain_done"          # M6 自适应再训练（本期无发射点，先留类型）
DATA_GAP = "data_gap"                  # M-a 的归档缺口检测
REBALANCE_EXECUTED = "rebalance_executed"   # V3 A-b 的再平衡执行
```

⚠️ 沿用 A-c 定的规矩：**新类型一律进 `IN_APP_ONLY_DEFAULT_EVENTS`**
（默认只走站内，Telegram/Webhook 需用户显式勾选），
且 `frontend/src/types/index.ts` 的 `NotifyEventType` 与
`NotifyChannelsSection.tsx` 的 `EVENT_LABELS` **必须同步补**，
否则 `tsc` 会因 `Record<NotifyEventType, string>` 缺 key 而红 —— 这两处是共享文件，
**在报告里给集成片段，不要自己改**。

发射点：`DATA_GAP` 接 M-a 的归档缺口检测；`REBALANCE_EXECUTED` 接 A-b 的 execute 端点。
`RETRAIN_DONE` 本期没有发射点，只留类型（M6 未做）。

---

## 四、验收

```
1. tests/regression 146 用例全绿
2. tests/test_strategy_resolver.py:
   - 发现并加载合法的用户策略
   - 与 preset 同名 → **报错并列出冲突名**，不静默覆盖
   - 单个文件语法错误 → 跳过该文件，其余照常加载，errors 里有记录
   - user_strategies_enabled=False 时 available_strategies() == STRATEGY_REGISTRY
     （逐键相等，证明默认路径未受影响）
3. tests/test_cli.py:
   - list-strategies 在无 DB/Redis 环境下正常输出（不抛 traceback）
   - 参数非法 → 退出码 2；运行失败 → 退出码 1；成功 → 0
   - new-strategy 生成的骨架文件能被 discover_strategies 加载
4. tests/test_notify_events.py 扩展：3 个新类型可派发且默认只走站内
5. ruff check app tests → All checks passed! · pytest -q 全绿
```

## 五、不做

- **策略上传 HTTP 端点**（见 §1.2 第 3 点，这是有意不做）
- 任何形式的沙箱/AST 白名单（见 §1.2 第 1 点，做不到就不要假装）
- M6 自适应再训练（L 复杂度，本轮不做，只留事件类型）
- CLI 的 hyperopt 子命令（参数空间在命令行上表达很别扭，留待有实际需求再说）
