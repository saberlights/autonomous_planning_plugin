"""自主规划插件 - 事件处理器模块"""

import asyncio
import time
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

from src.plugin_system import BaseEventHandler, EventType, MaiMessages, CustomEventHandlerResult
from src.common.logger import get_logger

from ..planner.goal_manager import get_goal_manager
from ..planner.schedule_generator import ScheduleGenerator
from ..cache import LRUCache
from ..utils.time_utils import parse_time_window
from .exception_handler import handle_exception, handle_exception_silent

logger = get_logger("autonomous_planning.handlers")

class AutonomousPlannerEventHandler(BaseEventHandler):
    """自主规划事件处理器 - 定期清理过期目标"""

    event_type = EventType.ON_START
    handler_name = "autonomous_planner"
    handler_description = "定期清理过期的日程目标"
    weight = 10
    intercept_message = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.goal_manager = get_goal_manager()
        self.check_task: Optional[asyncio.Task] = None
        self.is_running = False
        self.enabled = self.get_config("plugin.enabled", True)
        self.cleanup_interval = self.get_config("autonomous_planning.cleanup_interval", 3600)  # 每小时清理一次
        logger.info(f"自主规划维护任务初始化完成 (清理间隔: {self.cleanup_interval}秒)")

    async def execute(
        self, message: MaiMessages | None
    ) -> Tuple[bool, bool, Optional[str], Optional[CustomEventHandlerResult], Optional[MaiMessages]]:
        """处理启动事件，启动后台清理循环"""
        if not self.enabled:
            return True, True, None, None, None

        if not self.is_running:
            self.is_running = True
            self.check_task = asyncio.create_task(self._cleanup_loop())
            logger.info("目标清理循环已启动")

        return True, True, None, None, None

    async def _cleanup_loop(self):
        """定期清理过期目标"""
        logger.info("🧹 麦麦目标清理系统启动")

        while self.is_running:
            try:
                await self._cleanup_old_goals()
            except Exception as e:
                logger.error(f"清理目标异常: {e}", exc_info=True)

            # 等待下一个清理周期（使用短间隔检查，支持快速退出）
            for _ in range(int(self.cleanup_interval)):
                if not self.is_running:
                    break
                await asyncio.sleep(1)

        logger.info("🛑 目标清理循环已停止")

    async def shutdown(self):
        """
        优雅停止清理循环

        调用此方法停止后台清理任务
        """
        if self.is_running:
            logger.info("正在停止目标清理循环...")
            self.is_running = False

            # 等待任务结束（最多3秒）
            if self.check_task:
                try:
                    await asyncio.wait_for(self.check_task, timeout=3.0)
                except asyncio.TimeoutError:
                    logger.warning("清理任务停止超时，强制取消")
                    self.check_task.cancel()
                except Exception as e:
                    logger.error(f"停止清理任务异常: {e}")

            logger.info("✅ 目标清理循环已停止")

    @handle_exception("清理旧目标失败: {e}", log_level="error", exc_info=True)
    async def _cleanup_old_goals(self):
        """清理旧目标和过期日程"""
        # 1. 清理过期的日程（昨天及更早的ACTIVE日程）
        expired_schedules = self.goal_manager.cleanup_expired_schedules()
        if expired_schedules > 0:
            logger.info(f"🧹 清理了 {expired_schedules} 个过期日程（昨天及更早）")

        # 2. 清理已完成/已取消的旧目标（保留30天）
        cleanup_days = self.get_config("autonomous_planning.cleanup_old_goals_days", 30)
        cleaned_count = self.goal_manager.cleanup_old_goals(days=cleanup_days)
        if cleaned_count > 0:
            logger.info(f"🧹 清理了 {cleaned_count} 个旧目标（{cleanup_days}天前）")


class ScheduleInjectEventHandler(BaseEventHandler):
    """日程注入事件处理器 - 在LLM调用前注入当前日程信息到prompt"""

    event_type = EventType.POST_LLM  # POST_LLM = 在规划器后、LLM调用前执行
    handler_name = "schedule_inject_handler"
    handler_description = "在LLM调用前注入当前日程信息到prompt"
    weight = 10
    intercept_message = True

    # 时间相关关键词（用于智能判断是否需要注入日程）
    TIME_KEYWORDS = {
        "现在", "当前", "正在", "在做", "在干",
        "今天", "今日", "今早", "今晚",
        "明天", "昨天", "后天", "前天",
        "几点", "什么时候", "多久", "时间",
        "安排", "计划", "日程", "行程",
        "接下来", "等下", "稍后", "之后",
        "早上", "中午", "下午", "晚上", "夜里",
        "忙", "空闲", "有空", "在忙",
        "做什么", "干什么", "要做",
    }

    # P1优化：预编译正则表达式，一次匹配所有关键词
    _TIME_KEYWORDS_PATTERN = __import__('re').compile('|'.join(TIME_KEYWORDS))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enabled = self.get_config("plugin.enabled", True)
        self.inject_schedule = self.get_config("autonomous_planning.schedule.inject_schedule", True)
        self.auto_generate_schedule = self.get_config("autonomous_planning.schedule.auto_generate", True)

        # P2优化：从配置读取缓存参数
        cache_max_size = self.get_config("autonomous_planning.schedule.cache_max_size", 100)
        self._schedule_cache = LRUCache(max_size=cache_max_size)

        # 缓存配置
        self._schedule_cache_ttl = self.get_config("autonomous_planning.schedule.cache_ttl", 300)
        self._cache_cleanup_interval = 600  # 10分钟清理一次
        self._last_cache_cleanup = 0  # 上次清理时间

        # 日程生成锁（防止并发生成）
        self._generate_lock = asyncio.Lock()
        self._last_schedule_check_date = None

        # 🆕 智能注入组件初始化
        try:
            from .inject import (
                IntentClassifier,
                ContentTemplateEngine,
                InjectOptimizer,
                ActivityStateAnalyzer,
                ActivityState,  # 提前导入，避免在execute中导入
                ConversationContextCache,  # v1.1.0新增
            )

            # 读取智能注入配置
            inject_mode = self.get_config(
                "autonomous_planning.schedule.inject.inject_mode", "smart"
            )
            enable_intent_classification = self.get_config(
                "autonomous_planning.schedule.inject.enable_intent_classification", True
            )
            enable_state_analysis = self.get_config(
                "autonomous_planning.schedule.inject.enable_state_analysis", True
            )
            enable_inject_optimization = self.get_config(
                "autonomous_planning.schedule.inject.enable_inject_optimization", True
            )
            casual_inject_prob = self.get_config(
                "autonomous_planning.schedule.inject.casual_chat_inject_probability", 0.5
            )
            context_max_turns = self.get_config(
                "autonomous_planning.schedule.inject.context_max_turns", 3
            )
            context_ttl = self.get_config(
                "autonomous_planning.schedule.inject.context_ttl", 600
            )

            # 保存配置
            self.inject_mode = inject_mode

            # 初始化智能组件
            self.intent_classifier = IntentClassifier() if enable_intent_classification else None
            self.state_analyzer = ActivityStateAnalyzer() if enable_state_analysis else None
            self.content_engine = ContentTemplateEngine(
                self.state_analyzer
            ) if enable_state_analysis else None
            self.inject_optimizer = InjectOptimizer(
                cache_ttl=self._schedule_cache_ttl,
                casual_inject_probability=casual_inject_prob
            ) if enable_inject_optimization else None

            # 🆕 v1.1.0: 初始化对话上下文缓存
            self.context_cache = ConversationContextCache(
                max_turns=context_max_turns,
                ttl=context_ttl
            )

            # 保存 ActivityState 类引用，供 execute 使用
            self.ActivityState = ActivityState

            logger.info(
                f"✅ 智能日程注入组件已加载 "
                f"(模式: {inject_mode}, "
                f"意图分类: {enable_intent_classification}, "
                f"状态分析: {enable_state_analysis}, "
                f"注入优化: {enable_inject_optimization}, "
                f"对话上下文: {context_max_turns}轮/{context_ttl}秒)"
            )
        except ImportError as e:
            logger.warning(f"智能注入组件加载失败，使用传统模式: {e}")
            self.inject_mode = "traditional"
            self.intent_classifier = None
            self.state_analyzer = None
            self.content_engine = None
            self.inject_optimizer = None
            self.context_cache = None
            self.ActivityState = None

        if self.enabled and self.inject_schedule:
            logger.info(f"日程注入功能已启用（缓存TTL: {self._schedule_cache_ttl}秒，最大{cache_max_size}项）")
            if self.auto_generate_schedule:
                logger.info("日程自动生成功能已启用")
            asyncio.create_task(self._preheat_cache())  # 启动缓存预热

    def _get_timezone_now(self):
        """
        获取配置时区的当前时间

        根据插件配置中的时区设置，返回对应时区的当前时间。
        如果pytz模块未安装或时区配置错误，则回退到系统时间。

        Returns:
            datetime.datetime: 当前时间对象
        """
        timezone_str = self.get_config("autonomous_planning.schedule.timezone", "Asia/Shanghai")
        try:
            import pytz
            tz = pytz.timezone(timezone_str)
            return datetime.now(tz)
        except ImportError:
            logger.warning("pytz模块未安装，使用系统时间")
            return datetime.now()
        except Exception as e:
            logger.warning(f"时区处理出错: {e}，使用系统时间")
            return datetime.now()

    @handle_exception_silent("缓存预热失败: {e}", log_level="warning")
    async def _preheat_cache(self):
        """预热缓存 - 启动时提前加载全局日程"""
        await asyncio.sleep(5)  # 等待系统初始化
        logger.info("🔥 开始预热日程缓存...")
        self._get_current_schedule("global")
        logger.info("✅ 日程缓存预热完成")

    @handle_exception("检查今天日程失败: {e}", log_level="warning", default_return=False)
    def _check_today_schedule_exists(self, chat_id: str = "global") -> bool:
        """
        检查今天是否已经有日程

        Args:
            chat_id: 聊天ID，默认为"global"

        Returns:
            True表示今天已有日程，False表示没有
        """
        goal_manager = get_goal_manager()
        goals = goal_manager.get_active_goals(chat_id=chat_id)

        if not goals:
            return False

        # ✅ 获取配置的时区并计算今天的日期字符串
        today_str = self._get_timezone_now().strftime("%Y-%m-%d")

        # 检查是否有今天创建的带time_window的目标
        for goal in goals:
            # 检查是否有time_window（日程类型的标志）
            has_time_window = False
            if goal.parameters and "time_window" in goal.parameters:
                has_time_window = True
            elif goal.conditions and "time_window" in goal.conditions:
                has_time_window = True

            if has_time_window:
                # 检查创建时间是否是今天
                goal_date = None
                if goal.created_at:
                    try:
                        if isinstance(goal.created_at, str):
                            goal_date = goal.created_at.split("T")[0]
                        else:
                            goal_date = goal.created_at.strftime("%Y-%m-%d")
                    except Exception as e:
                        logger.debug(f"解析目标创建时间失败: {goal.created_at} - {e}")

                if goal_date == today_str:
                    logger.debug(f"找到今天的日程目标: {goal.name}")
                    return True

        logger.debug("今天还没有日程")
        return False

    @handle_exception("自动生成日程失败: {e}", log_level="error", exc_info=True, default_return=False)
    async def _auto_generate_today_schedule(self, user_id: str, chat_id: str = "global") -> bool:
        """
        自动生成今天的日程

        注意：此方法假设调用者已持有 _generate_lock，不会再次获取锁

        Args:
            user_id: 用户ID
            chat_id: 聊天ID，默认为"global"

        Returns:
            True表示生成成功，False表示失败
        """
        logger.info("🔄 开始自动生成今天的日程...")

        goal_manager = get_goal_manager()

        # 读取配置并传给ScheduleGenerator
        schedule_config = {
            "use_multi_round": self.get_config("autonomous_planning.schedule.use_multi_round", False),
            "max_rounds": self.get_config("autonomous_planning.schedule.max_rounds", 1),
            "quality_threshold": self.get_config("autonomous_planning.schedule.quality_threshold", 0.80),
            "min_activities": self.get_config("autonomous_planning.schedule.min_activities", 8),
            "max_activities": self.get_config("autonomous_planning.schedule.max_activities", 15),
            "min_description_length": self.get_config("autonomous_planning.schedule.min_description_length", 15),
            "max_description_length": self.get_config("autonomous_planning.schedule.max_description_length", 50),
            "max_tokens": self.get_config("autonomous_planning.schedule.max_tokens", 8192),
            "custom_prompt": self.get_config("autonomous_planning.schedule.custom_prompt", ""),
            "custom_model": {
                "enabled": self.get_config("autonomous_planning.schedule.custom_model.enabled", False),
                "model_name": self.get_config("autonomous_planning.schedule.custom_model.model_name", ""),
                "api_base": self.get_config("autonomous_planning.schedule.custom_model.api_base", ""),
                "api_key": self.get_config("autonomous_planning.schedule.custom_model.api_key", ""),
                "provider": self.get_config("autonomous_planning.schedule.custom_model.provider", "openai"),
                "temperature": self.get_config("autonomous_planning.schedule.custom_model.temperature", 0.7),
            },
        }
        schedule_generator = ScheduleGenerator(goal_manager, config=schedule_config)

        # 生成每日日程
        schedule = await schedule_generator.generate_daily_schedule(
            user_id=user_id,
            chat_id=chat_id,
            use_llm=True
        )

        # 🔧 修复：如果日程已存在（metadata中标记existing=True），跳过应用
        if schedule.metadata and schedule.metadata.get("existing"):
            logger.info(f"📅 今天已有日程（{len(schedule.items)}个活动），跳过应用")
            return True

        # 应用日程
        created_ids = await schedule_generator.apply_schedule(
            schedule=schedule,
            user_id=user_id,
            chat_id=chat_id
        )

        if created_ids:
            logger.info(f"✅ 自动生成日程成功，创建了 {len(created_ids)} 个目标")
            # 清理缓存，强制重新加载
            self._schedule_cache.clear()
            # 🔧 修复：统一使用时区感知时间
            self._last_schedule_check_date = self._get_timezone_now().strftime("%Y-%m-%d")
            return True
        else:
            logger.warning("⚠️ 日程生成失败，没有创建任何目标")
            return False

    @handle_exception("判断是否注入日程失败: {e}", log_level="warning", default_return=False)
    def _should_inject_schedule(self, message: MaiMessages) -> bool:
        """
        智能判断是否需要注入日程信息

        🆕 智能化版本：
        - 使用意图分类器识别用户意图
        - 技术问答/命令执行场景自动跳过
        - 其他意图交由InjectOptimizer进一步判断

        向后兼容：
        - 如果智能组件未加载，回退到关键词匹配模式

        Args:
            message: 消息对象

        Returns:
            是否需要注入日程
        """
        # 提取用户消息文本
        user_message = self._extract_user_message(message)
        if not user_message:
            logger.debug("未能提取到用户消息，跳过日程注入")
            return False

        # 🆕 使用智能意图分类（如果可用）
        if self.intent_classifier:
            intent, confidence = self.intent_classifier.classify(user_message)

            # 技术问答/命令执行 → 直接跳过
            if intent.value in ["tech_question", "command"]:
                logger.debug(f"检测到{intent.value}意图，跳过注入")
                return False

            # 其他意图 → 允许注入（由InjectOptimizer进一步判断）
            logger.debug(
                f"意图分类: {intent.value} (置信度: {confidence:.2f}), "
                f"允许注入（后续由optimizer判断）"
            )
            return True

        # 向后兼容：使用传统关键词匹配
        logger.debug("智能组件未加载，使用传统关键词匹配")

        # P1优化：使用预编译正则一次匹配所有关键词
        match = self._TIME_KEYWORDS_PATTERN.search(user_message)
        if match:
            logger.info(f"检测到时间关键词: {match.group()}，将注入日程")
            return True

        # 规则2：短消息 + 问号（可能是询问）
        if len(user_message) < 5 and ("?" in user_message or "？" in user_message):
            logger.info("检测到短消息问句，将注入日程")
            return True

        # 其他情况不注入
        logger.debug("用户消息不涉及时间，跳过日程注入")
        return False

    def _extract_user_message(self, message: MaiMessages) -> str:
        """提取用户消息文本

        Args:
            message: 消息对象

        Returns:
            用户消息文本，如果提取失败则返回空字符串
        """
        user_message = ""

        # 🔧 优先级1: 从message_base_info提取原始消息
        if hasattr(message, 'message_base_info') and message.message_base_info:
            # 尝试多个可能的字段
            user_message = (
                message.message_base_info.get('message') or
                message.message_base_info.get('original_message') or
                message.message_base_info.get('content') or
                ""
            )
            if user_message:
                logger.debug(f"从message_base_info提取到用户消息: '{str(user_message)[:50]}...'")
                return str(user_message)

        # 🔧 优先级2: 从raw_message提取（可能更原始）
        if hasattr(message, 'raw_message') and message.raw_message:
            # raw_message可能更接近原始消息
            raw = str(message.raw_message)
            # 如果raw_message不包含聊天记录标记，则使用它
            if '群里正在进行的聊天内容' not in raw and len(raw) < 200:
                logger.debug(f"从raw_message提取到用户消息: '{raw[:50]}...'")
                return raw

        # 🔧 优先级3: 从plain_text提取（但可能包含聊天记录）
        if hasattr(message, 'plain_text') and message.plain_text:
            plain = str(message.plain_text)
            # 如果plain_text很短且不包含聊天记录，则使用
            if '群里正在进行的聊天内容' not in plain and len(plain) < 200:
                logger.debug(f"从plain_text提取到用户消息: '{plain[:50]}...'")
                return plain

        logger.warning("未能提取到有效的用户消息")
        return ""

    def _get_user_id(self, message: MaiMessages) -> str:
        """获取用户ID

        Args:
            message: 消息对象

        Returns:
            用户ID，如果获取失败则返回'unknown'
        """
        if hasattr(message, 'message_base_info') and message.message_base_info:
            return str(message.message_base_info.get('user_id', 'unknown'))
        return 'unknown'

    def _build_smart_inject_prompt(
        self,
        current_activity: str,
        description: str,
        future_activities: List[Tuple[str, str]],
        user_message: str,
        activity_type: Optional[str] = None
    ) -> str:
        """🆕 v1.1.0: 构建智能注入prompt - LLM自判断版本

        核心设计：
        1. 提供日程信息作为"可选上下文"
        2. 根据用户消息类型给出使用指导
        3. 让LLM自己决定是否使用、如何使用

        Args:
            current_activity: 当前活动名称
            description: 活动描述
            future_activities: 未来活动列表 [(时间, 活动名)]
            user_message: 用户消息
            activity_type: 活动类型（可选）

        Returns:
            注入的prompt内容
        """
        # 分析用户消息类型（轻量级预判，辅助LLM）
        msg_lower = user_message.lower()

        # 直接询问当前状态
        is_direct_query = any(kw in msg_lower for kw in [
            "在干嘛", "做什么", "忙吗", "在做", "正在",
            "日程", "计划", "安排", "行程",
            "现在", "当前", "这会儿"
        ])

        # 询问未来计划
        is_future_query = any(kw in msg_lower for kw in [
            "接下来", "等下", "稍后", "之后", "待会",
            "明天", "今晚", "晚上", "下午", "上午"
        ])

        # 问候语
        is_greeting = any(kw in msg_lower for kw in [
            "早上好", "晚上好", "早安", "晚安",
            "你好", "hi", "hello", "嗨"
        ])

        # 技术问题
        is_tech_question = any(kw in msg_lower for kw in [
            "怎么", "如何", "为什么", "什么是",
            "报错", "错误", "bug", "异常",
            "代码", "配置", "安装", "调试"
        ])

        # 命令执行
        is_command = user_message.startswith('/') or user_message.startswith('sudo')

        # 构建prompt
        prompt_parts = []

        # 1. 日程信息部分
        prompt_parts.append("【可选上下文 - Bot的当前日程】")
        prompt_parts.append(f"现在：{current_activity}（{description}）")

        # 未来活动
        if future_activities:
            max_show = self.get_config(
                "autonomous_planning.schedule.inject.max_future_activities", 3
            )
            prompt_parts.append("接下来的安排:")
            for i, (time_str, activity_name) in enumerate(future_activities[:max_show]):
                prompt_parts.append(f"  {time_str} - {activity_name}")

        prompt_parts.append("")  # 空行

        # 2. 使用指导部分（根据消息类型）
        if is_command:
            prompt_parts.append("⚠️ 用户正在执行命令，请忽略以上日程信息，专注处理命令。")
        elif is_tech_question:
            prompt_parts.append("⚠️ 用户在询问技术问题，请忽略以上日程信息，专注回答技术内容。")
        elif is_direct_query:
            prompt_parts.append("💡 用户直接询问当前状态，请如实告知当前活动及状态。")
        elif is_future_query:
            prompt_parts.append("💡 用户询问未来计划，请自然地介绍后续安排。")
        elif is_greeting:
            prompt_parts.append("💡 用户在问候，可以自然地顺便提一下今天的计划（可选，不要强行提及）。")
        else:
            prompt_parts.append("💡 以上是Bot当前的日程信息，仅供参考。")
            prompt_parts.append("   - 如果与用户问题相关，可以自然提及")
            prompt_parts.append("   - 如果不相关，请完全忽略此信息")
            prompt_parts.append("   - 不要为了提及日程而刻意转移话题")

        prompt_parts.append("")  # 空行
        prompt_parts.append("---")  # 分隔符
        prompt_parts.append("")

        return "\n".join(prompt_parts)

    async def execute(
        self, message: MaiMessages | None
    ) -> Tuple[bool, bool, Optional[str], Optional[CustomEventHandlerResult], Optional[MaiMessages]]:
        """执行日程注入（v1.1.0 - LLM软注入 + 对话上下文）

        🆕 v1.1.0 新特性：
        1. LLM软注入模式 - 让LLM自己判断是否使用日程信息
        2. 对话上下文缓存 - 支持连续多轮对话
        3. 简化状态分析 - 由LLM负责理解状态

        支持3种模式：
        - smart: LLM自判断（推荐，零额外成本）
        - rule: 规则引擎判断（原v3.2.0智能模式）
        - traditional: 固定注入（向后兼容）

        向后兼容：
        - 所有新功能都是可选的
        - 组件加载失败自动降级
        """
        if not self.enabled or not self.inject_schedule or not message or not message.llm_prompt:
            return True, True, None, None, None

        try:
            chat_id = message.stream_id if hasattr(message, 'stream_id') else None
            if not chat_id:
                return True, True, None, None, None

            # 提取用户消息和用户ID
            user_message = self._extract_user_message(message)
            user_id = self._get_user_id(message)

            # 🆕 检查对话上下文：判断是否在连续讨论日程话题
            context_continue_inject = False
            context_reason = None
            if self.context_cache:
                context_continue_inject, context_reason = self.context_cache.should_continue_inject(
                    user_id, None  # 当前活动稍后获取
                )

            # P0修复：检查今天是否有日程，没有则自动生成（原子化操作）
            if self.auto_generate_schedule:
                # 🔧 修复：统一使用 _get_timezone_now() 处理时区
                today_str = self._get_timezone_now().strftime("%Y-%m-%d")

                # 使用锁确保检查+生成的原子性，防止竞态条件
                async with self._generate_lock:
                    # 只在今天还没检查过的情况下检查
                    if self._last_schedule_check_date != today_str:
                        has_schedule = self._check_today_schedule_exists(chat_id="global")

                        if not has_schedule:
                            logger.info("📅 今天还没有日程，准备自动生成...")

                            # 获取用户ID
                            user_id = "system"
                            if hasattr(message, 'message_base_info') and message.message_base_info:
                                user_id = message.message_base_info.get('user_id', 'system')

                            # P0修复：添加超时保护（可配置，默认3分钟）
                            generation_timeout = self.get_config("autonomous_planning.schedule.generation_timeout", 180.0)
                            generation_task = None
                            try:
                                # 🆕 创建任务以便超时时主动取消
                                generation_task = asyncio.create_task(
                                    self._auto_generate_today_schedule(user_id, chat_id="global")
                                )
                                generation_success = await asyncio.wait_for(
                                    generation_task,
                                    timeout=generation_timeout
                                )
                            except asyncio.TimeoutError:
                                logger.error(f"⏰ 日程生成超时（{generation_timeout}秒），跳过本次生成")
                                # 🆕 P0级：超时后主动取消任务，避免后台继续运行
                                if generation_task and not generation_task.done():
                                    generation_task.cancel()
                                    try:
                                        await generation_task
                                    except asyncio.CancelledError:
                                        logger.debug("已取消超时的日程生成任务")
                                generation_success = False
                            except Exception as e:
                                logger.error(f"日程生成异常: {e}", exc_info=True)
                                generation_success = False

                            if generation_success:
                                logger.info("✅ 日程自动生成完成，继续注入")
                            else:
                                logger.warning("⚠️ 日程自动生成失败")
                        else:
                            logger.debug("今天已有日程，跳过自动生成")

                        # 更新检查日期（无论是否生成成功）
                        self._last_schedule_check_date = today_str

            # 获取当前日程（现在返回所有未来活动列表）
            current_activity, current_description, all_future_activities, activity_type = self._get_current_schedule(chat_id)

            # 🆕 更新对话上下文缓存中的当前活动信息
            if self.context_cache and context_continue_inject:
                # 如果之前判断需要继续注入，但活动已变化，则更新判断
                context_continue_inject, context_reason = self.context_cache.should_continue_inject(
                    user_id, current_activity
                )

            # 没有当前活动，尝试记录对话但不注入
            if not current_activity:
                if self.context_cache:
                    self.context_cache.add_turn(
                        user_id=user_id,
                        user_message=user_message,
                        intent=None,
                        injected=False,
                        activity=None
                    )
                return True, True, None, None, None

            # 🆕 模式选择：smart / rule / traditional
            inject_content = None
            injected = False
            detected_intent = None

            if self.inject_mode == "smart":
                # ============================================================
                # 🆕 v1.1.0: LLM软注入模式（推荐）
                # ============================================================
                # 特点：
                # 1. 零额外成本（不调用额外LLM）
                # 2. 准确率更高（LLM理解力强）
                # 3. 简化状态分析（LLM自己判断）

                # 轻量级预判：技术问答/命令直接跳过
                msg_lower = user_message.lower()
                is_command = user_message.startswith('/') or user_message.startswith('sudo')
                is_tech = any(kw in msg_lower for kw in ["怎么", "如何", "报错", "错误", "bug", "代码", "配置"])

                if is_command or is_tech:
                    # 技术/命令场景，不注入
                    logger.debug(f"Smart模式：检测到技术/命令场景，跳过注入")
                    if self.context_cache:
                        self.context_cache.add_turn(
                            user_id=user_id,
                            user_message=user_message,
                            intent="tech_or_command",
                            injected=False,
                            activity=current_activity
                        )
                else:
                    # 🆕 对话上下文增强：连续对话中强制注入
                    if context_continue_inject:
                        logger.info(f"📖 对话上下文触发注入: {context_reason}")

                    # 使用smart模式prompt
                    inject_content = self._build_smart_inject_prompt(
                        current_activity=current_activity,
                        description=current_description,
                        future_activities=all_future_activities,
                        user_message=user_message,
                        activity_type=activity_type
                    )
                    injected = True
                    logger.info(f"✅ Smart注入: {current_activity}")

            elif self.inject_mode == "rule" and self.intent_classifier and self.inject_optimizer:
                # ============================================================
                # 规则引擎模式（原v3.2.0智能模式）
                # ============================================================
                intent, confidence = self.intent_classifier.classify(user_message)
                detected_intent = intent.value

                # 🆕 对话上下文增强
                if context_continue_inject:
                    logger.info(f"📖 对话上下文触发注入: {context_reason}")
                    should_inject = True
                    skip_reason = None
                else:
                    # 使用InjectOptimizer判断
                    should_inject, skip_reason = self.inject_optimizer.should_inject(
                        user_id, intent, current_activity, confidence
                    )

                if not should_inject:
                    logger.debug(f"Rule模式：InjectOptimizer决定跳过注入: {skip_reason}")
                    if self.context_cache:
                        self.context_cache.add_turn(
                            user_id=user_id,
                            user_message=user_message,
                            intent=detected_intent,
                            injected=False,
                            activity=current_activity
                        )
                else:
                    # 使用ContentTemplateEngine生成
                    if self.content_engine:
                        inject_content = self.content_engine.build_inject_content(
                            intent=intent,
                            current_activity=current_activity,
                            current_description=current_description,
                            activity_state=None,
                            state_desc=current_description,
                            next_activities=all_future_activities
                        )
                        injected = True
                        # 记录注入历史
                        if self.inject_optimizer:
                            self.inject_optimizer.record_injection(
                                user_id, current_activity, inject_content or "", intent
                            )
                        logger.info(
                            f"✅ Rule注入: intent={intent.value}, "
                            f"confidence={confidence:.2f}"
                        )

            else:
                # ============================================================
                # 传统模式（向后兼容）
                # ============================================================
                inject_content = f"【当前状态】\n这会儿正{current_activity}"
                if current_description:
                    inject_content += f"（{current_description}）"
                inject_content += f"\n回复时可以自然提到当前在做什么，不要刻意强调。"
                if all_future_activities:
                    next_time, next_activity = all_future_activities[0]
                    inject_content += f"\n等下{next_time}要{next_activity}。"
                inject_content += "\n"
                injected = True
                logger.info(f"✅ Traditional注入: {current_activity}")

            # 🆕 记录到对话上下文缓存
            if self.context_cache:
                self.context_cache.add_turn(
                    user_id=user_id,
                    user_message=user_message,
                    intent=detected_intent,
                    injected=injected,
                    activity=current_activity
                )

            # 注入日程信息到prompt
            if inject_content:
                original_prompt = str(message.llm_prompt)
                new_prompt = inject_content + "\n" + original_prompt
                message.modify_llm_prompt(new_prompt, suppress_warning=True)

            return True, True, None, None, message

        except Exception as e:
            logger.error(f"注入日程信息失败: {e}", exc_info=True)
            return True, True, None, None, None

    def _cleanup_expired_cache(self, current_time: float):
        """清理过期的缓存项（P0修复：线程安全）"""
        # 使用锁保护，防止与并发的get/set操作冲突
        with self._schedule_cache._lock:
            expired_keys = []

            # 使用list()创建快照避免迭代时修改
            for key, (_, cached_time) in list(self._schedule_cache.cache.items()):
                if current_time - cached_time > self._schedule_cache_ttl:
                    expired_keys.append(key)

            for key in expired_keys:
                if key in self._schedule_cache.cache:
                    del self._schedule_cache.cache[key]

            if expired_keys:
                logger.debug(f"清理了 {len(expired_keys)} 个过期缓存项")

    def _get_current_schedule(self, chat_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str], List[Tuple[str, str]], Optional[str]]:
        """
        获取当前日程信息（带优化缓存）

        优化：
        1. 缓存TTL从30秒提升到5分钟
        2. 缓存键改为按小时（而非5分钟窗口），提高命中率
        3. 定期清理过期缓存，避免内存泄漏

        Returns:
            (当前活动, 活动描述, 所有未来活动列表, 当前活动类型)
            其中未来活动列表格式: [(时间, 活动名), ...]
        """
        import time

        # 获取当前时间
        # 🔧 修复：统一使用时区感知时间
        now = self._get_timezone_now()
        current_hour = now.hour
        current_minute = now.minute
        current_time = time.time()

        # P1修复：按15分钟窗口缓存（而非按小时），提高精度同时保持命中率
        # 原因：同一小时内活动可能变化，但15分钟内基本稳定
        time_window = (current_hour * 60 + current_minute) // 15
        cache_key = f"{chat_id or 'global'}_{now.strftime('%Y%m%d')}_{time_window}"

        # 定期清理过期缓存（避免内存无限增长）
        if current_time - self._last_cache_cleanup > self._cache_cleanup_interval:
            self._cleanup_expired_cache(current_time)
            self._last_cache_cleanup = current_time

        # 检查缓存是否有效
        if cache_key in self._schedule_cache:
            cached_result, cached_time = self._schedule_cache[cache_key]
            if current_time - cached_time < self._schedule_cache_ttl:
                # 缓存命中
                return cached_result

        # 缓存过期或不存在，重新查询
        try:
            goal_manager = get_goal_manager()

            # 先尝试获取全局日程（chat_id="global"）
            goals = goal_manager.get_active_goals(chat_id="global")

            # 如果没有全局日程，再尝试获取当前聊天的日程
            if not goals and chat_id:
                goals = goal_manager.get_active_goals(chat_id=chat_id)

            if not goals:
                result = (None, None, [], None)
                self._schedule_cache[cache_key] = (result, current_time)
                return result

            current_time_minutes = current_hour * 60 + current_minute
            today_date = now.strftime("%Y-%m-%d")

            # 找到有时间窗口的目标，优先选择今天创建的
            scheduled_goals = []
            for goal in goals:
                # 向后兼容：优先从parameters读取time_window，其次从conditions读取
                time_window = None
                if goal.parameters and "time_window" in goal.parameters:
                    time_window = goal.parameters.get("time_window")
                elif goal.conditions:
                    time_window = goal.conditions.get("time_window")

                if time_window:
                    # 检查是否是今天创建的任务
                    # created_at 可能是 datetime 对象或字符串
                    is_today = False
                    if goal.created_at:
                        if isinstance(goal.created_at, str):
                            is_today = goal.created_at.startswith(today_date)
                        else:
                            # datetime 对象
                            is_today = goal.created_at.strftime("%Y-%m-%d") == today_date
                    scheduled_goals.append((goal, time_window, is_today))

            if not scheduled_goals:
                result = (None, None, [], None)
                self._schedule_cache[cache_key] = (result, current_time)
                return result

            # 排序：按开始时间（兼容新旧格式）
            def get_start_minutes(item):
                goal, time_window, is_today = item
                if not time_window or len(time_window) < 2:
                    return 0
                start_val = time_window[0]
                # 判断格式：end_val > 24 说明是分钟格式
                if time_window[1] > 24:
                    return start_val
                else:
                    return start_val * 60

            scheduled_goals.sort(key=get_start_minutes)

            # 查找当前活动（仅选择今天创建的任务）
            current_activity = None
            current_description = None
            current_activity_type = None  # 🆕 新增：活动类型
            current_goal_created_at = None

            for goal, time_window, is_today in scheduled_goals:
                start_minutes, end_minutes = parse_time_window(time_window)
                if start_minutes is None:
                    continue

                # 处理跨夜时间窗口（end_minutes > 1440）
                # 例如 23:00-01:00 会被转换为 [1380, 1500]
                is_in_window = False
                if end_minutes > 1440:
                    # 跨夜任务：检查当前时间是否在开始时间之后，或在（结束时间-1440）之前
                    # 例如：1380 <= 1410 < 1500 或 0 <= 30 < 60
                    is_in_window = (start_minutes <= current_time_minutes < 1440) or (0 <= current_time_minutes < (end_minutes - 1440))
                else:
                    # 普通任务
                    is_in_window = start_minutes <= current_time_minutes < end_minutes

                if is_in_window:
                    # 仅选择今天创建的任务
                    if is_today:
                        # 如果有多个今天的任务，选择创建时间最新的
                        if current_activity is None or (goal.created_at and goal.created_at > current_goal_created_at):
                            current_activity = goal.name
                            current_description = goal.description
                            current_activity_type = goal.goal_type  # 🆕 获取真实活动类型
                            current_goal_created_at = goal.created_at

            # 🆕 收集所有未来活动（优先选择今天的任务）
            all_future_activities = []
            for goal, time_window, is_today in scheduled_goals:
                start_val = time_window[0] if len(time_window) > 0 else 0
                end_val = time_window[1] if len(time_window) > 1 else start_val + 60

                # 判断格式并转换
                if end_val <= 24:
                    start_minutes = start_val * 60
                else:
                    start_minutes = start_val

                if start_minutes > current_time_minutes:
                    # 转换为时:分格式
                    hour = start_minutes // 60
                    minute = start_minutes % 60
                    time_str = f"{hour:02d}:{minute:02d}"

                    # 添加到列表（今天的任务排在前面）
                    if is_today:
                        all_future_activities.append((time_str, goal.name))
                    else:
                        # 非今天的任务添加到末尾
                        all_future_activities.append((time_str, goal.name))

            result = (current_activity, current_description, all_future_activities, current_activity_type)
            self._schedule_cache[cache_key] = (result, current_time)
            return result

        except Exception as e:
            logger.debug(f"获取日程信息失败: {e}")
            result = (None, None, [], None)
            self._schedule_cache[cache_key] = (result, current_time)
            return result


# ===== Commands =====

