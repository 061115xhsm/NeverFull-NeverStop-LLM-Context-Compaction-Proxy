# LLM Context Compaction Proxy — Docker 镜像
FROM python:3.11-slim

LABEL org.opencontainers.image.title="LLM Context Compaction Proxy"
LABEL org.opencontainers.image.description="永不超限,永不停歇 · Never Full, Never Stop"
LABEL org.opencontainers.image.licenses="MIT"

WORKDIR /app

# 安装依赖
COPY requirements.txt /app/requirements.txt 2>/dev/null || true
RUN pip install --no-cache-dir aiohttp \
    && if [ -f /app/requirements.txt ]; then pip install --no-cache-dir -r /app/requirements.txt; fi

# 复制代码
COPY compaction-proxy.py /app/compaction-proxy.py
COPY fidelity.py /app/fidelity.py
COPY memory_engine.py /app/memory_engine.py
COPY knowledge_graph.py /app/knowledge_graph.py
COPY compression_advanced.py /app/compression_advanced.py
COPY security_enhanced.py /app/security_enhanced.py
COPY protocol_extra.py /app/protocol_extra.py
COPY cache_engine.py /app/cache_engine.py
COPY observability.py /app/observability.py
COPY .env.example /app/.env.example

# 运行用户
RUN useradd -m proxy && chown -R proxy:proxy /app
USER proxy

EXPOSE 8198

ENV COMPACTION_PROXY_PORT=8198

CMD ["python3", "compaction-proxy.py"]
