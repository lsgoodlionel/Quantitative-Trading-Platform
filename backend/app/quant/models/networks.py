"""序列模型网络结构定义（B8）。

三种循环网络用于二分类（预测 n 日后涨跌）：
  - lstm   : 标准 LSTM，取末隐状态 → 全连接输出
  - gru    : 标准 GRU，取末隐状态 → 全连接输出
  - alstm  : 注意力 LSTM，输入映射 + LSTM + 时间维注意力池化 + 全连接

torch 为可选依赖，所有 nn.Module 子类均在函数内部定义（lazy import），
未安装 torch 时本模块可被导入但不会触发 torch 加载。

参考结构（只读，未复制代码）：
  refs/qlib/qlib/contrib/model/pytorch_{lstm,gru,alstm}.py
差异：参考为回归 (输出维度1 + MSE)；此处为二分类，输出单 logit 配合
BCEWithLogitsLoss；ALSTM 注意力沿用「输入FC → RNN → tanh 注意力 → 拼接」范式。
"""

from __future__ import annotations

from typing import Literal

SequenceModelType = Literal["lstm", "gru", "alstm"]

# 支持的模型元信息（供前端展示 + 后端校验）
SEQUENCE_MODEL_META: list[dict] = [
    {
        "value": "lstm",
        "label": "LSTM",
        "desc": "长短期记忆网络，捕捉价格序列长期依赖，稳健基线",
    },
    {
        "value": "gru",
        "label": "GRU",
        "desc": "门控循环单元，参数更少训练更快，性能接近 LSTM",
    },
    {
        "value": "alstm",
        "label": "ALSTM",
        "desc": "注意力 LSTM，对关键时间步加权，可解释性更强",
    },
]

VALID_MODEL_TYPES: frozenset[str] = frozenset(m["value"] for m in SEQUENCE_MODEL_META)


def build_network(
    model_type: SequenceModelType,
    input_size: int,
    hidden_size: int,
    num_layers: int,
    dropout: float,
):
    """构造并返回一个未训练的序列网络（nn.Module）。

    在函数内部 import torch，使 torch 成为真正的可选依赖。
    调用方须先确认 torch 可用（见 pipeline.torch_available）。
    """
    import torch
    from torch import nn

    if model_type not in VALID_MODEL_TYPES:
        raise ValueError(f"未知序列模型类型: {model_type}")

    # 多层时才对 RNN 施加层间 dropout（单层设 0 避免 torch 警告）
    rnn_dropout = dropout if num_layers > 1 else 0.0

    class _LSTMNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.rnn = nn.LSTM(
                input_size, hidden_size, num_layers,
                batch_first=True, dropout=rnn_dropout,
            )
            self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(hidden_size, 1))

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            out, _ = self.rnn(x)
            return self.head(out[:, -1, :]).squeeze(-1)

    class _GRUNet(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.rnn = nn.GRU(
                input_size, hidden_size, num_layers,
                batch_first=True, dropout=rnn_dropout,
            )
            self.head = nn.Sequential(nn.Dropout(dropout), nn.Linear(hidden_size, 1))

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            out, _ = self.rnn(x)
            return self.head(out[:, -1, :]).squeeze(-1)

    class _ALSTMNet(nn.Module):
        """输入映射 → LSTM → 时间维注意力池化 → 拼接末隐状态 → 输出。"""

        def __init__(self) -> None:
            super().__init__()
            self.input_fc = nn.Sequential(
                nn.Linear(input_size, hidden_size), nn.Tanh(),
            )
            self.rnn = nn.LSTM(
                hidden_size, hidden_size, num_layers,
                batch_first=True, dropout=rnn_dropout,
            )
            self.att_score = nn.Sequential(
                nn.Linear(hidden_size, hidden_size), nn.Tanh(),
                nn.Dropout(dropout), nn.Linear(hidden_size, 1),
            )
            self.head = nn.Linear(hidden_size * 2, 1)

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":
            mapped = self.input_fc(x)                 # (B, T, H)
            out, _ = self.rnn(mapped)                 # (B, T, H)
            scores = self.att_score(out)              # (B, T, 1)
            weights = torch.softmax(scores, dim=1)    # 沿时间维归一化
            context = (out * weights).sum(dim=1)      # (B, H) 注意力上下文
            combined = torch.cat([out[:, -1, :], context], dim=1)  # (B, 2H)
            return self.head(combined).squeeze(-1)

    networks = {"lstm": _LSTMNet, "gru": _GRUNet, "alstm": _ALSTMNet}
    return networks[model_type]()
