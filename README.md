# Claude Agent SDK on AWS Bedrock AgentCore Runtime

本项目展示如何将基于 **Claude Agent SDK** 的对话 Agent 部署到 **AWS Bedrock AgentCore Runtime**。

## 项目结构

```
claude-agentcore/
├── agent.py              # Agent 主程序 (使用 Claude Agent SDK)
├── deploy.py             # 使用 starter toolkit 部署
├── deploy_manual.py      # 手动部署 (支持 CodeBuild)
├── test_runtime.py       # Session 持久化测试脚本
├── Dockerfile            # 容器配置
├── requirements.txt      # Python 依赖
└── README.md
```

## 前置条件

- Python 3.10+
- AWS CLI 已配置 (`aws configure`)
- AWS 账号具有以下权限：
  - Amazon Bedrock AgentCore
  - Amazon ECR
  - AWS IAM
  - AWS CodeBuild (如果使用云端构建)
  - Amazon S3 (如果使用云端构建)

## 快速开始

### 1. 安装依赖

```bash
cd sample-deploy-ClaudeAgentSDK-based-agents-to-AgentCore-Runtime
pip install -r requirements.txt
```

### 2. 部署 Agent

**方式 A: 使用 Starter Toolkit (推荐)**

```bash
python deploy.py
```

**方式 B: 手动部署 (本地 Docker)**

```bash
python deploy_manual.py
```

**方式 C: 手动部署 (AWS CodeBuild, 无需本地 Docker)**

```bash
python deploy_manual.py --codebuild
```

### 3. 测试调用

部署成功后，脚本会自动进行测试调用。你也可以手动调用：

```python
from deploy_manual import ManualDeployer

deployer = ManualDeployer(agent_name="conversation_agent_claudeagentsdk")
response = deployer.invoke(
    agent_arn="arn:aws:bedrock-agentcore:us-east-1:123456789:agent-runtime/xxx",
    prompt="Hello, how are you?"
)
print(response)
```

**测试 Session 持久化**

```bash
python test_runtime.py --agent-id <your-agent-id> --region us-east-1
```

该脚本会测试会话记忆功能，包含四个测试用例：

| 测试 | 流程 | 预期结果 |
|------|------|---------|
| Test 1 | Session A 中告诉 agent 用户信息 | Agent 正常响应 |
| Test 2 | Session B 中询问 agent 是否记得 | 不记得（跨 session 隔离）|
| Test 3 | 返回 Session A 询问是否记得 | 记得（同 session 保持上下文）|
| Test 4 | 停止 Session A 后重新调用 | 根据实现可能记得或忘记 |

### 4. 清理资源

```bash
python deploy_manual.py --cleanup <agent-id>
```

---

## Claude Code 配置说明

### 什么是 Claude Agent SDK?

Claude Agent SDK 是 Anthropic 提供的 Agent 开发框架，它使用 **Claude Code CLI** 作为运行时。Claude Code 支持多种后端：

1. **Anthropic API** - 直接调用 Anthropic API (需要 `ANTHROPIC_API_KEY`)
2. **AWS Bedrock** - 使用 AWS Bedrock 中的 Claude 模型 (需要 AWS 凭证)

### Bedrock 模式配置

本项目使用 **AWS Bedrock 模式**，在 `Dockerfile` 中配置以下环境变量：

```dockerfile
# 启用 Bedrock 模式
ENV CLAUDE_CODE_USE_BEDROCK=1

# 指定 Claude 模型 (可选)
ENV ANTHROPIC_MODEL=us.anthropic.claude-sonnet-4-20250514-v1:0
```

### 环境变量说明

| 环境变量 | 说明 | 示例值 |
|---------|------|--------|
| `CLAUDE_CODE_USE_BEDROCK` | 启用 Bedrock 模式 | `1` |
| `ANTHROPIC_MODEL` | Claude 模型 ID | `us.anthropic.claude-sonnet-4-20250514-v1:0` |
| `AWS_REGION` | AWS 区域 | `us-east-1` |

### 可用的 Bedrock Claude 模型

| 模型 | Model ID |
|------|----------|
| Claude Sonnet 4 | `us.anthropic.claude-sonnet-4-20250514-v1:0` |
| Claude Opus 4.5 | `us.anthropic.claude-opus-4-5-20251101-v1:0` |
| Claude 3.5 Sonnet v2 | `us.anthropic.claude-3-5-sonnet-20241022-v2:0` |
| Claude 3.5 Haiku | `us.anthropic.claude-3-5-haiku-20241022-v1:0` |

> **注意**: 使用 `us.` 前缀表示跨区域推理配置 (Cross-region inference profile)

### IAM 权限

AgentCore Runtime 的执行角色需要以下权限才能访问 Bedrock：

```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream"
            ],
            "Resource": "arn:aws:bedrock:*::foundation-model/*"
        }
    ]
}
```

---

## Agent 代码说明

### agent.py

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

app = BedrockAgentCoreApp()

@app.entrypoint
async def agent_invocation(payload, context):
    """AgentCore Runtime 入口点"""
    prompt = payload.get("prompt", "Hello")

    # 使用 Claude Agent SDK 调用
    result = []
    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            system_prompt="You are a helpful assistant.",
        )
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    result.append(block.text)

    return {"result": "".join(result)}

if __name__ == "__main__":
    app.run()
```

### 关键组件

| 组件 | 说明 |
|------|------|
| `BedrockAgentCoreApp` | AgentCore Runtime 应用框架 |
| `@app.entrypoint` | 标记函数为 Runtime 入口点 |
| `query()` | Claude Agent SDK 的查询函数 |
| `ClaudeAgentOptions` | 配置 system prompt 等选项 |

---

## Dockerfile 说明

```dockerfile
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# 安装 Claude Code CLI 依赖
RUN apt-get update && apt-get install -y \
    curl git nodejs npm \
    && rm -rf /var/lib/apt/lists/*

# 安装 Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Claude Code Bedrock 配置
ENV CLAUDE_CODE_USE_BEDROCK=1 \
    ANTHROPIC_MODEL=us.anthropic.claude-sonnet-4-20250514-v1:0

# 安装 Python 依赖
COPY requirements.txt .
RUN uv pip install -r requirements.txt

# 复制应用代码
COPY . .

# 启动 Agent
CMD ["opentelemetry-instrument", "python", "-m", "agent"]
```

---

## 部署流程详解

### 使用 Starter Toolkit (`deploy.py`)

```
Step 1: Configuring runtime...     # 生成 Dockerfile
Step 2: Patching Dockerfile...     # 注入 Claude Code 配置
Step 3: Launching agent...         # 构建镜像、推送 ECR、创建 Runtime
Step 4: Waiting for deployment...  # 等待状态变为 READY
```

### 手动部署 (`deploy_manual.py`)

```
Step 1: Create ECR repository      # 创建镜像仓库
Step 2: Build & push image         # 构建并推送 Docker 镜像
Step 3: Create IAM role            # 创建执行角色
Step 4: Create AgentCore Runtime   # 创建 Runtime
Step 5: Wait for READY             # 等待部署完成
```

---

## 故障排查

### 查看 CloudWatch 日志

Agent 运行日志会输出到 CloudWatch Logs，可以在 AWS Console 中查看或使用 AWS CLI：

```bash
aws logs filter-log-events \
    --log-group-name "/aws/bedrock-agentcore/<agent-id>" \
    --limit 50
```

### 常见错误

| 错误 | 原因 | 解决方案 |
|------|------|---------|
| `ValidationException: model ID xxx not supported` | 模型 ID 格式错误 | 使用 `us.` 前缀的跨区域推理配置 |
| `Command failed with exit code 1` | Claude Code CLI 未正确安装 | 检查 Dockerfile 中的 npm install |
| `AccessDeniedException` | IAM 权限不足 | 添加 Bedrock 调用权限 |
| `Access denied while validating ECR URI` | IAM 执行角色缺少 ECR 权限 | 为角色附加 `AmazonEC2ContainerRegistryReadOnly` 策略 |
| `Architecture incompatible: Supported architectures: [arm64]` | Docker 镜像架构不匹配 | 使用 ARM64 环境构建（`deploy_manual.py --codebuild` 已自动配置）|
| `ConflictException: An agent with the specified name already exists` | Runtime 已存在 | `deploy_manual.py` 会自动更新现有 runtime |

### 本地测试

在部署前可以本地测试 Agent：

```bash
# 设置环境变量
export CLAUDE_CODE_USE_BEDROCK=1
export ANTHROPIC_MODEL=us.anthropic.claude-sonnet-4-20250514-v1:0
export AWS_REGION=us-east-1

# 运行 Agent
python agent.py
```

然后在另一个终端测试：

```bash
curl -X POST http://localhost:8080/invocations \
    -H "Content-Type: application/json" \
    -d '{"prompt": "Hello, how are you?"}'
```

---

## 参考链接

- [AWS Bedrock AgentCore 文档](https://docs.aws.amazon.com/bedrock-agentcore/)
- [Claude Agent SDK](https://platform.claude.com/docs/agent-sdk)
- [Claude Code CLI](https://claude.ai/code)
- [Bedrock AgentCore Samples](https://github.com/awslabs/amazon-bedrock-agentcore-samples)

---

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This library is licensed under the MIT-0 License. See the LICENSE file.

