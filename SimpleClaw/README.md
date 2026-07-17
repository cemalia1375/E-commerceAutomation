# Mojing Agent

Mojing Agent 是魔镜业务的 Agent 服务，基于 FastAPI + Uvicorn 提供对话、设备回调、场景化子 Agent、记忆处理和后台任务调度能力。

服务同时兼容两类调用入口：

- 小程序 / 业务后端：`POST /agent/chat`
- 硬件 / 火山通道：`POST /v1/chat/completions`，返回 OpenAI 兼容 SSE 流

## 核心能力

- 主 Agent 对话编排：承接用户会话、上下文装配、工具调用和流式输出。
- 场景化子 Agent：支持肌肤日记、深度报告、设备新手期、设备连接帮助等业务场景。
- 记忆与状态管理：维护租户、会话、画像、动态状态和业务快照。
- 设备事件接入：处理设备拍照成功、失败等内部回调。
- 后台任务调度：基于 Redis / MySQL 执行异步任务、cron 轮询和结果回写。
- Admin 调试页：提供 prompt、场景、会话、租户状态和任务运行的本地调试入口。

## 技术栈

- Python 3.11+
- FastAPI / Uvicorn
- MySQL / Redis
- OpenAI SDK 兼容接口
- Pytest
- Docker / Docker Compose

## 目录结构

```text
.
├── Mojing/              # 魔镜 Agent 主业务代码
│   ├── api/             # FastAPI 服务入口与路由
│   ├── agent/           # 主 Agent、调度与执行逻辑
│   ├── subagent/        # 场景化子 Agent
│   ├── skills/          # 可热加载的场景技能
│   ├── memory/          # 记忆、画像、账本与压缩逻辑
│   ├── runtime/         # 任务运行时与队列消费
│   └── workspace/       # Agent 工作区 prompt / 行为约束
├── admin/               # Admin 调试路由与页面
├── simpleclaw/          # 通用 Agent 框架与历史兼容模块
├── script/              # 本地启动、Docker、测试和场景脚本
├── tests/               # 自动化测试
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 环境配置

首次启动前复制环境变量模板：

```bash
cp .env.example .env
```

按实际环境填写 `.env`。常用变量包括：

| 变量 | 说明 |
|------|------|
| `VOLCENGINE_API_KEY` | 火山 / 豆包模型 API Key |
| `VOLCENGINE_API_BASE` | 模型服务地址 |
| `VOLCENGINE_MODEL` | 默认主模型 |
| `VOLCENGINE_HOOK_MODEL` | hook / 轻量任务模型 |
| `VOLCENGINE_FIRST_TOKEN_MODEL` | first token 模型 |
| `SKIN_DIARY_CROP_ENDPOINT_URL` | 肌肤日记裁剪服务地址 |

`.env` 已被 `.gitignore` 排除，不要提交真实密钥。

## 本地开发

安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

启动本地服务：

```bash
./script/start_local_agent.sh
```

默认监听 `8000` 端口。也可以指定端口：

```bash
./script/start_local_agent.sh 8003
```

启动成功后访问：

- 健康检查：<http://127.0.0.1:8000/health>
- Admin 页面：<http://127.0.0.1:8000/admin>
- Chat Completions：`POST http://127.0.0.1:8000/v1/chat/completions`
- Agent Chat：`POST http://127.0.0.1:8000/agent/chat`

## Docker 部署

生产或联调环境推荐使用 Docker Compose 运行 Agent 与 Redis。

前置条件：

- 已安装 Docker 与 Docker Compose v2
- 项目根目录存在 `.env`
- 宿主机 `8000` 端口未被占用，或通过 `APP_PORT` 指定其他端口

快速部署：

```bash
./script/docker_agent.sh deploy
```

常用命令：

```bash
./script/docker_agent.sh status    # 查看容器状态与健康检查
./script/docker_agent.sh logs      # 跟踪 Agent 日志
./script/docker_agent.sh restart   # 重启，不重新构建
./script/docker_agent.sh stop      # 停止容器
./script/docker_agent.sh pause     # 暂停容器进程
./script/docker_agent.sh unpause   # 恢复暂停
./script/docker_agent.sh build     # 仅构建镜像
./script/docker_agent.sh down      # 停止并移除 compose 栈
```

Compose 组件：

| 组件 | 容器名 | 说明 |
|------|--------|------|
| Agent | `agent` | 镜像 `simpleclaw-agent:latest`，对外暴露 `8000` |
| Redis | `simpleclaw-redis-1` | Agent 队列与任务调度依赖 |

可选环境变量：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `APP_PORT` | `8000` | 宿主机映射端口 |
| `TAG` | `latest` | 镜像标签 |
| `COMPOSE_PROJECT_NAME` | `simpleclaw` | Compose 项目名 |

Docker Agent 与宿主机本地 Agent 同一时刻只能运行一个：

| 场景 | 命令 | 端口 |
|------|------|------|
| 生产 / 正式环境 | `./script/docker_agent.sh deploy` | 8000 |
| 本地调试 | `./script/start_local_agent.sh` | 8000 |

## 测试

日常单元测试：

```bash
python script/test.py --unit
```

运行全部测试：

```bash
python script/test.py --all
```

筛选测试：

```bash
python script/test.py --unit -- -k memory
python script/test.py --unit -- tests/test_memory_ledger.py
```

CI / 覆盖率：

```bash
python script/test.py --all --ci
python script/test.py --unit --coverage
```

## 与魔镜后端对接

魔镜 Java 后端通过环境变量指向 Agent：

```bash
MOJING_AGENT_BACKEND_URL=http://127.0.0.1:8000
```

后端会将请求转发到：

```text
{MOJING_AGENT_BACKEND_URL}/v1/chat/completions
```

修改 Agent 地址后，需要重启后端服务。

## 更新与发布

更新代码后重新部署：

```bash
git pull
./script/docker_agent.sh deploy
```

`deploy` 会先构建镜像，构建成功后再切换容器，避免构建失败导致线上服务中断。

若 Docker 构建时 `apt-get` 报 `502 Bad Gateway`，通常是 Docker 客户端代理影响。当前 `Dockerfile` 与 `docker-compose.yml` 已在 build 阶段禁用代理并使用国内镜像源，一般可以直接重新执行部署命令。

## 安全说明

- 不要提交 `.env`、真实 API Key、数据库密码或生产日志。
- 管理后台仅用于开发和内部调试，不应直接暴露到公网。
- 外部模型、裁剪服务、MySQL 和 Redis 地址应通过环境变量注入。
