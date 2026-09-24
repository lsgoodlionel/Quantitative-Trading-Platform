"""
Alpha101 表达式原文（审计用纯数据表）

每条是移植来源 `refs/vnpy-LHJY/vnpy/alpha/dataset/datasets/alpha_101.py`
（**MIT，可移植**）里对应 alpha 的表达式字符串，`returns` 已就地展开为
`(close / ts_delay(close, 1) - 1)` 的简写 `returns`。

**这张表的唯一用途是对拍**：`alpha101.py` 里的 pandas 实现必须逐算子对应本表的
表达式。因子最危险的失败模式不是算错，而是「名字叫 alpha42 但算法不是 alpha42」；
把原文摆在实现旁边，任何人都能一行一行比对。

未收录的 alpha 及原因见 `alpha101.SKIPPED_ALPHAS`。
"""

from __future__ import annotations

#: alpha 名 → (原始表达式, 主导回看窗口)
ALPHA_EXPRESSIONS: dict[str, tuple[str, int]] = {
    "alpha1": (
        "cs_rank(ts_argmax(pow1(quesval(0, returns, close, ts_std(returns, 20)), 2.0), 5)) - 0.5",
        20,
    ),
    "alpha2": (
        "-1 * ts_corr(cs_rank(ts_delta(log(volume), 2)), cs_rank((close - open) / open), 6)",
        6,
    ),
    "alpha3": ("-1 * ts_corr(cs_rank(open), cs_rank(volume), 10)", 10),
    "alpha4": ("-1 * ts_rank(cs_rank(low), 9)", 9),
    "alpha6": ("-1 * ts_corr(open, volume, 10)", 10),
    "alpha7": (
        "quesval2(ts_mean(volume, 20), volume, "
        "(-1 * ts_rank(abs(close - ts_delay(close, 7)), 60)) * sign(ts_delta(close, 7)), -1)",
        60,
    ),
    "alpha8": (
        "-1 * cs_rank((ts_sum(open, 5) * ts_sum(returns, 5)) "
        "- ts_delay(ts_sum(open, 5) * ts_sum(returns, 5), 10))",
        15,
    ),
    "alpha9": (
        "quesval(0, ts_min(ts_delta(close, 1), 5), ts_delta(close, 1), "
        "quesval(0, ts_max(ts_delta(close, 1), 5), -1 * ts_delta(close, 1), ts_delta(close, 1)))",
        5,
    ),
    "alpha10": (
        "cs_rank(quesval(0, ts_min(ts_delta(close, 1), 4), ts_delta(close, 1), "
        "quesval(0, ts_max(ts_delta(close, 1), 4), -1 * ts_delta(close, 1), ts_delta(close, 1))))",
        4,
    ),
    "alpha12": ("sign(ts_delta(volume, 1)) * (-1 * ts_delta(close, 1))", 1),
    "alpha13": ("-1 * cs_rank(ts_cov(cs_rank(close), cs_rank(volume), 5))", 5),
    "alpha14": (
        "(-1 * cs_rank(returns - ts_delay(returns, 3))) * ts_corr(open, volume, 10)",
        10,
    ),
    "alpha15": (
        "-1 * ts_sum(cs_rank(ts_corr(cs_rank(high), cs_rank(volume), 3)), 3)",
        6,
    ),
    "alpha16": ("-1 * cs_rank(ts_cov(cs_rank(high), cs_rank(volume), 5))", 5),
    "alpha17": (
        "(-1 * cs_rank(ts_rank(close, 10))) "
        "* cs_rank(close - 2 * ts_delay(close, 1) + ts_delay(close, 2)) "
        "* cs_rank(ts_rank(volume / ts_mean(volume, 20), 5))",
        20,
    ),
    "alpha18": (
        "-1 * cs_rank((ts_std(abs(close - open), 5) + (close - open)) + ts_corr(close, open, 10))",
        10,
    ),
    "alpha19": (
        "(-1 * sign(ts_delta(close, 7) + (close - ts_delay(close, 7)))) "
        "* (cs_rank(ts_sum(returns, 250) + 1) + 1)",
        250,
    ),
    "alpha20": (
        "(-1 * cs_rank(open - ts_delay(high, 1))) * cs_rank(open - ts_delay(close, 1)) "
        "* cs_rank(open - ts_delay(low, 1))",
        1,
    ),
    "alpha21": (
        "quesval2(ts_mean(close, 8) + ts_std(close, 8), ts_mean(close, 2), -1, "
        "quesval2(ts_mean(close, 2), ts_mean(close, 8) - ts_std(close, 8), 1, "
        "quesval(1, volume / ts_mean(volume, 20), 1, -1)))",
        20,
    ),
    "alpha22": (
        "-1 * ts_delta(ts_corr(high, volume, 5), 5) * cs_rank(ts_std(close, 20))",
        20,
    ),
    "alpha23": ("quesval2(ts_mean(high, 20), high, -1 * ts_delta(high, 2), 0)", 20),
    "alpha24": (
        "quesval(0.05, ts_delta(ts_sum(close, 100) / 100, 100) / ts_delay(close, 100), "
        "-1 * ts_delta(close, 3), -1 * (close - ts_min(close, 100)))",
        200,
    ),
    "alpha26": (
        "-1 * ts_max(ts_corr(ts_rank(volume, 5), ts_rank(high, 5), 5), 3)",
        13,
    ),
    "alpha28": (
        "cs_scale(ts_corr(ts_mean(volume, 20), low, 5) + (high + low) / 2 - close)",
        25,
    ),
    "alpha29": (
        "ts_min(ts_product(cs_rank(cs_rank(cs_scale(log(ts_sum(ts_min(cs_rank(cs_rank("
        "-1 * cs_rank(ts_delta(close - 1, 5)))), 2), 1))))), 1), 5) "
        "+ ts_rank(ts_delay(-1 * returns, 6), 5)",
        15,
    ),
    "alpha30": (
        "((cs_rank(sign(close - ts_delay(close, 1)) + sign(ts_delay(close, 1) - ts_delay(close, 2)) "
        "+ sign(ts_delay(close, 2) - ts_delay(close, 3))) * -1 + 1) * ts_sum(volume, 5)) "
        "/ ts_sum(volume, 20)",
        20,
    ),
    "alpha31": (
        "(cs_rank(cs_rank(cs_rank(ts_decay_linear(-1 * cs_rank(cs_rank(ts_delta(close, 10))), 10)))) "
        "+ cs_rank(-1 * ts_delta(close, 3))) + sign(cs_scale(ts_corr(ts_mean(volume, 20), low, 12)))",
        32,
    ),
    "alpha33": ("cs_rank(-1 * (open / close * -1 + 1))", 1),
    "alpha34": (
        "cs_rank((cs_rank(ts_std(returns, 2) / ts_std(returns, 5)) * -1 + 1) "
        "+ (cs_rank(ts_delta(close, 1)) * -1 + 1))",
        5,
    ),
    "alpha35": (
        "(ts_rank(volume, 32) * (ts_rank(close + high - low, 16) * -1 + 1)) "
        "* (ts_rank(returns, 32) * -1 + 1)",
        32,
    ),
    "alpha37": (
        "cs_rank(ts_corr(ts_delay(open - close, 1), close, 200)) + cs_rank(open - close)",
        200,
    ),
    "alpha38": ("(-1 * cs_rank(ts_rank(close, 10))) * cs_rank(close / open)", 10),
    "alpha39": (
        "(-1 * cs_rank(ts_delta(close, 7) "
        "* (cs_rank(ts_decay_linear(volume / ts_mean(volume, 20), 9)) * -1 + 1))) "
        "* (cs_rank(ts_sum(returns, 250)) + 1)",
        250,
    ),
    "alpha40": ("(-1 * cs_rank(ts_std(high, 10))) * ts_corr(high, volume, 10)", 10),
    "alpha43": (
        "ts_rank(volume / ts_mean(volume, 20), 20) * ts_rank(-1 * ts_delta(close, 7), 8)",
        20,
    ),
    "alpha44": ("-1 * ts_corr(high, cs_rank(volume), 5)", 5),
    "alpha45": (
        "-1 * cs_rank(ts_sum(ts_delay(close, 5), 20) / 20) * ts_corr(close, volume, 2) "
        "* cs_rank(ts_corr(ts_sum(close, 5), ts_sum(close, 20), 2))",
        25,
    ),
    "alpha46": (
        "quesval(0.25, (ts_delay(close, 20) - ts_delay(close, 10)) / 10 "
        "- (ts_delay(close, 10) - close) / 10, -1, "
        "quesval(0, (ts_delay(close, 20) - ts_delay(close, 10)) / 10 "
        "- (ts_delay(close, 10) - close) / 10, -1 * (close - ts_delay(close, 1)), 1))",
        20,
    ),
    "alpha49": (
        "quesval(-0.1, (ts_delay(close, 20) - ts_delay(close, 10)) / 10 "
        "- (ts_delay(close, 10) - close) / 10, -1 * (close - ts_delay(close, 1)), 1)",
        20,
    ),
    "alpha51": (
        "quesval(-0.05, (ts_delay(close, 20) - ts_delay(close, 10)) / 10 "
        "- (ts_delay(close, 10) - close) / 10, -1 * (close - ts_delay(close, 1)), 1)",
        20,
    ),
    "alpha52": (
        "((-1 * ts_min(low, 5)) + ts_delay(ts_min(low, 5), 5)) "
        "* cs_rank((ts_sum(returns, 240) - ts_sum(returns, 20)) / 220) * ts_rank(volume, 5)",
        240,
    ),
    "alpha53": (
        "-1 * ts_delta(((close - low) - (high - close)) / (close - low), 9)",
        9,
    ),
    "alpha54": (
        "(-1 * ((low - close) * pow1(open, 5))) / ((low - high) * pow1(close, 5))",
        1,
    ),
    "alpha55": (
        "-1 * ts_corr(cs_rank((close - ts_min(low, 12)) / (ts_max(high, 12) - ts_min(low, 12))), "
        "cs_rank(volume), 6)",
        18,
    ),
    "alpha60": (
        "-1 * ((2 * cs_scale(cs_rank(((close - low) - (high - close)) / (high - low) * volume))) "
        "- cs_scale(cs_rank(ts_argmax(close, 10))))",
        10,
    ),
    "alpha68": (
        "(ts_rank(ts_corr(cs_rank(high), cs_rank(ts_mean(volume, 15)), 9), 14) "
        "< cs_rank(ts_delta(close * 0.518371 + low * (1 - 0.518371), 1))) * -1",
        38,
    ),
    "alpha85": (
        "pow2(cs_rank(ts_corr(high * 0.876703 + close * 0.123297, ts_mean(volume, 30), 10)), "
        "cs_rank(ts_corr(ts_rank((high + low) / 2, 4), ts_rank(volume, 10), 7)))",
        40,
    ),
    "alpha88": (
        "ts_less(cs_rank(ts_decay_linear((cs_rank(open) + cs_rank(low)) "
        "- (cs_rank(high) + cs_rank(close)), 8)), "
        "ts_rank(ts_decay_linear(ts_corr(ts_rank(close, 8), ts_rank(ts_mean(volume, 60), 21), 8), 7), 3))",
        90,
    ),
    "alpha92": (
        "ts_less(ts_rank(ts_decay_linear(quesval2((high + low) / 2 + close, low + open, 1, 0), 15), 19), "
        "ts_rank(ts_decay_linear(ts_corr(cs_rank(low), cs_rank(ts_mean(volume, 30)), 8), 7), 7))",
        50,
    ),
    "alpha95": (
        "quesval2(cs_rank(open - ts_min(open, 12)), "
        "ts_rank(pow1(cs_rank(ts_corr(ts_sum((high + low) / 2, 19), ts_sum(ts_mean(volume, 40), 19), 13)), 5), 12), "
        "1, 0)",
        90,
    ),
    "alpha99": (
        "quesval2(cs_rank(ts_corr(ts_sum((high + low) / 2, 20), ts_sum(ts_mean(volume, 60), 20), 9)), "
        "cs_rank(ts_corr(low, volume, 6)), 1, 0) * -1",
        100,
    ),
    "alpha101": ("(close - open) / ((high - low) + 0.001)", 1),
}
