"""智能日程注入子模块

提供意图分类、内容模板、时机优化等功能，用于提升日程注入的智能化程度。

核心组件：
    - IntentClassifier: 用户意图分类器
    - ContentTemplateEngine: 动态内容模板引擎
    - InjectOptimizer: 注入时机优化器
    - ActivityStateAnalyzer: 活动状态分析器
    - ConversationContextCache: 对话上下文缓存 (v1.1.0新增)

版本：v1.1.0 (第二阶段 - LLM软注入 + 对话上下文)
"""

from .intent_classifier import IntentClassifier, UserIntent
from .content_template import ContentTemplateEngine
from .inject_optimizer import InjectOptimizer
from .state_analyzer import ActivityStateAnalyzer, ActivityState
from .context_cache import ConversationContextCache, ConversationTurn

__all__ = [
    'IntentClassifier',
    'UserIntent',
    'ContentTemplateEngine',
    'InjectOptimizer',
    'ActivityStateAnalyzer',
    'ActivityState',
    'ConversationContextCache',
    'ConversationTurn',
]

__version__ = '1.1.0'
