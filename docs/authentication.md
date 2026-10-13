# 用户认证

Unibot 支持邮箱密码账户，以及 GitHub、华为 Uniportal、Google、Microsoft、Apple 登录。业务接口默认要求登录；`/health`、API 文档和
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

## 华为 Uniportal OAuth

按 [Nexent 的华为适配协议](https://github.com/ModelEngine-Group/nexent/blob/0fa09c1d767b0419d4c42a3e6a5f172f2723ec1b/backend/consts/oauth_providers.py)接入内部 Uniportal。
在 Uniportal 注册应用后，将回调地址设置为
`http://127.0.0.1:5173/api/auth/huawei/callback`，并配置：

```dotenv
UNIBOT_HUAWEI_BASE_URL=https://your-uniportal-host
UNIBOT_HUAWEI_CLIENT_ID=your-client-id
UNIBOT_HUAWEI_CLIENT_SECRET=your-client-secret
UNIBOT_HUAWEI_CALLBACK_URL=http://127.0.0.1:5173/api/auth/huawei/callback
UNIBOT_FRONTEND_BASE_URL=http://127.0.0.1:5173
```

地址与两项凭据都存在时，登录页显示“使用华为账号登录”。后端申请 `base.profile` scope，
通过 `/saaslogin1/oauth2/authorize` 授权，以 JSON POST 请求
`/saaslogin1/oauth2/accesstoken` 换取令牌，再 GET 请求 `/saaslogin1/oauth2/userinfo`。
用户信息请求携带 Bearer 令牌及 `access_token`、`client_id`、`scope` 查询参数，与 Nexent 一致。
使用 `globalUserID` 识别账户，读取 `email` 和 `givenName`；缺少有效 ID 或邮箱时拒绝登录，
昵称缺失时使用 ID。重复登录更新昵称并保留原账户 ID、邮箱与用户空间。

授权使用随机 `state` 和 10 分钟有效的签名 HttpOnly Cookie，回调成功后清除授权 Cookie，
签发与其他登录方式相同的会话。访问令牌不会持久化；同邮箱已有账户时拒绝自动合并。

本地开发将以上配置写入 `backend/.env`；Docker Compose 部署写入项目根目录 `.env`；
飞牛部署写入 `deploy/fnos/.env`。也可以直接注入进程环境变量，环境变量优先于后端 dotenv 文件。
这些 `.env` 文件均被 Git 忽略，提交的 `.env.example` 只包含空值和配置示例；密钥仅由后端读取。

后端及 Docker Compose 都接受 Nexent 的 `UNIPORTAL_URL`、`HUAWEI_OAUTH_CLIENT_ID`、
`HUAWEI_OAUTH_CLIENT_SECRET`。容器部署中，非空的 `UNIBOT_*` 变量优先，留空时回退到 Nexent 名称；
本地 dotenv 中的空值也按未设置处理，可直接在模板中追加 Nexent 别名。回调地址仍通过
`UNIBOT_HUAWEI_CALLBACK_URL` 配置，不能沿用 Nexent 的 `/api/user/oauth/callback` 路径。

内部证书可通过 `UNIBOT_HUAWEI_CA_BUNDLE=/path/to/internal-ca.pem` 配置。默认验证 TLS 证书；
`UNIBOT_HUAWEI_SSL_VERIFY=false` 可用于内部联调，CA bundle 配置优先于该开关。
容器部署时，证书路径必须位于后端容器内，并自行挂载相应文件。

生产回调地址需要改为实际站点的 `/api/auth/huawei/callback`；若前端部署在 `/superbot/`，
则回调为 `/superbot/api/auth/huawei/callback`，且 `UNIBOT_FRONTEND_BASE_URL` 也应包含 `/superbot`。
首次升级启动会自动为现有 `unibot_users` 表添加可空的 `huawei_id` 字段，数据库用户需要
`ALTER` 权限。全新数据库直接按新结构建表。

## Google、Microsoft、Apple 登录

三家使用授权码流程。后端通过官方 JWKS 验证 ID token 的 RS256 签名，并校验 issuer、audience、
有效期、nonce 和适用的 access-token hash。Google、Microsoft 同时使用 S256 PKCE。
以签发方和 `sub` 组合识别用户，Microsoft 的租户信息参与身份隔离；同邮箱已有账户时拒绝自动合并。
第三方令牌不持久化，登录后使用现有 Unibot 会话和用户空间。

### Google

在 Google Cloud 创建 **Web application** 类型的 OAuth 客户端，并配置授权回调地址。
后端只申请 `openid email profile`，使用有效且已验证的邮箱：

```dotenv
UNIBOT_GOOGLE_CLIENT_ID=your-google-client-id
UNIBOT_GOOGLE_CLIENT_SECRET=your-google-client-secret
UNIBOT_GOOGLE_CALLBACK_URL=http://127.0.0.1:5173/api/auth/google/callback
```

客户端 ID 和 secret 都配置后显示“使用 Google 登录”。部署时回调必须与 Google 后台注册的地址一致。
参考 [Google OpenID Connect 文档](https://developers.google.com/identity/openid-connect/openid-connect)。

### Microsoft

在 Microsoft Entra **App registrations** 注册应用，添加 **Web** 平台回调地址，
创建 client secret；应填写 secret 的 **Value**。注册应用的账号类型需要与所选租户范围一致。

```dotenv
UNIBOT_MICROSOFT_CLIENT_ID=your-application-client-id
UNIBOT_MICROSOFT_CLIENT_SECRET=your-client-secret-value
UNIBOT_MICROSOFT_CALLBACK_URL=http://127.0.0.1:5173/api/auth/microsoft/callback
UNIBOT_MICROSOFT_TENANT=common
```

`common` 支持组织和个人账号；`organizations` 仅组织账号；`consumers` 仅个人账号；
也可填写特定租户 UUID。后端验证 token 的租户与签发方、签名密钥的 issuer 相匹配。
只申请 `openid email profile`；Microsoft 不保证提供 `email`，没有有效邮箱的账号会被拒绝登录。
邮箱不用于授权或自动合并账户。

参考 [Microsoft 授权码流程](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-auth-code-flow)
与 [多租户令牌校验](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens)。

### Apple

在 Apple Developer 为网站配置 **Services ID**、关联启用 Sign in with Apple 的 App ID，
注册实际 HTTPS 域名和回调地址。配置：

```dotenv
UNIBOT_APPLE_CLIENT_ID=your-services-id
UNIBOT_APPLE_CLIENT_SECRET=your-signed-client-secret-jwt
UNIBOT_APPLE_CALLBACK_URL=https://unibot.example.com/api/auth/apple/callback
UNIBOT_FRONTEND_BASE_URL=https://unibot.example.com
UNIBOT_AUTH_COOKIE_SECURE=true
```

Apple client secret 是使用 Apple 私钥签名的 ES256 JWT，需要在过期前重新生成并更新配置。
它的 `iss` 为 Team ID，`sub` 为 Services ID，`aud` 为 `https://appleid.apple.com`，header 的
`kid` 为签名 Key ID。可用后端已有的 `python-jose` 生成，例如有效期 7 天：

```python
from pathlib import Path
from time import time
from jose import jwt

now = int(time())
secret = jwt.encode(
    {"iss": "YOUR_TEAM_ID", "sub": "YOUR_SERVICES_ID", "aud": "https://appleid.apple.com",
     "iat": now, "exp": now + 7 * 24 * 3600},
    Path("AuthKey_YOUR_KEY_ID.p8").read_text(),
    algorithm="ES256", headers={"kid": "YOUR_KEY_ID"},
)
print(secret)
```

后端申请 `name email`，使用 `form_post` 接收回调。临时授权 Cookie 设置
`SameSite=None; Secure`，因此需要 HTTPS 和 `UNIBOT_AUTH_COOKIE_SECURE=true`，满足配置后才显示入口。
首次授权返回的姓名会保存；后续没有姓名时保留原昵称。邮箱以验证后的 ID token 为准，
支持 Apple 的私密转发邮箱；缺少有效或已验证邮箱时拒绝登录。

参考 [Apple 网站接入说明](https://developer.apple.com/documentation/signinwithapple/incorporating-sign-in-with-apple-into-other-platforms)
与 [Apple 令牌验证](https://developer.apple.com/documentation/signinwithapplerestapi/generate-and-validate-tokens)。

所有提供方的回调地址都应包含站点实际路径前缀，例如 `/superbot/api/auth/google/callback`，
并将 `UNIBOT_FRONTEND_BASE_URL` 设置为 `https://unibot.example.com/superbot`。
Docker 和飞牛部署的环境变量示例已包含上述配置。Apple 在飞牛部署中同样需要 HTTPS 反向代理。

旧数据库启动时自动添加可空的 `oidc_identities` JSON 字段；原用户的空值按未绑定身份处理。
数据库用户需要 `ALTER` 权限。新表直接使用新结构，Redis 锁协调跨节点账户创建和登录。

## 生产和多节点部署

```dotenv
UNIBOT_AUTH_SECRET=replace-with-a-long-random-production-secret
UNIBOT_AUTH_SESSION_HOURS=168
UNIBOT_AUTH_COOKIE_SECURE=true
UNIBOT_FRONTEND_BASE_URL=https://unibot.example.com
UNIBOT_GITHUB_CALLBACK_URL=https://unibot.example.com/api/auth/github/callback
```

- 所有 Unibot 后端节点必须共享相同的 `UNIBOT_AUTH_SECRET`，这样任意节点都能验证同一个会话。
- 用户记录存入现有 MySQL 权威存储；Redis 锁串行化跨节点账户创建和 OAuth 登录，防止重复账户。
- 前端和 `/api` 应通过同一个站点提供服务。若使用反向代理，保留 HTTPS 和 Cookie 响应头。
- `UNIBOT_AUTH_SECRET`、各提供方 Client secret 和 Apple 私钥不得提交到 Git；轮换认证密钥会使已有会话全部失效。
