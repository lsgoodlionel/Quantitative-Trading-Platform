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

async def reconcile(
    market: Market, oms, gateway, *, local_cash: float | None = None
) -> ReconcileResult: ...
```

> **原稿这里有两处错，实现时才暴露：**
>
> **(1) `cash_diff` 在原签名下无法有意义地实现。** `OrderManager` **不维护本地资金账本**
> （`_control_context` 里 `cash`/`portfolio_value` 注释明说恒为 0）。照原签名只能拿 0
> 去减券商现金，产出一条 100% 触发的假差异 —— 正是本契约自己反对的假阳性。
> 改为显式传 `local_cash`，不传时 `cash_diff=None`，语义是「本期未对资金」
> 而非「资金一致」。
>
> **(2) 「Celery 定时任务」这个载体本身是错的。** `get_order_manager()` 读的是
> **模块级全局**，只在 FastAPI 启动时 `init_order_manager()` 赋值。Celery worker 是
> 另一个进程，那份订单簿**永远是空的** —— 于是券商每一个持仓都变成一条
> 「本地 0 / 券商 N」的差异，定时任务照写出来就是个每天准时刷屏的噪音源。
>
> 实际做法：**`POST /api/v1/reconcile/{market}` 在 FastAPI 进程内跑**，那是唯一能拿到
> 有意义结果的地方；Celery 任务保留，但检测到「本地订单簿为空且差异全部来自本地无记录」
> 时抑制通知并写明 `suppressed_reason`。**券商不可达永不抑制。**

### 1.3 四个必须做对的点

1. **券商不可达 ≠ 对账通过。** 拉不到数据时 `broker_reachable=False`，
   **不要产出一份「零差异」的报告** —— 那是最危险的假阳性：
   用户看到「对账正常」，实际上根本没对上账。
2. **无差异时不发通知**（沿用 O-a 的判断）：每次对账都响一下等于训练用户忽略它。
   但**券商不可达要发** —— 那是需要人介入的状态。

   > ⚠️ 这一条与「直接复用既有 `emit_reconcile_diff`」冲突：原发射器标题写死
   > 「实盘对账存在差异」、载荷只有差异条数。不可达时 `diff_count=0`，用户收到的是
   > 一条**标题说有差异、内容说 0 条**的通知 —— 会被读成「通过」，正是第 1 点要防的。
   > 实际做法：给发射器加 `broker_reachable` 参数（默认 True，老调用方零影响），
   > 不可达时换成「实盘对账未完成 · 券商不可达」并附「本次结果不代表账目一致」。
   > **复用发射器可以，文案必须分叉。**
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
2. ~~**密码哈希继续用 passlib/bcrypt**（已在依赖里），不要换算法。~~

   > **「已在依赖里」这个前提是错的 —— passlib 是坏的。** passlib 1.7.4 读
   > `bcrypt.__about__.__version__`，而锁定的 bcrypt 5.0.0 已移除该属性：
   > `passlib.hash.bcrypt.hash()` 抛 `AttributeError`，再被包装成一条误导性的
   > 「password cannot be longer than 72 bytes」。
   > `auth.py` 本来就直接调 `bcrypt`。**算法不变，变的是别碰 passlib。**
   > 已从 `pyproject.toml` / `requirements.txt` 移除该死依赖，改为直接声明 bcrypt。
3. **`role` 的取值必须与 `app/core/rbac.py` 的枚举一致**，
   落库时校验 —— 一个 role 写错的用户会以「谁都不是」的身份存在。

4. **（原稿漏了，是个安全洞）登录取数顺序必须写死。** §2.2 第 1 点只讲了播种，
   没讲登录时查不到人怎么办。若实现成「无条件回落内置账户」，管理员就**删不掉账号** ——
   被删的 admin 用源码里公开的 `admin123` 照样能登录。

   口径：**库可达 + 查无此人 → 拒绝**（绝不回落）；**只有库本身不可达才回落内置账户**
   （保住零配置启动）并记 WARNING。`is_active=False` 一律拒绝登录。

5. **（原稿漏了）管理员不得删除/停用/降级自己** —— 把最后一条进管理后台的路
   自己锁上是不可逆事故。

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

   实际选了双写，理由：删掉 Redis 会破坏现有 `GET /audit` 的有界倒序扫描
   （Postgres OFFSET 分页在同一位置更差）；只留 Redis 又没解决本契约要解决的问题
   （maxlen 1 万条滚动裁剪 = 会悄悄丢）。两条路径互相独立，任一失败只记 ERROR
   并计数，不阻断主流程也不带走另一条。

3. **（原稿漏了）Redis 挂了怎么读 —— 现状恰恰是最坏的那种。** 现有 `GET /audit`
   在 Redis 异常时 `return AuditListResponse(items=[], total=0)`：用户看到「审计是空的」，
   与「审计被删了」在界面上完全无法区分。这和本契约批评的 `logger.debug` 吞异常
   是同一种病。加 Postgres 回落，并给响应加 `source: redis|postgres|none`，
   让「查不到」和「没有」可区分。

4. **（原稿漏了）Postgres 熔断。** 没起 Postgres 的部署里，每次下单都会做一次
   必然失败的连接（本机实测约 50ms）并刷一条 ERROR。连续 3 次失败 → 停写 60s；
   熔断期间跳过的写入**仍计入失败计数**，监控照样看得到「审计没落库」，
   只是不再刷日志、不再拖慢热路径。

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
