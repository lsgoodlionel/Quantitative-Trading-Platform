"""
表达式树（Expression Tree）— 遗传因子挖掘的个体基因型

个体在遗传算法中以「表达式树」为基因型，序列化为 RPN（逆波兰）token 列表后交由
formula_factor.py 的栈式虚拟机（evaluate_formula）执行——与 AlphaGPT vm.py 的 StackVM
思想一致，但用不可变树而非裸 token 串，保证交叉/变异后始终产出栈平衡的合法公式。

叶子（特征）取自 formula_factor.FEATURE_META；内部节点（算子）取自 formula_factor.OPS
（按元数分组）。窗口已烘焙进算子名（SLOPE10/CORR20…），故树无需携带额外参数。

设计要点：
  - Node 为 frozen dataclass，所有变换返回新树（不可变，符合项目风格）。
  - 生成用「grow」法：越深越可能收敛为叶子，从而约束公式长度。
  - 交叉/变异按前序索引随机选点，重建时复制未改动子树。
"""

from __future__ import annotations

import itertools
import random
from dataclasses import dataclass, field
from functools import lru_cache


@dataclass(frozen=True)
class Node:
    """表达式树节点：kind='leaf' 携带特征名；kind='op' 携带算子名与子节点。"""

    kind: str                       # "leaf" | "op"
    value: str                      # 特征名或算子名
    children: tuple[Node, ...] = field(default=())


# ── 词表（惰性构建，来自 formula_factor 单一真源，避免漂移）─────────────

@lru_cache(maxsize=2)
def _vocab(include_cross_section: bool = False) -> tuple[list[str], dict[int, list[str]]]:
    """返回 (叶子特征名列表, {arity: 算子名列表})，与公式引擎共享同一份定义。

    `include_cross_section=True` 时把 CS_* 截面算子并入搜索空间 —— 只有当挖掘走
    panel 求值路径时才可以打开，否则搜出来的公式在单标的路径上必然报错。
    默认关闭，因此既有实验的 seed → 结果映射保持不变。
    """
    from app.quant.formula_factor import CS_OPS, FEATURE_META, OPS

    leaves = [m["name"] for m in FEATURE_META]
    ops = [*OPS, *CS_OPS] if include_cross_section else list(OPS)
    ops_by_arity: dict[int, list[str]] = {}
    for op in ops:
        ops_by_arity.setdefault(op.arity, []).append(op.name)
    return leaves, ops_by_arity


def leaf_names() -> list[str]:
    return list(_vocab()[0])


def op_names_by_arity(include_cross_section: bool = False) -> dict[int, list[str]]:
    return {a: list(names) for a, names in _vocab(include_cross_section)[1].items()}


# ── 结构工具 ──────────────────────────────────────────────────────

def tree_size(node: Node) -> int:
    """节点总数（前序）。"""
    if node.kind == "leaf":
        return 1
    return 1 + sum(tree_size(c) for c in node.children)


def to_rpn(node: Node) -> list[str]:
    """后序遍历 → RPN token 列表（可直接交给 evaluate_formula）。"""
    tokens: list[str] = []
    _emit_rpn(node, tokens)
    return tokens


def _emit_rpn(node: Node, out: list[str]) -> None:
    for child in node.children:
        _emit_rpn(child, out)
    out.append(node.value)


def to_expr(node: Node) -> str:
    """人类可读中缀近似式，用于展示（如 DIV(MOM20, ATR_RATIO)）。"""
    if node.kind == "leaf":
        return node.value
    inner = ", ".join(to_expr(c) for c in node.children)
    return f"{node.value}({inner})"


# ── 随机生成（grow 法）────────────────────────────────────────────

def random_tree(
    rng: random.Random, max_depth: int, include_cross_section: bool = False
) -> Node:
    """按 grow 法随机生成一棵表达式树。"""
    return _grow(rng, depth=0, max_depth=max_depth, include_cs=include_cross_section)


def _grow(rng: random.Random, depth: int, max_depth: int, include_cs: bool = False) -> Node:
    leaves, ops_by_arity = _vocab(include_cs)
    leaf_prob = 0.3 + 0.7 * (depth / max(max_depth, 1))
    if depth >= max_depth or rng.random() < leaf_prob:
        return Node("leaf", rng.choice(leaves))

    arity = _pick_arity(rng, ops_by_arity)
    op_name = rng.choice(ops_by_arity[arity])
    children = tuple(_grow(rng, depth + 1, max_depth, include_cs) for _ in range(arity))
    return Node("op", op_name, children)


def _pick_arity(rng: random.Random, ops_by_arity: dict[int, list[str]]) -> int:
    """优先选一元/二元算子，三元（GATE）出现概率低，控制树深。"""
    weights = {1: 0.55, 2: 0.4, 3: 0.05}
    arities = [a for a in ops_by_arity if a in weights]
    probs = [weights[a] for a in arities]
    return rng.choices(arities, weights=probs, k=1)[0]


# ── 随机子树选取 / 替换（交叉与变异的公共原语）────────────────────

def random_subtree(rng: random.Random, tree: Node) -> Node:
    """均匀随机返回 tree 的某个子树（含根）。"""
    target = rng.randrange(tree_size(tree))
    counter = itertools.count()
    picked: list[Node] = []

    def visit(node: Node) -> None:
        if picked:
            return
        idx = next(counter)
        if idx == target:
            picked.append(node)
            return
        for child in node.children:
            visit(child)

    visit(tree)
    return picked[0]


def replace_random_subtree(rng: random.Random, tree: Node, replacement: Node) -> Node:
    """把 tree 中随机一个子树替换为 replacement，返回新树（不可变重建）。"""
    target = rng.randrange(tree_size(tree))
    counter = itertools.count()

    def rebuild(node: Node) -> Node:
        idx = next(counter)
        if idx == target:
            return replacement
        if node.kind == "leaf":
            return node
        return Node("op", node.value, tuple(rebuild(c) for c in node.children))

    return rebuild(tree)


def crossover(rng: random.Random, parent_a: Node, parent_b: Node) -> Node:
    """子代 = 用 parent_b 的随机子树替换 parent_a 的随机子树。"""
    donor = random_subtree(rng, parent_b)
    return replace_random_subtree(rng, parent_a, donor)


def mutate(
    rng: random.Random,
    tree: Node,
    max_depth: int,
    subtree_depth: int = 2,
    include_cross_section: bool = False,
) -> Node:
    """点变异：把随机子树替换为一棵新的小随机树。"""
    fresh = random_tree(rng, max_depth=subtree_depth, include_cross_section=include_cross_section)
    mutated = replace_random_subtree(rng, tree, fresh)
    if tree_size(mutated) <= _MAX_NODES:
        return mutated
    return random_tree(rng, max_depth, include_cross_section=include_cross_section)


# 节点数硬上限（对应 evaluate_formula 的 32-token 约束，留裕量）
_MAX_NODES = 24


def clamp_size(
    rng: random.Random, tree: Node, max_depth: int, include_cross_section: bool = False
) -> Node:
    """若树过大（超过 RPN token 上限）则回退为一棵小树。"""
    if tree_size(tree) <= _MAX_NODES and len(to_rpn(tree)) <= 30:
        return tree
    return random_tree(
        rng, max_depth=min(max_depth, 3), include_cross_section=include_cross_section
    )
