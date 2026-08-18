"""
MemSkill 自进化强化学习脚手架(memskill_rl.py)
==============================================
MemSkill 深化为自进化压缩系统(路线图 #19):

- SkillPolicy:压缩策略(提示词/记忆策略/保留规则)的可变参数
- RewardSignal:以「任务成功率 + token 净节省率」为奖励信号
- RLSkillOptimizer:基于简单强化(梯度上升近似/多臂老虎机)自动优化策略
  - 探索:随机扰动策略参数
  - 利用:朝奖励更高的方向微调
- AutoSkillDesigner:自动发现、生成、验证新压缩 Skill(脚手架接口)

纯标准库实现(随机/数学),作为后续接入真实 RL 框架的前置脚手架。
"""

from __future__ import annotations

import copy
import json
import math
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ── 策略参数 ────────────────────────────────────────────────────────

@dataclass
class SkillPolicy:
    """
    一个压缩 Skill 的可变策略参数。

    - keep_turns: 保留轮次
    - min_fidelity: 保真度底线
    - arc_threshold: ARC 引用阈值(字符)
    - summary_temperature: 摘要温度
    - description: 策略描述(便于审计)
    """
    keep_turns: float = 6.0
    min_fidelity: float = 0.90
    arc_threshold: float = 800.0
    summary_temperature: float = 0.3
    description: str = "default policy"

    def perturbed(self, scale: float = 0.2) -> "SkillPolicy":
        """返回一个在 scale 范围内随机扰动的策略副本(探索)。"""
        p = copy.deepcopy(self)
        p.keep_turns = max(2.0, self.keep_turns * (1 + random.uniform(-scale, scale)))
        p.min_fidelity = max(0.5, min(1.0, self.min_fidelity * (1 + random.uniform(-scale, scale))))
        p.arc_threshold = max(200.0, self.arc_threshold * (1 + random.uniform(-scale, scale)))
        p.summary_temperature = max(0.0, min(1.0, self.summary_temperature * (1 + random.uniform(-scale, scale))))
        return p

    def to_dict(self) -> Dict[str, Any]:
        return {
            "keep_turns": self.keep_turns,
            "min_fidelity": self.min_fidelity,
            "arc_threshold": self.arc_threshold,
            "summary_temperature": self.summary_temperature,
            "description": self.description,
        }


# ── 奖励信号 ────────────────────────────────────────────────────────

class RewardSignal:
    """
    奖励信号:任务成功率 + token 净节省率。

    reward = w_success * task_success + w_save * token_save
    - task_success: 0-1(压缩后任务是否成功/关键信息是否保留)
    - token_save: 0-1(压缩节省的 token 比例)
    """

    def __init__(self, w_success: float = 0.7, w_save: float = 0.3) -> None:
        self.w_success = w_success
        self.w_save = w_save

    def compute(self, task_success: float, token_saved_ratio: float) -> float:
        return self.w_success * max(0.0, min(1.0, task_success)) \
            + self.w_save * max(0.0, min(1.0, token_saved_ratio))

    @staticmethod
    def token_saved_ratio(tokens_before: int, tokens_after: int) -> float:
        if tokens_before <= 0:
            return 0.0
        return max(0.0, min(1.0, (tokens_before - tokens_after) / tokens_before))


# ── 自进化优化器 ────────────────────────────────────────────────────

class RLSkillOptimizer:
    """
    基于简单强化(策略扰动 + 奖励反馈)的压缩策略优化器。

    方法:
    - propose(): 返回一个待评估的策略(初始为当前最优 + 探索扰动)
    - update(policy, reward): 依据奖励更新最优策略与学习率
    - best(): 当前最优策略
    """

    def __init__(
        self,
        initial_policy: Optional[SkillPolicy] = None,
        reward_signal: Optional[RewardSignal] = None,
        lr: float = 0.1,
        decay: float = 0.99,
    ) -> None:
        self.best_policy: SkillPolicy = initial_policy or SkillPolicy()
        self.best_reward: float = -math.inf
        self.reward_signal = reward_signal or RewardSignal()
        self.lr = lr
        self.decay = decay
        self._trials: List[Dict[str, Any]] = []
        self._exploration = 0.3  # 探索率(随学习衰减)

    def propose(self) -> SkillPolicy:
        """提议下一个待评估策略:扰动探索 + 利用最优。"""
        if random.random() < self._exploration:
            return self.best_policy.perturbed(scale=self._exploration)
        return copy.deepcopy(self.best_policy)

    def update(self, policy: SkillPolicy, task_success: float,
               tokens_before: int, tokens_after: int) -> float:
        """
        依据一次试验的奖励更新最优策略。

        Returns: 本次奖励
        """
        reward = self.reward_signal.compute(
            task_success,
            self.reward_signal.token_saved_ratio(tokens_before, tokens_after),
        )
        self._trials.append({
            "ts": time.time(),
            "reward": reward,
            "task_success": task_success,
            "tokens_before": tokens_before,
            "tokens_after": tokens_after,
            "policy": policy.to_dict(),
        })
        if reward > self.best_reward:
            self.best_reward = reward
            self.best_policy = copy.deepcopy(policy)
            # 奖励上升 → 缩小探索
            self._exploration = max(0.05, self._exploration * self.decay)
        else:
            # 奖励未升 → 保持探索
            self._exploration = min(0.5, self._exploration / self.decay)
        return reward

    @property
    def best(self) -> SkillPolicy:
        return self.best_policy

    def stats(self) -> Dict[str, Any]:
        return {
            "trials": len(self._trials),
            "best_reward": round(self.best_reward, 4),
            "exploration": round(self._exploration, 4),
            "best_policy": self.best_policy.to_dict(),
        }

    def save(self, path: str) -> None:
        data = {
            "best_reward": self.best_reward,
            "exploration": self._exploration,
            "best_policy": self.best_policy.to_dict(),
            "trials": self._trials[-200:],  # 只保留最近 200 条
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.best_reward = data.get("best_reward", -math.inf)
            self._exploration = data.get("exploration", 0.3)
            bp = data.get("best_policy", {})
            self.best_policy = SkillPolicy(
                keep_turns=bp.get("keep_turns", 6.0),
                min_fidelity=bp.get("min_fidelity", 0.90),
                arc_threshold=bp.get("arc_threshold", 800.0),
                summary_temperature=bp.get("summary_temperature", 0.3),
                description=bp.get("description", "default policy"),
            )
            self._trials = data.get("trials", [])
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            pass


# ── 自动技能设计器 ──────────────────────────────────────────────────

class AutoSkillDesigner:
    """
    自动发现、生成、验证新压缩 Skill 的脚手架。

    当前为接口脚手架:验证回调由调用方提供(如接入真实 LLM/评测),
    目标场景为特定任务(代码开发/数据分析/客服)自动进化最优策略。
    """

    def __init__(
        self,
        evaluator: Optional[Callable[[SkillPolicy], float]] = None,
        optimizer: Optional[RLSkillOptimizer] = None,
    ) -> None:
        self.evaluator = evaluator  # fn(policy) -> 0-1 任务成功率
        self.optimizer = optimizer or RLSkillOptimizer()
        self._generated: List[Dict[str, Any]] = []

    def design_round(self, tokens_before: int, tokens_after: int,
                     task_context: str = "") -> Optional[SkillPolicy]:
        """
        一轮设计:提议 → 评估 → 更新。

        若未提供 evaluator,直接返回提议策略(由外部调用方评估后调
        optimizer.update() 反馈)。
        """
        policy = self.optimizer.propose()
        self._generated.append({"ts": time.time(), "policy": policy.to_dict(),
                                "context": task_context})
        if self.evaluator is not None:
            success = self.evaluator(policy)
            self.optimizer.update(policy, success, tokens_before, tokens_after)
        return policy

    @property
    def generated_count(self) -> int:
        return len(self._generated)
