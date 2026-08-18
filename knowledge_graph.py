"""
知识图谱记忆模块(knowledge_graph.py)
======================================
轻量知识图谱混合存储,供压缩代理调用:

- KnowledgeGraph:纯 Python 图,支持实体/关系/属性,中英文三元组抽取
- HybridRetriever:关键词匹配 + 图关联推理 + 文本回退的组合召回

持久化为 JSON,原子写入。纯标准库实现。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Set, Tuple


# ── 知识图谱 ────────────────────────────────────────────────────────

class KnowledgeGraph:
    """轻量有向图:实体为节点,关系为边。"""

    def __init__(self) -> None:
        self.entities: Dict[str, Dict[str, Any]] = {}   # id -> {type, properties}
        self.relations: List[Dict[str, Any]] = []        # [{subject, relation, object, weight}]
        self._adj: Dict[str, List[Tuple[str, str, float]]] = defaultdict(list)  # id -> [(rel, target, w)]

    # ── 写入 ──
    def add_entity(self, entity_id: str, entity_type: str = "entity",
                   properties: Optional[Dict[str, Any]] = None) -> None:
        if entity_id not in self.entities:
            self.entities[entity_id] = {"type": entity_type, "properties": properties or {}}

    def add_relation(self, subject_id: str, relation: str, object_id: str, weight: float = 1.0) -> None:
        self.relations.append({
            "subject": subject_id, "relation": relation, "object": object_id, "weight": weight,
        })
        self._adj[subject_id].append((relation, object_id, weight))
        # 反向也记录(便于双向推理)
        self._adj[object_id].append((f"<-{relation}", subject_id, weight))

    # ── 三元组抽取(中英文) ──
    # 模式: X 使用/依赖/基于/采用 Y ; X uses/depends on/relies on Y ; X 的 A 是 B
    _PATTERNS: List[Tuple[str, str, str]] = [
        (r"([\u4e00-\u9fffA-Za-z0-9_\-\.]{2,40})\s*(?:使用|依赖|基于|采用|利用|调用|连接)\s*([\u4e00-\u9fffA-Za-z0-9_\-\.]{2,40})", "uses", "depends_on"),
        (r"([\u4e00-\u9fffA-Za-z0-9_\-\.]{2,40})\s+(?:uses|depends on|relies on|based on|built on|connects to)\s+([\u4e00-\u9fffA-Za-z0-9_\-\.]{2,40})", "uses", "depends_on"),
        (r"([\u4e00-\u9fffA-Za-z0-9_\-\.]{2,40})\s+的\s+([\u4e00-\u9fffA-Za-z0-9_\-\.]{2,20})\s+是\s+([\u4e00-\u9fffA-Za-z0-9_\-\.]{2,40})", "has_attr", "is"),
    ]

    def extract_from_messages(self, messages: List[dict]) -> int:
        """从消息中抽取三元组并入库,返回新增关系数。"""
        added = 0
        for msg in messages:
            content = str(msg.get("content", ""))
            for pat, rel_use, rel_dep in self._PATTERNS:
                for m in re.finditer(pat, content):
                    groups = m.groups()
                    if len(groups) == 2:
                        subj, obj = groups
                        rel = rel_use
                    elif len(groups) == 3:
                        subj, attr, obj = groups
                        # 三元组: subject --has_attr--> attr --is--> obj
                        self.add_entity(subj)
                        self.add_entity(attr)
                        self.add_relation(subj, "has_attr", attr)
                        self.add_relation(attr, "is", obj)
                        added += 2
                        continue
                    else:
                        continue
                    self.add_entity(subj)
                    self.add_entity(obj)
                    self.add_relation(subj, rel, obj)
                    added += 1
        return added

    # ── 查询 ──
    def query(self, entity_id: str) -> Dict[str, Any]:
        """返回实体及其直接邻居。"""
        neighbors = [
            {"relation": rel, "target": tgt, "weight": w}
            for rel, tgt, w in self._adj.get(entity_id, [])
        ]
        return {"entity": entity_id, "meta": self.entities.get(entity_id, {}), "neighbors": neighbors}

    def query_related(self, entity_id: str, depth: int = 2) -> List[Dict[str, Any]]:
        """BFS 图关联推理:返回深度范围内的关联路径。"""
        results: List[Dict[str, Any]] = []
        visited: Set[str] = {entity_id}
        queue: deque = deque([(entity_id, 0, "")])
        while queue:
            node, d, path = queue.popleft()
            if d >= depth:
                continue
            for rel, tgt, w in self._adj.get(node, []):
                if tgt in visited:
                    continue
                visited.add(tgt)
                new_path = f"{path} --{rel}--> {tgt}" if path else f"{node} --{rel}--> {tgt}"
                results.append({"path": new_path, "target": tgt, "weight": w, "depth": d + 1})
                queue.append((tgt, d + 1, new_path))
        results.sort(key=lambda x: x["weight"], reverse=True)
        return results

    def find_by_keyword(self, keyword: str) -> List[str]:
        """关键词匹配实体 id。"""
        kw = keyword.lower()
        return [eid for eid in self.entities if kw in eid.lower()]

    # ── 持久化 ──
    def save(self, path: str) -> None:
        data = {"entities": self.entities, "relations": self.relations}
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def load(self, path: str) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            self.entities = data.get("entities", {})
            self.relations = data.get("relations", [])
            self._adj = defaultdict(list)
            for r in self.relations:
                self._adj[r["subject"]].append((r["relation"], r["object"], r["weight"]))
                self._adj[r["object"]].append((f"<-{r['relation']}", r["subject"], r["weight"]))
        except (FileNotFoundError, json.JSONDecodeError, TypeError):
            pass


# ── 混合召回 ────────────────────────────────────────────────────────

class HybridRetriever:
    """
    组合召回:关键词匹配 → 图关联推理 → 文本回退。
    """

    def __init__(self, graph: Optional[KnowledgeGraph] = None) -> None:
        self.graph = graph or KnowledgeGraph()

    @staticmethod
    def _tokenize(text: str) -> Set[str]:
        return set(re.findall(r"[\u4e00-\u9fff]|[a-z0-9_]+", (text or "").lower()))

    def retrieve(self, query: str, messages: List[dict],
                 graph: Optional[KnowledgeGraph] = None, k: int = 5) -> List[Dict[str, Any]]:
        """
        返回按相关度排序的结果列表,每项含 score 与来源(source: keyword/graph/text)。
        """
        g = graph or self.graph
        results: List[Dict[str, Any]] = []
        q_tokens = self._tokenize(query)

        # 1) 关键词匹配实体
        for eid in g.find_by_keyword(query):
            results.append({"score": 1.0, "source": "keyword", "entity": eid,
                            "meta": g.entities.get(eid, {})})

        # 2) 图关联推理:对命中的实体做 BFS 扩展
        hit_entities = [r["entity"] for r in results if r["source"] == "keyword"]
        for eid in hit_entities:
            for rel in g.query_related(eid, depth=2):
                rel_score = 0.8 * rel["weight"]
                results.append({"score": rel_score, "source": "graph", "entity": rel["target"],
                                "path": rel["path"]})

        # 3) 文本回退:消息内容关键词重叠
        for msg in messages:
            content = str(msg.get("content", ""))
            m_tokens = self._tokenize(content)
            if q_tokens and m_tokens:
                overlap = len(q_tokens & m_tokens) / len(q_tokens)
                if overlap > 0.3:
                    results.append({"score": overlap * 0.6, "source": "text", "content": content[:200]})

        # 去重 + 排序 + top-k
        seen: Set[str] = set()
        deduped: List[Dict[str, Any]] = []
        for r in results:
            key = r.get("entity") or r.get("content") or str(r)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)
        deduped.sort(key=lambda x: x["score"], reverse=True)
        return deduped[:k]
