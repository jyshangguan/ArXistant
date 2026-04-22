# 飞书机器人配置指南（长连接模式）

本文档指导你从零开始配置 ArXistant 飞书机器人，使用**长连接模式**（WebSocket），无需公网 IP、域名或反向代理。

---

## 第一步：创建飞书应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app)，用你的飞书账号登录
2. 点击 **创建企业自建应用**
3. 填写应用名称（如 `ArXistant`）和描述，选择所在的组织，点击确认创建
4. 创建完成后，进入应用的 **凭证与基础信息** 页面，记录以下两项：
   - **App ID**（类似 `cli_xxxxxxxxxxxxxx`）
   - **App Secret**（32 位字符串）

## 第二步：添加机器人能力

1. 在左侧导航栏点击 **添加应用能力**
2. 找到 **机器人**，点击添加
3. 添加成功后，左侧会出现 **机器人** 菜单，可以设置机器人的名称、头像、描述

## 第三步：配置权限

1. 左侧导航栏进入 **权限管理**
2. 搜索并开启以下权限：

| 权限名称 | 权限标识 | 用途 |
|---------|---------|------|
| 获取与发送单聊、群组消息 | `im:message` | 接收用户消息 |
| 以应用的身份发消息 | `im:message:send_as_bot` | 发送回复和卡片消息 |

3. 开启后，页面顶部会提示需要发布才能生效。**先不要发布**，等全部配置完成后再统一发布。

## 第四步：配置事件订阅（长连接模式）

1. 左侧导航栏进入 **事件订阅**
2. 在连接方式中选择 **长连接（推荐）** —— 这是最简单的方式，无需公网地址
3. 在页面下方找到 **Encrypt Key**（加密策略），建议设置一个，记下这个值
4. 点击 **添加事件**，搜索并添加：
   - `接收消息 im.message.receive_v1`
5. 在事件订阅页面上方找到 **Verification Token**，记下这个值

## 第五步：将机器人添加到群聊

1. 打开飞书，创建或打开一个群聊
2. 点击群聊右上角 **设置** → **群机器人** → **添加机器人**
3. 搜索你的应用名称（如 `ArXistant`），点击添加
4. 启动 bot 服务后（见第七步），在群里发任意消息，服务端日志中会打印类似：
   ```
   Received message from oc_xxxxxxxxxxxxxx: /help
   ```
   其中 `oc_xxxxxxxxxxxxxx` 就是这个群的 `chat_id`，记下来。

## 第六步：配置本地环境变量

编辑项目根目录的 `.env` 文件，填入之前记录的值：

```bash
# 飞书机器人凭证（从第一步和第四步获取）
FEISHU_APP_ID=cli_xxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_VERIFICATION_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# FEISHU_ENCRYPT_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx   # 可选但建议设置
```

编辑 `config/settings.yaml`，填入 chat_id：

```yaml
bot:
  target_chat_id: "oc_xxxxxxxxxxxxxx"   # 从第五步获取
  report_cron: "0 9 * * *"               # 每天早上 9 点推送日报
```

## 第七步：发布应用并启动 Bot 服务

1. 回到飞书开放平台，点击右上角 **版本管理与发布** → **创建版本** → 填写版本号和更新说明 → **申请发布**
2. 管理员审批通过后（如果你是管理员可以直接通过），应用生效

3. 启动 bot 服务：

```bash
conda activate llm
cd /home/shangguan/Softwares/my_modules/ArXistant
python -m src.bot.server
```

看到以下日志说明启动成功：

```
INFO     Initializing ArXistant bot service...
INFO     Database initialized at data/arxistant.db
INFO     Feishu client initialized
INFO     Scheduler started
INFO     WebSocket client thread started
INFO     Starting WebSocket client...
DEBUG    connecting to wss://...
```

> 如果 `target_chat_id` 为空，日志会显示 "No target_chat_id configured, scheduler not started"，这是正常的——获取到 chat_id 后填入 settings.yaml 重启即可。

## 测试

在飞书群聊中测试：

| 测试命令 | 预期结果 |
|---------|---------|
| `/help` | 收到命令列表卡片 |
| `/tree` | 收到知识树卡片 |
| `/prefs` | 收到偏好设置卡片（初始为空） |
| `/scan 2504.12345` | 先收到"Processing..."，然后收到扫描结果卡片 |
| `/read 2504.12345` | 先收到"Processing..."，然后收到阅读笔记卡片 |
| `/report GA` | 收到 GA 类别的日报卡片 |
| `/reset` | 收到"Session reset"提示 |
| 随便发一句中文，如"最近有哪些关于星系棒的文章？" | 收到对话式回复 |

## 常见问题

| 问题 | 解决方法 |
|------|---------|
| WebSocket 连接失败 | 检查 `.env` 中 `FEISHU_APP_ID` 和 `FEISHU_APP_SECRET` 是否正确 |
| 机器人不回复消息 | 检查 `.env` 中 `GLM_API_KEY` 是否已设置（LLM 调用需要） |
| `/scan` 一直显示"Processing..."没有后续 | 查看服务端日志，可能是 LLM API 调用失败或超时 |
| 定时日报没有推送 | 检查 `config/settings.yaml` 中 `bot.target_chat_id` 是否正确填写 |
| `Chat ID` 在哪里找？ | 启动服务后在群里发消息，看服务端日志中的 `Received message from xxx:` |
| 应用已发布但收不到事件 | 确认事件订阅选择了**长连接模式**，且添加了 `im.message.receive_v1` 事件 |
