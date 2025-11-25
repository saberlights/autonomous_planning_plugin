"""智能日程注入子模块

提供意图分类、内容模板、时机优化等功能，用于提升日程注入的智能化程度。

核心组件：
    - IntentClassifier: 用户意图分类器
    - ContentTemplateEngine: 动态内容模板引擎
    - InjectOptimizer: 注入时机优化器
    - ActivityStateAnalyzer: 活动状态分析器

版本：v1.0.0 (第一阶段 - 渐进式优化)
"""

from .intent_classifier import IntentClassifier, UserIntent
from .content_template import ContentTemplateEngine
from .inject_optimizer import InjectOptimizer
from .state_analyzer import ActivityStateAnalyzer, ActivityState

__all__ = [
    'IntentClassifier',
    'UserIntent',
    'ContentTemplateEngine',
    'InjectOptimizer',
    'ActivityStateAnalyzer',
    'ActivityState',
]

__version__ = '1.0.0'
