"""用户意图分类器模块

基于规则的轻量级意图分类，用于判断用户消息的意图类型。
通过关键词匹配和权重评分，准确识别用户是否在询问当前状态、未来计划等。
"""

import re
from enum import Enum
from typing import Tuple, Optional
from dataclasses import dataclass

from src.common.logger import get_logger

logger = get_logger("autonomous_planning.intent_classifier")


@dataclass
class TimeRange:
    """时间范围数据类"""
    name: str           # 时间段名称（如"下午"）
    start_hour: int     # 开始小时
    end_hour: int       # 结束小时


class UserIntent(Enum):
    """用户意图类型枚举"""
    QUERY_CURRENT = "query_current"          # 询问当前状态
    QUERY_FUTURE = "query_future"            # 询问未来计划
    CASUAL_CHAT = "casual_chat"              # 闲聊寒暄
    TECH_QUESTION = "tech_question"          # 技术问答
    COMMAND_EXECUTION = "command"            # 命令执行
    UNKNOWN = "unknown"                      # 未知意图


class IntentClassifier:
    """意图分类器 - 基于规则的轻量级实现

    通过关键词匹配和权重评分，识别用户消息的意图类型。
    性能目标：< 10ms，准确率 > 85%

    Attributes:
        current_keywords: 询问当前状态的关键词集合
        future_keywords: 询问未来计划的关键词集合
        tech_keywords: 技术问答的关键词集合
        command_patterns: 命令执行的正则表达式列表
        casual_keywords: 闲聊寒暄的关键词集合
    """

    def __init__(self):
        """初始化意图分类器，预定义关键词库"""

        # 询问当前状态的关键词（高权重）
        self.current_keywords = {
            "现在", "当前", "正在", "在做", "在干", "在忙",
            "这会儿", "此刻", "目前", "眼下",
            "做什么", "干什么", "忙什么",
            "在吗", "有空吗", "忙吗", "空闲吗",
            "刚", "刚才", "刚刚",  # 🆕 表示最近的状态
            "在", "在哪", "去哪",  # 🆕 询问位置/状态
        }

        # 🆕 活动相关动词（用于识别状态询问）
        self.activity_verbs = {
            "吃", "睡", "玩", "聊", "看", "学", "写", "做",
            "休息", "工作", "运动", "看剧", "追剧",
            "聊天", "打游戏", "学习", "写代码",
        }

        # 询问未来计划的关键词
        self.future_keywords = {
            "接下来", "等下", "稍后", "之后", "待会", "一会儿",
            "明天", "今晚", "晚上", "下午",
            "打算", "计划", "安排", "准备",
            "要做", "会做", "打算做",
            "然后", "后面", "接着",
        }

        # 技术问答的关键词
        self.tech_keywords = {
            "怎么", "如何", "为什么", "什么是",
            "配置", "安装", "设置", "调试",
            "错误", "报错", "异常", "bug",
            "代码", "程序", "脚本", "函数",
            "数据库", "服务器", "API", "接口",
            "版本", "更新", "升级", "兼容",
        }

        # 命令执行的正则表达式
        self.command_patterns = [
            r"^/\w+",           # /command 格式
            r"^sudo\s+",        # sudo 命令
            r"^git\s+",         # git 命令
            r"^npm\s+",         # npm 命令
            r"^python\s+",      # python 命令
            r"^cd\s+",          # cd 命令
            r"^ls\s+",          # ls 命令
        ]

        # 闲聊寒暄的关键词（低优先级）
        self.casual_keywords = {
            "你好", "hi", "hello", "嗨",
            "早", "晚安", "再见", "拜拜",
            "哈哈", "呵呵", "嘿嘿",
            "好的", "ok", "嗯", "嗯嗯",
            "谢谢", "多谢", "感谢",
        }

        # 🆕 时间段映射表
        self.time_ranges = {
            "凌晨": TimeRange("凌晨", 0, 6),
            "早上": TimeRange("早上", 6, 9),
            "上午": TimeRange("上午", 9, 12),
            "中午": TimeRange("中午", 11, 14),
            "下午": TimeRange("下午", 14, 18),
            "傍晚": TimeRange("傍晚", 17, 19),
            "晚上": TimeRange("晚上", 18, 23),
            "深夜": TimeRange("深夜", 22, 24),
        }

        # 预编译正则表达式（性能优化）
        self._command_regex = re.compile('|'.join(self.command_patterns))

        logger.info("意图分类器初始化完成")

    def classify(self, message: str) -> Tuple[UserIntent, float]:
        """分类用户消息意图

        分类优先级（从高到低）：
        1. 命令执行（最高优先级）
        2. 技术问答
        3. 询问当前状态
        4. 询问未来计划
        5. 闲聊寒暄（默认）

        Args:
            message: 用户消息文本

        Returns:
            (意图类型, 置信度分数 0-1)

        Examples:
            >>> classifier = IntentClassifier()
            >>> classifier.classify("你现在在干嘛？")
            (UserIntent.QUERY_CURRENT, 0.95)

            >>> classifier.classify("/help")
            (UserIntent.COMMAND_EXECUTION, 1.0)
        """
        if not message or not message.strip():
            return UserIntent.UNKNOWN, 0.0

        message = message.strip().lower()

        # 🔍 调试：记录原始消息（前50字符）
        logger.debug(f"意图分类输入: '{message[:50]}...'")

        # 1. 命令检测（最高优先级，置信度1.0）
        if self._command_regex.match(message):
            logger.debug(f"检测到命令: {message[:20]}...")
            return UserIntent.COMMAND_EXECUTION, 1.0

        # 2. 技术问答检测
        tech_score = self._calculate_keyword_score(message, self.tech_keywords)
        if tech_score > 0.5:
            logger.debug(f"检测到技术问答: score={tech_score:.2f}")
            return UserIntent.TECH_QUESTION, tech_score

        # 3. 询问当前状态检测
        current_score = self._calculate_keyword_score(message, self.current_keywords)

        # 特殊增强：包含"正在/在做/现在"等强指示词，分数加权
        if any(kw in message for kw in ["正在", "在做", "在干", "现在", "当前"]):
            current_score = min(1.0, current_score * 1.5)

        # 🆕 反问句检测："你不是...吗" 模式
        if ("你不是" in message or "不是" in message) and ("吗" in message or "?" in message or "？" in message):
            # 检查是否包含活动动词
            if any(verb in message for verb in self.activity_verbs):
                logger.debug(f"检测到反问句 + 活动动词，判定为询问当前状态")
                current_score = max(current_score, 0.85)

        # 🆕 活动动词 + 时间词检测："刚吃完"、"在聊天" 等
        if any(verb in message for verb in self.activity_verbs):
            if any(kw in message for kw in ["刚", "刚才", "刚刚", "在"]):
                logger.debug(f"检测到活动动词 + 时间词，判定为询问当前状态")
                current_score = max(current_score, 0.80)

        if current_score > 0.4:
            logger.debug(f"检测到询问当前状态: score={current_score:.2f}")
            return UserIntent.QUERY_CURRENT, current_score

        # 4. 询问未来计划检测
        future_score = self._calculate_keyword_score(message, self.future_keywords)

        # 特殊增强：包含"接下来/等下/计划"等强指示词，分数加权
        if any(kw in message for kw in ["接下来", "等下", "计划", "安排", "打算"]):
            future_score = min(1.0, future_score * 1.5)

        if future_score > 0.4:
            logger.debug(f"检测到询问未来计划: score={future_score:.2f}")
            return UserIntent.QUERY_FUTURE, future_score

        # 5. 闲聊寒暄检测
        casual_score = self._calculate_keyword_score(message, self.casual_keywords)
        if casual_score > 0.3:
            logger.debug(f"检测到闲聊寒暄: score={casual_score:.2f}")
            return UserIntent.CASUAL_CHAT, casual_score

        # 6. 短消息 + 问号 → 可能是询问当前状态
        # 🔧 修复：运算符优先级问题，添加括号确保逻辑正确
        if len(message) < 10 and ("?" in message or "？" in message):
            logger.debug("检测到短消息问句，推测为询问当前状态")
            return UserIntent.QUERY_CURRENT, 0.6

        # 默认：闲聊
        # 🔧 优化：提升默认置信度从0.30到0.40，让闲聊也能通过InjectOptimizer的阈值检查
        logger.debug("未匹配到明确意图，归类为闲聊")
        return UserIntent.CASUAL_CHAT, 0.40

    def _calculate_keyword_score(self, message: str, keywords: set) -> float:
        """计算关键词匹配分数

        算法：
        1. 统计匹配的关键词数量
        2. 考虑关键词长度权重（长关键词权重更高）
        3. 归一化到 0-1 范围

        Args:
            message: 用户消息文本（已转小写）
            keywords: 关键词集合

        Returns:
            匹配分数 (0-1)
        """
        if not keywords:
            return 0.0

        matched_count = 0
        total_weight = 0.0

        for keyword in keywords:
            if keyword in message:
                matched_count += 1
                # 长关键词权重更高（防止误匹配）
                weight = len(keyword) / 5.0  # 归一化权重
                total_weight += weight

        if matched_count == 0:
            return 0.0

        # 归一化：考虑匹配数量和权重
        # 至少匹配1个得分0.4，匹配3个以上得分接近1.0
        base_score = min(1.0, matched_count / 3.0)
        weight_bonus = min(0.5, total_weight / 5.0)

        final_score = min(1.0, base_score + weight_bonus)

        return final_score

    def get_intent_description(self, intent: UserIntent) -> str:
        """获取意图的中文描述

        Args:
            intent: 意图类型

        Returns:
            中文描述字符串
        """
        descriptions = {
            UserIntent.QUERY_CURRENT: "询问当前状态",
            UserIntent.QUERY_FUTURE: "询问未来计划",
            UserIntent.CASUAL_CHAT: "闲聊寒暄",
            UserIntent.TECH_QUESTION: "技术问答",
            UserIntent.COMMAND_EXECUTION: "命令执行",
            UserIntent.UNKNOWN: "未知意图",
        }
        return descriptions.get(intent, "未知")

    def extract_time_range(self, message: str) -> Optional[TimeRange]:
        """🆕 从用户消息中提取时间段

        识别用户询问的时间段（如"下午"、"晚上"），用于过滤活动列表。

        Args:
            message: 用户消息文本

        Returns:
            TimeRange对象，如果未识别到时间段则返回None

        Examples:
            >>> classifier.extract_time_range("下午有什么安排")
            TimeRange(name='下午', start_hour=14, end_hour=18)

            >>> classifier.extract_time_range("晚上呢")
            TimeRange(name='晚上', start_hour=18, end_hour=23)
        """
        if not message:
            return None

        message_lower = message.lower().strip()

        # 按优先级匹配时间段（从长到短，避免误匹配）
        for time_word, time_range in self.time_ranges.items():
            if time_word in message_lower:
                logger.debug(f"识别到时间段: {time_word} ({time_range.start_hour}-{time_range.end_hour}时)")
                return time_range

        return None
