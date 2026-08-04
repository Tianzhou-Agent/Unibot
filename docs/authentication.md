# 用户认证

Unibot 支持邮箱密码账户和 GitHub OAuth App 登录。业务接口默认要求登录；`/health`、API 文档和
`/auth/*` 保持公开。登录成功后，后端通过 `HttpOnly`、`SameSite=Lax` Cookie 保存有时效的签名会话，
前端不接触或持久化令牌。

## 本地账户

用户可直接在登录页切换到“注册”，填写昵称、邮箱和至少 8 位密码。密码使用 Argon2 单向哈希保存，
接口响应中不会返回哈希。可通过以下设置关闭开放注册：

```dotenv
UNIBOT_AUTH_REGISTRATION_ENABLED=false
```

## GitHub OAuth App

1. 在 GitHub 的 **Settings → Developer settings → OAuth Apps** 创建 OAuth App。
2. 本地开发的 Homepage URL 填写 `http://127.0.0.1:5173`。
3. Authorization callback URL 填写
   `http://127.0.0.1:5173/api/auth/github/callback`，必须与下面的配置完全一致。
4. 将 Client ID 和生成的 Client secret 写入后端环境变量：

```dotenv
UNIBOT_GITHUB_CLIENT_ID=your-client-id
UNIBOT_GITHUB_CLIENT_SECRET=your-client-secret
UNIBOT_GITHUB_CALLBACK_URL=http://127.0.0.1:5173/api/auth/github/callback
UNIBOT_FRONTEND_BASE_URL=http://127.0.0.1:5173
```

两个凭据都存在时，前端才显示“使用 GitHub 登录”。授权过程使用授权码流程、随机 `state` 和
S256 PKCE；仅申请读取验证邮箱所需的 `user:email` scope。每次登录都会重新读取 GitHub 用户资料，
以 GitHub 不变的数字用户 ID 识别账户，访问令牌用完即丢弃，不写入数据库。若验证邮箱已被本地账户使用，
系统会拒绝自动合并，避免未验证的本地邮箱被用于抢占 GitHub 身份。

参考 GitHub 官方文档：[授权 OAuth App](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)、
[用户邮箱 API](https://docs.github.com/en/rest/users/emails)。

## 生产和多节点部署

```dotenv
UNIBOT_AUTH_SECRET=replace-with-a-long-random-production-secret
UNIBOT_AUTH_SESSION_HOURS=168
UNIBOT_AUTH_COOKIE_SECURE=true
UNIBOT_FRONTEND_BASE_URL=https://unibot.example.com
UNIBOT_GITHUB_CALLBACK_URL=https://unibot.example.com/api/auth/github/callback
```

- 所有 Unibot 后端节点必须共享相同的 `UNIBOT_AUTH_SECRET`，这样任意节点都能验证同一个会话。
- 用户记录存入现有 MySQL 权威存储；Redis 锁串行化跨节点账户创建和 GitHub 绑定，防止重复账户。
- 前端和 `/api` 应通过同一个站点提供服务。若使用反向代理，保留 HTTPS 和 Cookie 响应头。
- `UNIBOT_AUTH_SECRET`、GitHub Client secret 不得提交到 Git；轮换认证密钥会使已有会话全部失效。
