"""注入时机优化器模块

智能判断是否应该注入日程信息，防止无效注入和重复骚扰用户。
通过记录注入历史、检查时间间隔等方式，优化注入策略。
"""

import random
import time
from typing import Dict, Optional, Tuple

from src.common.logger import get_logger

from .intent_classifier import UserIntent

logger = get_logger("autonomous_planning.inject_optimizer")


class InjectOptimizer:
    """注入时机优化器

    负责：
    1. 判断是否应该注入日程信息
    2. 防止短时间内重复注入相同内容
    3. 根据意图类型智能决策
    4. 管理注入历史缓存

    Attributes:
        inject_history: 用户注入历史缓存
        cache_ttl: 缓存过期时间（秒）
        casual_inject_probability: 闲聊时注入概率
    """

    def __init__(
        self,
        cache_ttl: int = 300,
        casual_inject_probability: float = 0.5
    ):
        """初始化注入优化器

        Args:
            cache_ttl: 缓存过期时间（秒），默认300秒（5分钟）
            casual_inject_probability: 闲聊时注入概率（0-1），默认0.5
        """
        # 用户注入历史缓存
        # 结构：{user_id: {last_time, last_activity, last_content, count}}
        self.inject_history: Dict[str, Dict] = {}

        self.cache_ttl = cache_ttl
        self.casual_inject_probability = casual_inject_probability

        logger.info(
            f"注入优化器初始化完成: TTL={cache_ttl}秒, "
            f"闲聊注入概率={casual_inject_probability}"
        )

    def should_inject(
        self,
        user_id: str,
        intent: UserIntent,
        current_activity: Optional[str],
        confidence: float = 1.0
    ) -> Tuple[bool, Optional[str]]:
        """判断是否应该注入日程信息

        决策逻辑：
        1. 技术问答/命令执行 → 不注入
        2. 无当前活动且非询问意图 → 不注入
        3. 短时间内重复注入 → 不注入
        4. 闲聊意图 → 概率性注入
        5. 其他 → 注入

        Args:
            user_id: 用户ID
            intent: 用户意图
            current_activity: 当前活动名称
            confidence: 意图分类的置信度（0-1）

        Returns:
            (是否注入, 跳过原因)

        Examples:
            >>> optimizer = InjectOptimizer()
            >>> optimizer.should_inject("user1", UserIntent.QUERY_CURRENT, "学习")
            (True, None)

            >>> optimizer.should_inject("user1", UserIntent.TECH_QUESTION, "学习")
            (False, "技术问答场景，跳过注入")
        """
        current_time = time.time()

        # 规则1：技术问答/命令执行 → 不注入
        if intent in [UserIntent.TECH_QUESTION, UserIntent.COMMAND_EXECUTION]:
            reason = f"{intent.value}场景，跳过注入"
            logger.debug(f"注入决策: user={user_id}, 拒绝, 原因={reason}")
            return False, reason

        # 规则2：无当前活动且非询问意图 → 不注入
        if not current_activity and intent not in [
            UserIntent.QUERY_CURRENT,
            UserIntent.QUERY_FUTURE
        ]:
            reason = "无当前活动且非询问意图"
            logger.debug(f"注入决策: user={user_id}, 拒绝, 原因={reason}")
            return False, reason

        # 规则3：检查注入历史，防止短时间重复
        if user_id in self.inject_history:
            history = self.inject_history[user_id]
            last_time = history.get("last_time", 0)
            last_activity = history.get("last_activity", "")
            last_intent = history.get("last_intent", "")  # 🆕 获取上次意图
            time_elapsed = current_time - last_time

            # 🆕 优化：只有当活动相同 AND 意图相同时才认为是重复
            # 例外：query_future（询问未来）意图不受限制，因为用户可能询问不同时间段
            #      （如先问"下午呢"再问"晚上呢"，应该返回不同的内容）
            is_same_intent = last_intent == intent.value
            is_same_activity = last_activity == current_activity

            # 🆕 query_future 意图豁免：允许短时间内多次询问未来计划
            if intent == UserIntent.QUERY_FUTURE:
                logger.debug(f"query_future意图豁免防重复检查")
            elif time_elapsed < self.cache_ttl and is_same_activity and is_same_intent:
                reason = f"短时间内重复注入（{int(time_elapsed)}秒前已注入相同活动和意图）"
                logger.debug(f"注入决策: user={user_id}, 拒绝, 原因={reason}")
                return False, reason

        # 规则4：闲聊意图 → 概率性注入
        if intent == UserIntent.CASUAL_CHAT:
            if random.random() > self.casual_inject_probability:
                reason = f"闲聊意图，随机决策不注入（概率={self.casual_inject_probability}）"
                logger.debug(f"注入决策: user={user_id}, 拒绝, 原因={reason}")
                return False, reason

        # 规则5：低置信度意图 → 谨慎注入
        # 🔧 优化：降低阈值从0.5到0.4，给改进后的意图识别更多机会
        if confidence < 0.4:
            reason = f"意图置信度过低（{confidence:.2f}），谨慎起见不注入"
            logger.debug(f"注入决策: user={user_id}, 拒绝, 原因={reason}")
            return False, reason

        # 默认：允许注入
        logger.debug(
            f"注入决策: user={user_id}, 允许, "
            f"intent={intent.value}, confidence={confidence:.2f}"
        )
        return True, None

    def record_injection(
        self,
        user_id: str,
        activity: str,
        content: str,
        intent: Optional['UserIntent'] = None  # 🆕 添加意图参数
    ):
        """记录注入事件

        Args:
            user_id: 用户ID
            activity: 活动名称
            content: 注入的内容
            intent: 用户意图（可选）
        """
        current_time = time.time()

        if user_id not in self.inject_history:
            self.inject_history[user_id] = {
                "last_time": current_time,
                "last_activity": activity,
                "last_content": content,
                "last_intent": intent.value if intent else "",  # 🆕 记录意图
                "count": 1,
            }
        else:
            history = self.inject_history[user_id]
            history["last_time"] = current_time
            history["last_activity"] = activity
            history["last_content"] = content
            history["last_intent"] = intent.value if intent else ""  # 🆕 记录意图
            history["count"] = history.get("count", 0) + 1

        logger.debug(
            f"记录注入: user={user_id}, activity={activity}, "
            f"total_count={self.inject_history[user_id]['count']}"
        )

    def cleanup_expired_cache(self):
        """清理过期的缓存项

        删除超过TTL的历史记录，防止内存无限增长。
        """
        current_time = time.time()
        expired_users = []

        for user_id, history in self.inject_history.items():
            last_time = history.get("last_time", 0)
            if current_time - last_time > self.cache_ttl * 2:  # 2倍TTL后清理
                expired_users.append(user_id)

        for user_id in expired_users:
            del self.inject_history[user_id]

        if expired_users:
            logger.debug(f"清理过期缓存: 删除{len(expired_users)}个用户的历史记录")

    def get_user_inject_stats(self, user_id: str) -> Optional[Dict]:
        """获取用户的注入统计信息

        Args:
            user_id: 用户ID

        Returns:
            注入统计字典，如果用户没有历史记录则返回None

        Examples:
            >>> optimizer.get_user_inject_stats("user1")
            {
                "last_time": 1234567890.0,
                "last_activity": "学习",
                "count": 5,
                "time_since_last": 120.5
            }
        """
        if user_id not in self.inject_history:
            return None

        history = self.inject_history[user_id]
        current_time = time.time()
        last_time = history.get("last_time", 0)

        return {
            "last_time": last_time,
            "last_activity": history.get("last_activity", ""),
            "count": history.get("count", 0),
            "time_since_last": current_time - last_time,
        }

    def reset_user_history(self, user_id: str):
        """重置指定用户的注入历史

        Args:
            user_id: 用户ID
        """
        if user_id in self.inject_history:
            del self.inject_history[user_id]
            logger.info(f"重置用户注入历史: user={user_id}")

    def get_total_inject_count(self) -> int:
        """获取总注入次数（所有用户）

        Returns:
            总注入次数
        """
        return sum(h.get("count", 0) for h in self.inject_history.values())

    def get_active_users_count(self) -> int:
        """获取活跃用户数量（有注入历史的用户）

        Returns:
            活跃用户数量
        """
        return len(self.inject_history)

    def set_casual_inject_probability(self, probability: float):
        """动态调整闲聊注入概率

        Args:
            probability: 新的概率值（0-1）

        Raises:
            ValueError: 如果概率值不在0-1范围内
        """
        if not 0 <= probability <= 1:
            raise ValueError(f"概率值必须在0-1之间，当前值: {probability}")

        self.casual_inject_probability = probability
        logger.info(f"闲聊注入概率已调整为: {probability}")
