# feishu-claude

用飞书机器人和 Claude 对话，支持多飞书应用 / 多会话、连续会话和工具调用（读写文件、执行命令等）。

```
你（飞书）──► feishu-claude ──► Claude Agent SDK ──► Claude
                   │                    │
             per-runtime session     工具调用进度实时回显
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
- `im:message`
- `im:message.group_at_msg` （响应群组 @消息）
- `im:message:send_as_bot`

**事件订阅 → 添加事件：**
- `im.message.receive_v1`

**事件订阅 → 连接方式：**
- 选择 **长连接** （不需要公网 IP，本地运行即可）

---

## 安装

```bash
cd ~/codes/feishu-claude

# 复制并填写配置
cp feishu-claude.yaml.example feishu-claude.yaml
# 编辑 feishu-claude.yaml，填入 app_id / app_secret / chat_id / cwd

# 安装依赖（需要 uv）
uv sync
```

---

## 启动

```bash
./run.sh
# 或
uv run feishu-claude --config feishu-claude.yaml
```

---

## 使用方式

| 操作 | 说明 |
|------|------|
| 直接发消息 | Claude 回复，自动维持上下文 |
| `/reset` | 清空当前对话，开启新会话 |
| `/compact` | 压缩上下文，保留摘要继续对话 |

每条 runtime 有独立的 Claude session，重启服务后上下文依然保留。

---

## 配置说明（feishu-claude.yaml）

顶层两段：`apps` 和 `runtimes`。`apps` 声明所有飞书应用凭据，`runtimes` 声明每一个"一个 app × 一个 chat"的绑定。

```yaml
apps:
  work:
    app_id: cli_xxx
    app_secret: xxx
  personal:
    app_id: cli_yyy
    app_secret: yyy

runtimes:
  work-eng-group:
    app: work
    chat_id: oc_aaa
    allowed_user_ids: [ou_xxx, ou_yyy]   # 可选，空/缺省 = 不做白名单
    session_path: /path/session.json     # 可选，缺省写到 ~/.feishu-claude/runtimes/<id>/session.json
    claude:
      cwd: /home/ubuntu/work             # 必填，Claude 执行文件操作的工作目录
      allowed_tools: [Read, Edit, Write, Bash, Glob, Grep, Skill]
      permission_mode: acceptEdits       # default / acceptEdits / plan / bypassPermissions
  personal-playground:
    app: personal
    chat_id: oc_ccc
    claude:
      cwd: /home/ubuntu/play
```

路由规则：

- 每条 runtime 精确绑定一个 `(app, chat_id)`。未在 runtimes 登记的 chat 一律忽略。
- 同一个 `(app, chat_id)` 对不能出现两次。
- 一个 app 可以被多条 runtime 复用（同一个机器人进不同的群/私聊）。
- 不同 app 下的相同 chat_id 视为两条独立 runtime（因为是两个不同的机器人）。

---

## 工作原理

1. 启动时为每个 app 拉起一条飞书 WebSocket 长连接，事件处理器闭包记住自己的 app_key
2. 消息到达后按 `(app_key, chat_id)` 路由到对应 runtime；找不到 runtime 直接丢弃
3. runtime 从自己的 session 文件读取 Claude session id，调用 `claude-agent-sdk` 的 `query()`，传入 `resume=session_id` 恢复上下文
4. 最终回复发回飞书（ReplyMessage，可能在 thread 内），新的 session id 写回该 runtime 的 session 文件

---

## 会话迁移

旧版本使用 `~/.feishu-claude/sessions.json` 单文件按 chat_id 存 session。首次以 YAML 配置启动时会自动迁移：遍历所有 runtime，把旧文件中匹配 chat_id 的 session_id 写到对应 runtime 的新路径，然后删除旧文件。无需人工干预。

---

## 注意事项

- **Claude Code CLI 必须在本机已登录**，因为 `claude-agent-sdk` 是对 CLI 的进程封装
- 同一条 runtime 的消息串行处理（per-runtime asyncio.Lock），不同 runtime 并行
- 群聊中机器人只响应直接发给它的消息
