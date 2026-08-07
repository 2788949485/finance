
"""回测深度分析包。

从各子模块重导出公共接口，保持向后兼容：
  from app.backtest_analysis import run_full_analysis, run_monte_carlo, ...
"""
from .scoring import calc_profit_factor, calc_recovery_factor, calc_comprehensive_score
from .monte_carlo import run_monte_carlo
from .layered import run_layered_test
from .sensitivity import run_parameter_sensitivity
from .full_analysis import run_full_analysis
from .walk_forward import run_walk_forward
from .cpcv import run_cpcv
from .pbo import run_pbo

__all__ = [
    'calc_profit_factor', 'calc_recovery_factor', 'calc_comprehensive_score',
    'run_monte_carlo', 'run_layered_test', 'run_parameter_sensitivity',
    'run_full_analysis', 'run_walk_forward', 'run_cpcv', 'run_pbo',
]
