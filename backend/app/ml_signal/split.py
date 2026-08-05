"""时间序列切分：避免数据泄露的 walk-forward / purged split。

金融时间序列绝不能随机切分（会泄露未来信息）。本模块提供：
  - time_series_split : 单次按时间点切 train/val/test
  - walk_forward_split: 滚动窗口的 walk-forward 切分（生成器）
  - purge_overlap     : 剔除标签窗口跨边界导致的污染样本

「Purge」与「Embargo」概念同样来自 López de Prado：
  训练集中靠近验证集的样本，其标签可能依赖验证集价格（前视泄露），
  需要在边界两侧删除一个窗口长度的样本。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np
import pandas as pd


@dataclass
class Split:
    """一次切分的训练/验证/测试索引（位置索引，非 label 索引）。"""
    train_idx: np.ndarray
    val_idx: np.ndarray
    test_idx: np.ndarray

    def sizes(self) -> dict[str, int]:
        return {
            "train": len(self.train_idx),
            "val": len(self.val_idx),
            "test": len(self.test_idx),
        }


def time_series_split(
    n: int,
    test_size: float = 0.2,
    val_size: float = 0.15,
    purge_window: int = 10,
) -> Split:
    """按时间顺序单次切分。

    参数：
      n             : 样本总数
      test_size     : 测试集占比（取最后段）
      val_size      : 验证集占比（取测试集之前）
      purge_window  : 边界两侧剔除的样本数（防止标签泄露）

    返回 Split（位置索引 0..n-1）。
    """
    if n < 30:
        # 样本太少，全部给训练
        all_idx = np.arange(n)
        return Split(all_idx, np.array([], dtype=int), np.array([], dtype=int))

    n_test = max(int(n * test_size), 1)
    n_val = max(int(n * val_size), 1)
    n_train = n - n_test - n_val
    if n_train < 10:
        n_train = max(n - n_test - n_val, 10)

    train_end = n_train
    val_start = train_end
    val_end = val_start + n_val
    test_start = val_end

    train_idx = np.arange(0, max(train_end - purge_window, 1))
    val_idx = np.arange(val_start + purge_window, val_end - purge_window) \
        if val_end - purge_window > val_start + purge_window else np.arange(val_start, val_end)
    test_idx = np.arange(test_start + purge_window, n) \
        if test_start + purge_window < n else np.arange(test_start, n)

    return Split(train_idx, val_idx, test_idx)


def walk_forward_split(
    n: int,
    train_size: int = 200,
    test_size: int = 50,
    step: int = 50,
    purge_window: int = 10,
) -> Iterator[Split]:
    """滚动 walk-forward 切分生成器。

    每一轮用 train_size 行训练，紧接着 test_size 行测试，
    然后整体向前滑动 step 行，直到样本耗尽。

    yield:
      Split（val_idx 留空，或用 train 末尾一段）
    """
    if n < train_size + test_size + purge_window:
        return

    start = 0
    while start + train_size + purge_window + test_size <= n:
        train_end = start + train_size
        test_start = train_end + purge_window
        test_end = test_start + test_size

        train_idx = np.arange(start, train_end)
        # 从训练集末尾抽 15% 作为验证
        val_n = max(int(train_size * 0.15), 5)
        val_idx = np.arange(train_end - val_n, train_end)
        test_idx = np.arange(test_start, min(test_end, n))

        yield Split(train_idx, val_idx, test_idx)
        start += step


def purge_overlap(
    indices: np.ndarray,
    label_horizon: int,
    forbidden: np.ndarray,
) -> np.ndarray:
    """从 indices 中剔除「标签窗口与 forbidden 区间重叠」的样本。

    用于：训练集中靠近测试集的样本，其 forward 标签可能用到测试集价格。
    参数：
      indices        : 待清洗的位置索引数组
      label_horizon  : 标签生成时的前瞻窗口（如 max_holding_days）
      forbidden      : 禁区位置索引（通常是测试集起点附近）

    返回过滤后的索引数组。
    """
    if len(forbidden) == 0:
        return indices
    fmin, fmax = int(forbidden.min()), int(forbidden.max())
    mask = np.ones(len(indices), dtype=bool)
    for k, i in enumerate(indices):
        # 样本 i 的标签覆盖区间 [i, i+label_horizon]
        if i + label_horizon >= fmin and i <= fmax:
            mask[k] = False
    return indices[mask]
