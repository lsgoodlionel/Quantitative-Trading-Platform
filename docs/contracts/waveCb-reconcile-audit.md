# V3 Wave C-b 契约：实盘对账（G6）+ 多用户与审计落库（J3）

> 对应 [DEVPLAN_V3.md](../../DEVPLAN_V3.md) 的 **G6 / J3** · Agent-Cb
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 一、G6 实盘对账

### 1.1 现状

`RECONCILE_DIFF` 事件类型与 `emit_reconcile_diff` 发射器在 Wave A-c 就建好了，
**但没有任何东西调用它** —— 对账逻辑本身不存在。

### 1.2 设计

```python
# app/oms/reconcile.py（新文件）

@dataclass(frozen=True)
class PositionDiff:
    symbol: str
    local_qty: int
    broker_qty: int
    delta: int

@dataclass(frozen=True)
class ReconcileResult:
    market: Market
    checked_at: datetime
    position_diffs: tuple[PositionDiff, ...]
    cash_diff: float | None
    broker_reachable: bool

async def reconcile(market: Market, oms, gateway) -> ReconcileResult: ...
```

Celery 定时任务（沿用 `app/tasks/` 的 `asyncio.run()` 桥接约定）拉券商持仓与资金，
与本地 OMS 比对，有差异发 `RECONCILE_DIFF`。

### 1.3 四个必须做对的点

1. **券商不可达 ≠ 对账通过。** 拉不到数据时 `broker_reachable=False`，
   **不要产出一份「零差异」的报告** —— 那是最危险的假阳性：
   用户看到「对账正常」，实际上根本没对上账。
2. **无差异时不发通知**（沿用 O-a 的判断）：每次对账都响一下等于训练用户忽略它。
   但**券商不可达要发** —— 那是需要人介入的状态。
3. **本地 OMS 的持仓是内存态**，重启即失。对账要说清比的是「本进程记录的委托」
   而非「历史全量持仓」，否则重启后必然满屏差异。在 docstring 与返回体里写明。
4. **只读，绝不自动纠正。** 发现差异只报告，不尝试补单或改本地记录 ——
   自动纠正一个你还没搞懂原因的差异，是把小问题变成大事故的经典路径。

---

## 二、J3 多用户 + 审计落库

### 2.1 现状与风险

`app/api/v1/endpoints/auth.py:21` 的 `_BUILTIN_USERS` 是**硬编码的三个账户**
（admin/trader/viewer），密码 hash 写在源码里，注释自己写着
「生产环境应改用数据库查询」。

审计日志 (`app/core/audit.py`) 写 **Redis**，且失败时只 `logger.debug` 后吞掉。

### 2.2 用户落库

```
infra/init-db/05_users.sql     users 表（id/username/email/hashed_pw/role/is_active/created_at）
app/data/storage/users.py      UserStore（Postgres 原生 SQL，与既有仓储同风格）
```

**三条硬要求**：

1. **内置账户作为「首次启动种子」而非删除。** 空表时插入三个默认账户
   （沿用现有 hash），**并在日志里明确警告默认密码必须改**。
   直接删掉内置账户会让零配置启动失效，那是现有部署依赖的行为。
2. **密码哈希继续用 passlib/bcrypt**（已在依赖里），不要换算法。
3. **`role` 的取值必须与 `app/core/rbac.py` 的枚举一致**，
   落库时校验 —— 一个 role 写错的用户会以「谁都不是」的身份存在。

⚠️ **不做用户自助注册**。本期只做「管理员维护账户」：
`GET/POST/PUT/DELETE /api/v1/users`，全部要求 `Role.ADMIN`。
开放注册涉及邮箱验证、限流、防滥用，那是另一个量级的工作。

### 2.3 审计落库

```
infra/init-db/06_audit_log.sql
app/data/storage/audit_log.py
```

**两条**：

1. **审计写失败不能吞掉。** 现在是 `logger.debug` 后继续 —— 审计的意义就是
   「出事后能查」，静默丢失等于没有。改为 `logger.error` 并计数；
   **但仍然不阻断主流程**（审计挂了不该让下单失败）。这个取舍要写进 docstring。
2. **Redis 保留为快速查询层**，Postgres 为持久层。双写，读优先走 Redis。
   若判断双写复杂度不值，可以只落 Postgres 并把 Redis 那套删掉 ——
   **在报告里说明选了哪种**。

⚠️ **审计记录不可变**：只有 INSERT 与 SELECT，不提供 UPDATE/DELETE 端点。

---

## 三、验收

```
1. tests/regression 146 用例全绿
2. tests/test_reconcile.py:
   - 有差异 → 发 RECONCILE_DIFF 且 diff 内容正确
   - 无差异 → **不发通知**
   - 券商不可达 → broker_reachable=False 且**发通知**，
     且 position_diffs 为空**不**被解读为「对账通过」
   - 对账不产生任何订单（mock 断言 submit_order 零调用）
3. tests/test_users_store.py:
   - 空表首次启动播种三个内置账户，日志含默认密码警告
   - role 非法值被拒
   - 用户管理端点要求 ADMIN，trader/viewer 得 403
4. tests/test_audit_store.py:
   - 写失败记 error 而非 debug，且不阻断主流程
   - 无 UPDATE/DELETE 端点（反射断言路由表）
5. ruff check app tests → All checks passed! · pytest -q 全绿
```

## 四、不做

- 用户自助注册 / 邮箱验证 / 找回密码
- 对账的自动纠正（见 §1.3 第 4 点）
- 审计日志的可视化页面（本期只做落库）
- 会话管理与强制下线
