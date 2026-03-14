# feishu-claude

用飞书机器人和 Claude 对话，支持连续会话和工具调用（读写文件、执行命令等）。

```
你（飞书）──► feishu-claude ──► Claude Agent SDK ──► Claude
                   │                    │
             session 持久化        工具调用进度实时回显
```

## 前置条件

1. **Claude Code CLI 已安装并登录**

   ```bash
   npm install -g @anthropic-ai/claude-code
   claude login
   ```

2. **飞书自建应用已创建**，并完成以下配置（详见下方）

---

## 飞书应用配置

在 [open.feishu.cn](https://open.feishu.cn) 创建自建应用，需要开通：

**权限管理 → 添加权限：**
- `im:message` （发送消息）
- `im:message.group_at_msg` （可选，响应群组 @消息）

**事件订阅 → 添加事件：**
- `im.message.receive_v1`

**事件订阅 → 连接方式：**
- 选择 **长连接** （不需要公网 IP，本地运行即可）

配置完成后，发布应用版本并审核通过。

---

## 安装

```bash
cd ~/Codes/feishu-claude

# 复制并填写环境变量
cp .env.example .env
# 编辑 .env，填入 FEISHU_APP_ID 和 FEISHU_APP_SECRET

# 安装依赖（需要 uv）
uv sync
```

---

## 启动

```bash
uv run feishu-claude
```

或直接：

```bash
uv run python -m feishu_claude.main
```

看到 `Starting feishu-claude bot` 后，去飞书和机器人发消息即可。

---

## 使用方式

| 操作 | 说明 |
|------|------|
| 直接发消息 | Claude 回复，自动维持上下文 |
| `/reset` | 清空当前对话，开启新会话 |

每个飞书 chat（私聊或群聊）有独立的 Claude session，重启服务后上下文依然保留（session 存储在 `~/.feishu-claude/sessions.json`）。

---

## 配置说明（.env）

| 变量 | 必填 | 说明 |
|------|------|------|
| `FEISHU_APP_ID` | ✅ | 飞书应用 App ID |
| `FEISHU_APP_SECRET` | ✅ | 飞书应用 App Secret |
| `ALLOWED_USER_IDS` | 可选 | 白名单 open_id，逗号分隔；留空则所有人可用 |
| `CLAUDE_CWD` | 可选 | Claude 执行文件操作的工作目录，默认为 `~` |

---

## 工作原理

1. 飞书 WebSocket 长连接接收消息（无需公网 IP）
2. 按 `chat_id` 查找已有 Claude session id
3. 调用 `claude-agent-sdk` 的 `query()`，传入 `resume=session_id` 恢复上下文
4. 流式事件中，工具调用（读文件、执行命令等）实时推送进度到飞书
5. 最终回复发回飞书，session id 持久化到本地

---

## 注意事项

- **Claude Code CLI 必须在本机已登录**，因为 `claude-agent-sdk` 是对 CLI 的进程封装
- 同一个 chat 的消息会串行处理，不会并发乱序
- 群聊中机器人只响应直接发给它的消息（不是所有群消息）
