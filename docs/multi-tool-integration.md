# 压缩代理多工具接入记录

> 日期:2026-08-22
> 部署版:`~/.local/bin/openclaw-compaction-proxy.py`(V10)
> 前置:部署版已从 V6 升级至 V10(见 `deployment-upgrade-v10.md`)
> ⚠️ 2026-08-22:讯飞上游故障,全部压缩链路已切换至商汤 `sensenova-6.8-flash-lite`

## 架构总览(当前:全部商汤)

```
工具           → 压缩实例(端口)   → 上游后端(模型不变)
─────────────────────────────────────────────────────
openclaw      → 8198              → token.sensenova.cn(商汤)
claude code   → 8197(vision) → 8199 → token.sensenova.cn(商汤)
hermes        → 8198              → token.sensenova.cn(商汤)
atomcode      → 8200              → token.sensenova.cn/v1(商汤)
```

**当前所有压缩实例:上游 = 商汤,压缩模型 = `sensenova-6.8-flash-lite`**
(讯飞 maas-coding-api 故障后统一切换,原上游见文末"历史上游"表)

## 各工具接入方式

### 1. openclaw(原有,已确认)

- 配置:`~/.openclaw/openclaw.json` 794 行 `compaction.enabled=true`、provider=`v6-proxy`、proxyUrl=`127.0.0.1:8198`
- 服务:`systemctl --user openclaw-compaction-proxy.service`

### 2. claude code(新增)

链路:`claude code → 8197(vision 代理)→ 8199(压缩)→ 商汤`

步骤:
1. 新建压缩实例 `compaction-proxy-claude.service`(端口 8199,上游 `https://token.sensenova.cn`)
2. 修改 `claude-vision-proxy.service` 的 `VISION_PROXY_UPSTREAM=http://127.0.0.1:8199`
3. 保留 vision 代理的 sensenova 模型路由(VISION_PROXY_VISION_MODELS 等不变)

### 3. hermes(新增)

链路:`hermes(xunfei-anthropic provider)→ 8198(压缩)→ 讯飞`

步骤:
1. 备份 `~/.hermes/auth.json` → `auth.json.bak-pre-compaction`
2. 修改 `providers.xunfei-anthropic.base_url = http://127.0.0.1:8198`
3. 重启 `hermes-gateway.service`

### 4. atomcode(新增)

链路:`atomcode(商汤账号)→ 8200(压缩)→ 商汤`

步骤:
1. 新建压缩实例 `compaction-proxy-atomcode.service`(端口 8200,上游 `https://token.sensenova.cn/v1`)
2. 修改 `~/.atomcode/config.toml` 中 `[provider_accounts."商汤"].base_url = http://127.0.0.1:8200`

## 验证结果

| 工具 | 压缩实例 | 版本 | 上游 | 服务状态 |
|------|---------|------|------|---------|
| openclaw | 8198 | V10 | maas-coding-api(讯飞) | active |
| claude code | 8199 | V10 | token.sensenova.cn(商汤) | active |
| hermes | 8198 | V10 | maas-coding-api(讯飞) | active |
| atomcode | 8200 | V10 | token.sensenova.cn/v1(商汤) | active |

`/health` 端点均返回 `{"status": "ok", "version": "V10"}`。

## 关键设计决策

1. **各工具独立压缩实例**:因各工具上游后端不同(讯飞 vs 商汤),为每个工具创建独立压缩实例(8198/8199/8200),避免串链导致模型后端错乱;
2. **模型后端保持不变**:压缩代理仅做压缩,上游仍指向工具原后端,模型名/API key 透传,不影响现有使用;
3. **claude code 特殊处理**:vision 代理与压缩实例分置(8197 → 8199),保留 vision 路由能力。

## 回滚方式

```bash
# claude code:恢复 vision 代理上游
sed -i 's|VISION_PROXY_UPSTREAM=http://127.0.0.1:8199|VISION_PROXY_UPSTREAM=https://token.sensenova.cn|' ~/.config/systemd/user/claude-vision-proxy.service
systemctl --user daemon-reload && systemctl --user restart claude-vision-proxy.service

# hermes:恢复 auth.json
cp ~/.hermes/auth.json.bak-pre-compaction ~/.hermes/auth.json
systemctl --user restart hermes-gateway.service

# atomcode:恢复 config.toml base_url
# 将 [provider_accounts."商汤"].base_url 改回 https://token.sensenova.cn/v1
```

## 历史上游(讯飞故障前)

| 实例 | 端口 | 原上游(讯飞) | 现上游(商汤) |
|------|------|-------------|-------------|
| openclaw | 8198 | maas-coding-api.cn-huabei-1.xf-yun.com/anthropic | token.sensenova.cn |
| hermes(同 8198) | 8198 | 同上 | 同上 |
| claude code | 8199 | —(创建即商汤) | token.sensenova.cn |
| atomcode | 8200 | —(创建即商汤) | token.sensenova.cn/v1 |

**2026-08-22 讯飞切换变更**:
- `openclaw-compaction-proxy.service`:新增 `COMPACTION_PROXY_UPSTREAM=https://token.sensenova.cn`、`COMPACTION_PROXY_UPSTREAM_IS_ANTHROPIC=1`、`COMPACTION_PROXY_MODEL=sensenova-6.8-flash-lite`(注意 Environment 必须放在 `[Service]` 段内,放在 `[Install]` 之后会被忽略)
- `compaction-proxy-claude.service` / `compaction-proxy-atomcode.service`:补充 `COMPACTION_PROXY_MODEL=sensenova-6.8-flash-lite`
- `openclaw.json` 的 `v6-compaction-provider`:model `xsparkx2agent` → `sensenova-6.8-flash-lite`,apiKey 切换为商汤 key
