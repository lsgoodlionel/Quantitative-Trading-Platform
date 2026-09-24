# Wave M-c 契约：投研产物库（M4）+ 统一 ML 模型模板（M5）

> 对应 [DEVPLAN_V4.md](../../DEVPLAN_V4.md) 的 **M4 / M5** · Agent-Mc
> 状态：📋 待审阅
>
> **红线**：`backend/tests/regression`（146 用例）全绿，不得修改 `tests/regression/` 任何文件。

---

## 一、M4 投研产物库（Lab）

### 1.1 现状与问题

`app/quant/experiments/recorder.py` 只存**指标记录**，且 `MAX_RECORDS = 500`
滚动淘汰。数据集、训练好的模型、预测信号统统没地方放 ——
调完参关掉浏览器，下次得从头再来。

V3 A-a 已经踩过这个坑的一角：被提升为策略的实验记录会被淘汰，
所以那里改成了「独立存完整 spec」。**M4 是这个问题的系统解法。**

### 1.2 设计

参考 vnpy `alpha/lab.py`（**MIT**，约 480 行，接口可直接对齐）。

```
app/quant/lab/
    base.py       # ArtifactKind / ArtifactMeta / LabStore 协议
    store.py      # 文件系统 + 元数据落库的实现
```

```python
class ArtifactKind(str, Enum):
    DATASET = "dataset"
    MODEL = "model"
    SIGNAL = "signal"

@dataclass(frozen=True)
class ArtifactMeta:
    artifact_id: str
    kind: ArtifactKind
    name: str
    created_at: datetime
    size_bytes: int
    tags: dict[str, str]          # 自由标注（策略名、因子公式、区间…）
    checksum: str                 # 内容哈希，用于校验与去重
```

**三个必须做对的点**：

1. **元数据落库，内容落文件系统**。元数据要能查询/筛选（TimescaleDB，
   与 A-d 的回测历史同一处理），内容（parquet / pickle）走对象存储式的本地目录。
   把模型二进制塞进数据库是个坑。

2. **`load_model` 要反序列化任意 pickle —— 这是远程代码执行面**。
   本期产物全部由本服务自己写入，风险可控，但**必须**：
   - 存储路径不接受用户输入（`artifact_id` 由服务端生成 UUID，不用用户给的 name 做路径）
   - 加载前校验 `checksum`，不匹配直接拒绝
   - 在模块 docstring 明确写出「不得加载外部来源的产物」
   若将来要支持导入外部模型，必须先换成非 pickle 的序列化格式。

3. **容量要有策略且要可见**。不能重蹈实验记录器「悄悄丢数据」的覆辙：
   要么不限容量+手动删除（推荐，与 A-d 的回测历史一致），
   要么限容量但在 API 与前端**明示**保留策略。

### 1.3 与实验记录器的关系

实验记录器**不动**（它记的是指标，职责清晰）。
增加 `ExperimentRecord.artifact_ids: list[str]` 让实验能引用产物。
产物独立生命周期 —— 实验记录被淘汰不影响产物存在。

---

## 二、M5 统一 ML 模型模板

### 2.1 现状

三套各自为政：`app/quant/ml_strategy.py`、`double_ensemble.py`、
`models/sequence.py`。接口不一致，无法横向对比，新增模型要重写一遍训练/预测胶水。

### 2.2 设计

参考 vnpy `alpha/model/template.py`（**MIT**）。

```python
# app/quant/models/template.py

class AlphaModelTemplate(ABC):
    """统一的因子模型接口。fit / predict / detail 三件套。"""

    name: str

    @abstractmethod
    def fit(self, train: pd.DataFrame, valid: pd.DataFrame | None = None) -> None: ...

    @abstractmethod
    def predict(self, features: pd.DataFrame) -> pd.Series: ...

    def detail(self) -> dict:
        """特征重要性 / 训练曲线 / 超参 —— 供前端展示，默认返回空。"""
        return {}
```

新增两个实现：`LassoAlphaModel`、`LightGBMAlphaModel`（sklearn / lightgbm 若已在
`requirements.txt` 则可用，**否则不新增依赖**，改用已有的库实现同等功能并在报告说明）。

### 2.3 收编既有三套的正确姿势

⚠️ **不要重写它们的算法**。正确做法是加**适配层**让它们满足新接口：

```python
class _MLStrategyAdapter(AlphaModelTemplate): ...   # 包 ml_strategy.py
class _DoubleEnsembleAdapter(AlphaModelTemplate): ...
class _SequenceAdapter(AlphaModelTemplate): ...
```

理由与 Wave K-d 的 `LegacyStrategyAlphaAdapter` 完全相同：
既有实现有测试背书、有用户在用，重写等于把已验证的东西推倒重来。
**适配层的验收标准是：包装后产出的预测值与直接调用原实现逐值相等。**

### 2.4 与 M4 的衔接

`AlphaModelTemplate` 训练完能存进 Lab（`kind=MODEL`），加载回来能直接 `predict`。
端到端用例：训练 → 存 → 新进程加载 → predict 结果与训练时一致。

---

## 三、验收

```
1. tests/regression 146 用例全绿
2. tests/test_lab_store.py:
   - 三类产物各自的存 → 列 → 读 → 删
   - checksum 不匹配时拒绝加载（篡改文件后断言抛错）
   - artifact_id 由服务端生成；用户传的 name 含 "../" 不影响存储路径
   - 实验记录被淘汰后产物仍可加载（这是 M4 存在的理由）
3. tests/test_model_template.py:
   - 三个适配器包装后的 predict **与直接调用原实现逐值相等**
     （这是「没有重写算法」的证据）
   - 新增的 Lasso / LightGBM 实现能 fit + predict + detail
   - 训练 → 存 Lab → 重新加载 → predict 一致（端到端）
4. ruff check app tests → All checks passed! · pytest -q 全绿
```

## 四、不做

- 自适应再训练与漂移检测（M6，Wave N+O）
- 模型的分布式训练
- 非 pickle 的模型序列化格式（本期产物全部自产自用，见 §1.2 第 2 点）
