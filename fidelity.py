"""
语义保真度量化与质量熔断模块(fidelity.py)
============================================
供压缩代理调用,实现:
1. FidelityScorer —— 压缩前后语义相似度评分(0-1)
2. AdaptiveCompactor —— 保真度底线约束的自适应压缩
3. QualityBreaker —— 连续低保真度质量熔断
4. query_relevance_weighting —— 查询相关性加权

依赖策略:优先使用 sentence-transformers(bge-small 系列)做嵌入余弦相似度;
未安装时自动降级为 n-gram Jaccard + token 覆盖率,保证纯标准库可运行。
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

# ── 可选依赖:语义嵌入模型 ──────────────────────────────────────────
try:
    from sentence_transformers import SentenceTransformer  # type: ignore

    _MODEL: Optional[SentenceTransformer] = None

    def _get_model(model_name: str = "BAAI/bge-small-zh-v1.5") -> Optional[SentenceTransformer]:
        """懒加载嵌入模型;加载失败返回 None(走降级路径)。"""
        global _MODEL
        if _MODEL is None:
            try:
                _MODEL = SentenceTransformer(model_name)
            except Exception:
                _MODEL = None
        return _MODEL

    _HAS_SENTENCE_TRANSFORMERS = True
except Exception:
    _HAS_SENTENCE_TRANSFORMERS = False

    def _get_model(model_name: str = "BAAI/bge-small-zh-v1.5") -> Optional["SentenceTransformer"]:  # type: ignore
        return None


# ── 工具函数 ────────────────────────────────────────────────────────

def _tokenize(text: str) -> List[str]:
    """中英文混合分词:中文按字,英文按词。"""
    text = (text or "").lower()
    # 中文单字 + 英文/数字词
    tokens = re.findall(r"[\u4e00-\u9fff]|[a-z0-9_]+", text)
    return tokens


def _ngrams(tokens: List[str], n: int = 2) -> set:
    """生成 n-gram 集合。"""
    if len(tokens) < n:
        return set(tokens) if tokens else set()
    return set(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


# ── 语义保真度评分器 ────────────────────────────────────────────────

class FidelityScorer:
    """
    压缩前后语义相似度评分(0-1)。

    - 有 sentence-transformers 时:嵌入余弦相似度,最高保真。
    - 无依赖时:2-gram Jaccard + token 覆盖率加权,保证可用。
    """

    def __init__(self, model_name: str = "BAAI/bge-small-zh-v1.5") -> None:
        self.model_name = model_name
        self.embedder = _get_model(model_name) if _HAS_SENTENCE_TRANSFORMERS else None

    @property
    def using_embedding(self) -> bool:
        return self.embedder is not None

    def _embed_similarity(self, a: str, b: str) -> Optional[float]:
        if self.embedder is None:
            return None
        try:
            vecs = self.embedder.encode([a, b], normalize_embeddings=True)
            # 余弦相似度(已归一化,内积即余弦)
            return float(vecs[0] @ vecs[1])
        except Exception:
            return None

    def _lexical_similarity(self, a: str, b: str) -> float:
        """词法降级:2-gram Jaccard + 覆盖率。"""
        ta, tb = _tokenize(a), _tokenize(b)
        if not ta or not tb:
            return 1.0 if a.strip() == b.strip() else 0.0
        ngram_a, ngram_b = _ngrams(ta, 2), _ngrams(tb, 2)
        jac = _jaccard(ngram_a, ngram_b)
        # token 覆盖率:两边都要覆盖够,取较小者
        set_a, set_b = set(ta), set(tb)
        if not set_a or not set_b:
            return jac
        cov = min(len(set_a & set_b) / len(set_a), len(set_a & set_b) / len(set_b))
        return 0.6 * jac + 0.4 * cov

    def score(self, original_text: str, compacted_text: str) -> float:
        """计算压缩前后语义相似度,返回 0-1。"""
        if self.embedder is not None:
            sim = self._embed_similarity(original_text, compacted_text)
            if sim is not None:
                # 余弦可能为负,夹到 [0,1]
                return max(0.0, min(1.0, (sim + 1.0) / 2.0 if sim < 0 else sim))
        return self._lexical_similarity(original_text, compacted_text)


# ── 保真度约束的自适应压缩 ──────────────────────────────────────────

class AdaptiveCompactor:
    """
    以保真度底线为约束的自适应压缩。

    流程:以初始强度压缩 → 评分 → 低于 min_fidelity 则降低压缩强度
    (提高保留内容比例)重试,最多 max_attempts 次。
    """

    def __init__(
        self,
        scorer: Optional[FidelityScorer] = None,
        min_fidelity: float = 0.92,
        max_attempts: int = 3,
        min_content_len: int = 80,
    ) -> None:
        self.scorer = scorer or FidelityScorer()
        self.min_fidelity = min_fidelity
        self.max_attempts = max_attempts
        self.min_content_len = min_content_len

    def _compress_with_strength(self, messages: List[dict], budget: int, strength: float) -> List[dict]:
        """
        按强度压缩:strength 0-1,越高保留越多。
        简化实现:对每条消息按强度截断长文本。
        """
        result: List[dict] = []
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > self.min_content_len:
                # 保留比例 = strength;预算紧张时进一步收缩
                keep_len = max(20, int(len(content) * strength))
                if budget is not None and len(result) > 0:
                    keep_len = min(keep_len, max(20, budget // max(1, len(messages))))
                if keep_len < len(content):
                    new_msg = dict(msg)
                    new_msg["content"] = content[:keep_len] + ("..." if len(content) > keep_len else "")
                    result.append(new_msg)
                else:
                    result.append(msg)
            else:
                result.append(msg)
        return result

    def compact(
        self,
        messages: List[dict],
        budget: int,
        min_fidelity: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        执行自适应压缩。

        Returns:
            {'messages': [...], 'fidelity': float, 'attempts': int, 'met_floor': bool}
        """
        floor = min_fidelity if min_fidelity is not None else self.min_fidelity
        original_text = "\n".join(str(m.get("content", "")) for m in messages)

        # 从宽松到严格:strength 递减,保真度不足时降低压缩强度
        for attempt in range(1, self.max_attempts + 1):
            strength = max(0.3, 0.9 - (attempt - 1) * 0.2)
            compacted = self._compress_with_strength(messages, budget, strength)
            compacted_text = "\n".join(str(m.get("content", "")) for m in compacted)
            fidelity = self.scorer.score(original_text, compacted_text)

            if fidelity >= floor or attempt == self.max_attempts:
                return {
                    "messages": compacted,
                    "fidelity": fidelity,
                    "attempts": attempt,
                    "met_floor": fidelity >= floor,
                }

        # 理论不可达;兜底
        return {
            "messages": messages,
            "fidelity": 1.0,
            "attempts": self.max_attempts,
            "met_floor": True,
        }


# ── 质量熔断器 ──────────────────────────────────────────────────────

class QualityBreaker:
    """
    质量熔断:连续 N 次保真度低于阈值时进入 open 状态,
    暂停压缩调用,避免反复产生低质量压缩。
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 60.0) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown_seconds
        self.failure_count = 0
        self.state = "closed"  # closed | open
        self.last_failure_time = 0.0

    def record(self, fidelity: float, min_fidelity: float) -> None:
        if fidelity < min_fidelity:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = "open"
        else:
            self.failure_count = 0
            self.state = "closed"

    def can_attempt(self) -> bool:
        if self.state == "closed":
            return True
        # open 状态冷却期后恢复 half-open(由调用方试探)
        if time.time() - self.last_failure_time > self.cooldown:
            self.state = "closed"
            self.failure_count = 0
            return True
        return False

    def reset(self) -> None:
        self.failure_count = 0
        self.state = "closed"
        self.last_failure_time = 0.0


# ── 查询相关性加权 ──────────────────────────────────────────────────

def query_relevance_weighting(messages: List[dict], query: str) -> List[float]:
    """
    基于查询为每条消息打相关性权重(0-1)。

    权重 = 0.7 * 关键词重叠率 + 0.3 * 近因权重(越近越重要)。
    """
    if not messages:
        return []
    q_tokens = set(_tokenize(query))
    n = len(messages)
    weights: List[float] = []
    for i, msg in enumerate(messages):
        content = str(msg.get("content", ""))
        m_tokens = set(_tokenize(content))
        if q_tokens and m_tokens:
            overlap = len(q_tokens & m_tokens) / len(q_tokens)
        else:
            overlap = 0.0
        recency = (i + 1) / n  # 越靠后越新,权重越高
        weights.append(0.7 * min(1.0, overlap) + 0.3 * recency)
    return weights
