from __future__ import annotations
from typing import List, Tuple
import numpy as np


def ensemble_stats(models: List[np.ndarray]) -> Tuple[np.ndarray, np.ndarray]:
    stack = np.stack(models, axis=0).astype(np.float32)
    valid = np.isfinite(stack)
    count = valid.sum(axis=0)
    mean = np.full(stack.shape[1:], np.nan, dtype=np.float32)
    np.divide(np.where(valid, stack, 0.0).sum(axis=0, dtype=np.float32), count, out=mean, where=count > 0)
    var = np.full(stack.shape[1:], np.nan, dtype=np.float32)
    np.divide(np.where(valid, (stack - mean) ** 2, 0.0).sum(axis=0, dtype=np.float32), count, out=var, where=count > 0)
    spread = np.sqrt(var, dtype=np.float32)
    thr = 0.6
    agree = np.full(stack.shape[1:], np.nan, dtype=np.float32)
    np.divide(((stack >= thr) & valid).sum(axis=0, dtype=np.float32), count, out=agree, where=count > 0)
    return agree.astype(np.float32), spread.astype(np.float32)
