# Unibot 0.3.0 AMD64 离线部署

适用 Linux AMD64（目标 Ubuntu 24.04 x86_64）。包含八个镜像：Unibot 后端、前端、统一网关、drawio、PPTist 编辑器、PPTist MCP、MySQL 8.0、Redis 7。不含 Vision / YOLO 和模型权重。Docker Engine、Compose v2、Bash、unzip 和 sha256sum 须预先安装。

## 安装发布包

```bash
sha256sum -c unibot-0.3.0-amd64-superbot-offline.zip.sha256
mkdir -p /opt/unibot
unzip unibot-0.3.0-amd64-superbot-offline.zip -d /opt/unibot
cd /opt/unibot
bash init-config.sh
# 编辑 .env，替换全部 SERVER_IP，并配置可从容器访问的模型地址及密钥。
bash install.sh
```

`init-config.sh` 生成随机密码和认证密钥，拒绝覆盖已有 `.env`。`install.sh` 校验文件、加载镜像、生成应用包并启动八个服务；安装无需拉取镜像或编译源码。应用的对外地址通过 `UNIBOT_PUBLIC_ORIGIN` 指定（例如 `http://192.168.1.10:8080`，不带路径或末尾斜线），须与允许来源、前端地址和 OAuth 回调保持一致。

| 应用 | 上下文根 | 工具地址 |
|---|---|---|
| Superbot | `/superbot/` | `/superbot/api/` |
| drawio 32.5.5 / MCP 2.3.0 | `/drawio/` | `/drawio/mcp`，WebSocket `/drawio/ws` |
| PPTist | `/pptist/` | `/pptist/mcp` |

仅网关发布 HTTP 端口（默认 8080）；后端调试端口仅绑定回环，应用内部端口不对外发布。不要给 drawio 添加 `offline=1`，该选项会禁用 MCP 插件；`/drawio` 默认跳转并启用 `local=1&lang=zh&stealth=1`。drawio MCP 沿用现有部署，无登录认证，部署于可信网络或有认证的网关后。

## 添加应用

安装后在 Superbot 的插件页上传 `apps/pptist.aina.zip` 并安装 **PPTist 幻灯片**。包内已配置当前站点的 `/pptist/` 和 `/pptist/mcp`。PPTist MCP 在容器网络内访问 `http://backend:8000/aina-host/v1`，签发者使用公开的 `/superbot/api/aina-host/v1`；与后端一致，支持保存到用户私有空间。每个对话保存独立幻灯片，导出 PPTX 保存到用户文件。

drawio 作为 MCP 工具服务添加，URL 为 `站点地址/drawio/mcp`（配置示例 `apps/drawio-mcp.json`）；同时在浏览器保持 `站点地址/drawio` 页面打开。MCP 通过该页面的 WebSocket 操作图表。它不是 PPTist 类型的 AINA 包。

远程 AINA URL 画布要求 HTTPS（回环地址可使用 HTTP）。服务器部署 PPTist 时请配置域名及 HTTPS。PPTist 自带 AI 按钮使用外部服务；Superbot 的幻灯片工具不依赖这些按钮。

## HTTPS 与升级

将 `nginx-contexts.conf` 加入现有 HTTPS server 块，保留三个路径前缀并传递 WebSocket Upgrade。设置 `.env` 中的 `UNIBOT_PUBLIC_ORIGIN=https://example.com`、`UNIBOT_FRONTEND_BASE_URL=https://example.com/superbot`、两个允许来源为 `https://example.com`、OAuth 回调及 `UNIBOT_AUTH_COOKIE_SECURE=true`。网关在同机时可设置 `UNIBOT_HTTP_BIND=127.0.0.1`。重新执行安装脚本会按新地址生成应用包；已添加的应用需要重新配置相应 URL。

从旧版升级前备份数据库、`.env`、NAS 和沙箱工作区卷。后端结构指纹不匹配时会删除并重建业务表，当前没有保留历史数据的自动迁移。保留 Compose 项目名 `unibot` 与原业务卷，补齐新 `.env` 配置后运行安装脚本。回退须同时恢复对应数据库及卷备份。不要执行 `docker compose down -v`。

`source.zip` 是对应 Unibot 源码；`third-party/` 包含 PPTist 对应源码及许可证、drawio 前端发行包及许可证、MCP 对应 npm 包及许可证。`manifest.json` 记录镜像摘要、来源和实际验证范围，`SHA256SUMS` 校验包内文件。配置修改后用 `docker compose -f compose.yml up -d --pull never --no-build` 重建容器；查看状态用 `docker compose -f compose.yml ps`。
