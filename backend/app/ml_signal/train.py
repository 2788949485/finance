"""模型训练：自适应后端（sklearn 优先，numpy 兜底）。

设计：
  - sklearn 可用时：使用 RandomForest / GradientBoosting / LogisticRegression
  - sklearn 不可用时：退化为 numpy 实现的逻辑回归（L2 正则）
  - 统一接口：fit(X, y) → predict(X) → predict_proba(X)

所有模型支持三分类 {-1, 0, 1}。评估时通常聚焦 +1 类的精度。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

try:
    from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.pipeline import Pipeline as SkPipeline
    SKLEARN_AVAILABLE = True
except Exception:  # pragma: no cover
    SKLEARN_AVAILABLE = False


# ============================================================
# 统一 Trainer 接口
# ============================================================

@dataclass
class ModelTrainer:
    """模型训练器：封装特征矩阵 → 训练 → 预测。

    用法：
        trainer = ModelTrainer(model="auto")
        trainer.fit(X_train, y_train)
        preds = trainer.predict(X_test)
        probs = trainer.predict_proba(X_test)
    """
    model: str = "auto"            # auto / rf / gb / logit / numpy
    random_state: int = 42
    _clf: Any = field(default=None, init=False, repr=False)
    _classes: Any = field(default=None, init=False, repr=False)
    _feature_names: Any = field(default=None, init=False, repr=False)
    _backend: str = field(default="unknown", init=False)

    def fit(self, X: np.ndarray, y: np.ndarray,
            feature_names: Optional[list[str]] = None) -> "ModelTrainer":
        """训练模型。

        参数：
          X            : (n_samples, n_features) 特征矩阵
          y            : (n_samples,) 标签，值 ∈ {-1, 0, 1}
          feature_names: 特征列名（用于特征重要性输出）
        """
        X = np.asarray(X, dtype=float)
        y = np.asarray(y, dtype=float)

        # 过滤 NaN（按行）
        valid = ~(np.isnan(X).any(axis=1) | np.isnan(y))
        X, y = X[valid], y[valid]

        if len(X) == 0 or len(np.unique(y)) < 2:
            # 样本不足或单一类别：退化为常量预测器
            self._clf = _ConstantPredictor(np.bincount(
                (y.astype(int) + 1).clip(0, 2) if len(y) else [1],
                minlength=3,
            ).argmax() - 1 if len(y) else 0)
            self._classes = np.array([-1, 0, 1])
            self._backend = "constant"
            self._feature_names = feature_names
            return self

        self._feature_names = feature_names
        self._clf, self._backend = self._build_and_fit(X, y)
        self._classes = np.array([-1, 0, 1])
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        if self._clf is None:
            raise RuntimeError("模型未训练，请先调用 fit()")
        X = np.asarray(X, dtype=float)
        preds = self._clf.predict(X)
        return np.asarray(preds)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """返回 (n_samples, 3) 概率，列顺序对应 [-1, 0, 1]。"""
        if self._clf is None:
            raise RuntimeError("模型未训练，请先调用 fit()")
        X = np.asarray(X, dtype=float)
        if hasattr(self._clf, "predict_proba"):
            proba = self._clf.predict_proba(X)
            # 对齐到 [-1, 0, 1]（列顺序固定为 3 列）
            classes = getattr(self._clf, "classes_", None)
            if classes is not None:
                full = np.zeros((len(proba), 3))
                for k, c in enumerate(classes):
                    # 每个模型实际出现的类别映射到固定列
                    if int(c) + 1 in (0, 1, 2):
                        full[:, int(c) + 1] = proba[:, k]
                return full
            return proba
        # 退化为 one-hot
        preds = self._clf.predict(X)
        proba = np.zeros((len(preds), 3))
        for i, p in enumerate(preds):
            proba[i, int(p) + 1] = 1.0
        return proba

    def feature_importance(self) -> dict[str, float]:
        """返回 {特征名: 重要性}，无重要性时返回空字典。"""
        if self._feature_names is None:
            return {}
        clf = self._clf
        # sklearn pipeline：取最后一步
        if SKLEARN_AVAILABLE and isinstance(clf, SkPipeline):
            clf = clf.steps[-1][1]
        if hasattr(clf, "feature_importances_"):
            imp = clf.feature_importances_
            return dict(zip(self._feature_names, np.round(imp, 4).tolist()))
        if hasattr(clf, "coef_"):
            coef = clf.coef_
            # 取绝对值平均（多分类）
            imp = np.abs(coef).mean(axis=0) if coef.ndim > 1 else np.abs(coef)
            return dict(zip(self._feature_names, np.round(imp, 4).tolist()))
        return {}

    @property
    def backend(self) -> str:
        return self._backend

    # --------------------------------------------------------
    def _build_and_fit(self, X: np.ndarray, y: np.ndarray):
        """根据 model 参数和 sklearn 可用性构建并训练模型。"""
        choice = self.model
        if choice == "auto":
            choice = "rf" if SKLEARN_AVAILABLE else "numpy"

        if not SKLEARN_AVAILABLE or choice == "numpy":
            clf = _NumpyLogisticRegression(
                lr=0.1, epochs=400, l2=0.01, seed=self.random_state
            )
            clf.fit(X, y)
            return clf, "numpy_logit"

        if choice == "rf":
            clf = SkPipeline([
                ("scaler", StandardScaler()),
                ("clf", RandomForestClassifier(
                    n_estimators=200, max_depth=6,
                    min_samples_leaf=10, class_weight="balanced",
                    random_state=self.random_state, n_jobs=-1,
                )),
            ])
        elif choice == "gb":
            clf = SkPipeline([
                ("scaler", StandardScaler()),
                ("clf", GradientBoostingClassifier(
                    n_estimators=150, max_depth=3, learning_rate=0.05,
                    random_state=self.random_state,
                )),
            ])
        else:  # logit
            clf = SkPipeline([
                ("scaler", StandardScaler()),
                ("clf", LogisticRegression(
                    max_iter=1000, class_weight="balanced",
                    C=1.0, random_state=self.random_state,
                )),
            ])
        clf.fit(X, y)
        backend_name = {"rf": "sklearn_rf", "gb": "sklearn_gb",
                        "logit": "sklearn_logit"}[choice]
        return clf, backend_name


# ============================================================
# numpy 兜底：softmax 逻辑回归（多分类，L2 正则）
# ============================================================

class _NumpyLogisticRegression:
    """纯 numpy 多分类逻辑回归（softmax + 梯度下降 + L2）。

    仅在 sklearn 不可用时启用，保证训练管线不中断。
    支持 {-1, 0, 1} → 内部映射到 {0, 1, 2}。
    """
    def __init__(self, lr: float = 0.1, epochs: int = 400,
                 l2: float = 0.01, seed: int = 42):
        self.lr = lr
        self.epochs = epochs
        self.l2 = l2
        self.seed = seed
        self.W: Optional[np.ndarray] = None
        self.b: Optional[np.ndarray] = None
        self.classes_: np.ndarray = np.array([-1, 0, 1])
        self._fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "_NumpyLogisticRegression":
        rng = np.random.default_rng(self.seed)
        X = np.asarray(X, dtype=float)
        # 标准化（仅用训练统计量）
        self._mean = X.mean(axis=0)
        self._std = X.std(axis=0) + 1e-8
        Xn = (X - self._mean) / self._std

        # 映射 y 到 0..K-1
        y_mapped = (np.asarray(y, dtype=int) + 1).clip(0, 2)
        n, d = Xn.shape
        K = 3
        self.W = rng.normal(0, 0.01, (d, K))
        self.b = np.zeros(K)

        Y = np.zeros((n, K))
        Y[np.arange(n), y_mapped] = 1.0

        for _ in range(self.epochs):
            logits = Xn @ self.W + self.b
            P = _softmax(logits)
            grad_logits = (P - Y) / n
            gW = Xn.T @ grad_logits + self.l2 * self.W
            gb = grad_logits.sum(axis=0)
            self.W -= self.lr * gW
            self.b -= self.lr * gb

        self._fitted = True
        return self

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        Xn = (np.asarray(X, dtype=float) - self._mean) / self._std
        logits = Xn @ self.W + self.b
        return _softmax(logits)

    def predict(self, X: np.ndarray) -> np.ndarray:
        return self.classes_[self.predict_proba(X).argmax(axis=1)]


class _ConstantPredictor:
    """样本不足时的兜底常量预测器。"""
    def __init__(self, value: int):
        self.value = value
        self.classes_ = np.array([value])

    def predict(self, X: np.ndarray) -> np.ndarray:
        return np.full(len(X), self.value)

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        out = np.zeros((len(X), 1))
        out[:, 0] = 1.0
        return out


# ============================================================
# 便捷函数
# ============================================================

def train_model(
    X: np.ndarray,
    y: np.ndarray,
    model: str = "auto",
    feature_names: Optional[list[str]] = None,
) -> ModelTrainer:
    """便捷入口：训练并返回 ModelTrainer。"""
    trainer = ModelTrainer(model=model)
    trainer.fit(X, y, feature_names=feature_names)
    return trainer


def _softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)
