# XMSmartLink 视频中转能力测试步骤

本文档用于在测试服务器上验证 `api.xmsmartlink.cn` 的视频处理稳定性，重点覆盖 Flowcut 后续替换 Gemini 中转 API 前需要确认的能力。

## 1. 本地准备

服务器 SSH 信息：

```sshconfig
Host test
    HostName 118.145.77.153
    User root
    Port 22
    IdentityFile /Users/shengxingou-1/.ssh/mojing_test.pem
```

Windows 本地私钥路径：

```powershell
D:\Internship\ManJu\mojing_test.pem
```

建议先修正私钥权限。如果你用 PowerShell 自带 `ssh`：

```powershell
icacls "D:\Internship\ManJu\mojing_test.pem" /inheritance:r
icacls "D:\Internship\ManJu\mojing_test.pem" /grant:r "$env:USERNAME:R"
```

登录服务器：

```powershell
ssh -i "D:\Internship\ManJu\mojing_test.pem" root@118.145.77.153
```

## 2. 上传脚本和视频样本

在本地仓库根目录执行：

```powershell
scp -i "D:\Internship\ManJu\mojing_test.pem" `
  "D:\PyCharm\E-commerceAutomation\SimpleClaw\Flowcut\scripts\xmsmartlink_video_bench.py" `
  root@118.145.77.153:/root/xmsmartlink_video_bench.py
```

准备 2-3 个样本视频，建议：

- `5s` 左右，小于 5 MB
- `30s` 左右，10-30 MB
- `60s` 左右，50-100 MB，运行时用脚本压缩后再测

上传示例：

```powershell
scp -i "D:\Internship\ManJu\mojing_test.pem" `
  "D:\path\to\sample_5s.mp4" `
  "D:\path\to\sample_30s.mp4" `
  root@118.145.77.153:/root/
```

## 3. 服务器环境准备

SSH 到服务器后执行：

```bash
python3 --version
mkdir -p /root/xmsmartlink-test/reports
mv /root/xmsmartlink_video_bench.py /root/xmsmartlink-test/
cd /root/xmsmartlink-test
```

安装依赖：

```bash
python3 -m pip install -U httpx
```

如果需要压缩大视频，安装或确认 `ffmpeg`：

```bash
ffmpeg -version
```

如果没有：

```bash
apt-get update && apt-get install -y ffmpeg
```

## 4. 设置密钥

不要把密钥写进脚本或命令历史以外的文件。建议只放环境变量：

```bash
export XMSMARTLINK_API_KEY='你的 sk-... 密钥'
export XMSMARTLINK_BASE_URL='https://api.xmsmartlink.cn'
export XMSMARTLINK_TOKEN_URL='https://api.xmsmartlink.cn/console/token'
export XMSMARTLINK_MODEL='gemini-3.1-flash-lite-preview'
```

如果确认该中转只支持其他模型名，把 `XMSMARTLINK_MODEL` 改成控制台里实际可用的模型。

## 5. 第一阶段：连通性测试

Gemini REST 形态，默认把 key 放在 query 参数：

```bash
python3 xmsmartlink_video_bench.py \
  --mode gemini \
  --include-text-smoke \
  --repeat 3 \
  --concurrency 1 \
  --out-dir reports
```

如果返回 `401/403`，尝试 key 放 header：

```bash
python3 xmsmartlink_video_bench.py \
  --mode gemini \
  --key-placement x-goog-api-key \
  --include-text-smoke \
  --repeat 3 \
  --concurrency 1 \
  --out-dir reports
```

或：

```bash
python3 xmsmartlink_video_bench.py \
  --mode gemini \
  --key-placement bearer \
  --include-text-smoke \
  --repeat 3 \
  --concurrency 1 \
  --out-dir reports
```

如果中转是 OpenAI-compatible 接口：

```bash
python3 xmsmartlink_video_bench.py \
  --mode openai \
  --key-placement bearer \
  --include-text-smoke \
  --repeat 3 \
  --concurrency 1 \
  --out-dir reports
```

## 6. 第二阶段：小视频能力测试

先用小视频，避免一开始就被请求体大小限制干扰：

```bash
python3 xmsmartlink_video_bench.py \
  --mode gemini \
  --video /root/sample_5s.mp4 \
  --repeat 5 \
  --concurrency 1 \
  --max-inline-mb 8 \
  --out-dir reports
```

如果小视频成功，再测 30 秒视频：

```bash
python3 xmsmartlink_video_bench.py \
  --mode gemini \
  --video /root/sample_30s.mp4 \
  --repeat 10 \
  --concurrency 1 \
  --max-inline-mb 8 \
  --compress \
  --out-dir reports
```

`--compress` 会用 ffmpeg 压到较低分辨率和帧率，模拟当前项目里“中转模式尽量走 inline 视频”的策略。

## 7. 第三阶段：稳定性测试

低并发：

```bash
python3 xmsmartlink_video_bench.py \
  --mode gemini \
  --video /root/sample_5s.mp4 \
  --video /root/sample_30s.mp4 \
  --repeat 20 \
  --concurrency 1 \
  --compress \
  --out-dir reports
```

轻并发：

```bash
python3 xmsmartlink_video_bench.py \
  --mode gemini \
  --video /root/sample_5s.mp4 \
  --video /root/sample_30s.mp4 \
  --repeat 20 \
  --concurrency 3 \
  --compress \
  --out-dir reports
```

业务上限探测：

```bash
python3 xmsmartlink_video_bench.py \
  --mode gemini \
  --video /root/sample_5s.mp4 \
  --video /root/sample_30s.mp4 \
  --repeat 30 \
  --concurrency 5 \
  --compress \
  --timeout 240 \
  --out-dir reports
```

## 8. 查看结果

每次运行会生成两个文件：

```bash
ls -lh reports/
```

- `xmsmartlink_video_YYYYMMDD_HHMMSS.jsonl`：逐请求明细
- `xmsmartlink_video_YYYYMMDD_HHMMSS.md`：汇总报告

查看最新报告：

```bash
ls -t reports/*.md | head -1 | xargs cat
```

快速统计 500 错误：

```bash
grep '"error_type": "server_500"' reports/*.jsonl | wc -l
```

快速查看失败样本：

```bash
grep '"ok": false' reports/*.jsonl | tail -20
```

## 9. 替换判定标准

建议满足以下条件再替换项目当前中转：

- 文本 smoke test 成功率 >= 99%
- 小视频成功率 >= 98%
- 30 秒视频成功率 >= 95%
- 轻并发 3 下没有持续性 500
- `server_500` 错误率 <= 1%
- P95 总耗时在业务可接受范围内
- 返回内容可稳定解析为 JSON 或可通过现有容错解析修复

如果只有单请求可用、并发 3 就大量 500，建议只作为备用通道，不直接替换主链路。

