---
name: video-transcribe
description: 将视频 URL 或本地视频文件转写为带关键帧图片的 Markdown 文档，并生成不损失有效信息的精炼版。用于视频转写、课程录播、B 站或 YouTube 视频笔记、带关键帧的 Markdown 转写。
---

# Video Transcribe

将视频 URL 或本地视频文件转写为带关键帧图片的 Markdown，并生成同目录精炼版。

## 配置

调用前先确认这些配置，缺失时让用户提供，不要使用硬编码私钥：

- `OPENROUTER_API_KEY`: OpenRouter API key，或用户指定的兼容 OpenAI Chat Completions API key。
- `TRANSCRIBE_SCRIPT`: 本地转写脚本路径；如果使用分发包，默认为分发包内 `scripts/video_transcribe.py`。
- `TRANSCRIBE_MODEL`: 可选，默认 `google/gemini-3-flash-preview`。
- `REFINE_MODEL`: 可选，精炼模型默认 `google/gemini-3-flash-preview`。
- `TRANSCRIBE_OUTPUT_DIR`: 可选，工作目录。
- `TRANSCRIBE_SAVE_DIR`: 可选，最终保存目录。

示例命令：

```bash
python3 "${TRANSCRIBE_SCRIPT:-./scripts/video_transcribe.py}" "<视频URL或本地路径>" \
  --api-key "$OPENROUTER_API_KEY" \
  --model "${TRANSCRIBE_MODEL:-google/gemini-3-flash-preview}" \
  --refine-model "${REFINE_MODEL:-google/gemini-3-flash-preview}" \
  --output-dir "${TRANSCRIBE_OUTPUT_DIR:-./transcribe_work}" \
  --save-dir "${TRANSCRIBE_SAVE_DIR:-./transcribe_output}" \
  --save-images
```

如需额外生成精炼版，再加 `--refine`。默认不生成精炼版，以节省模型 token。
精炼会上传完整 Markdown 以及 Markdown 中引用的关键帧图片，让模型根据图片内容只保留高信息密度关键帧；默认使用 `google/gemini-3-flash-preview`。

## 工作流

1. **确认输入**
   - 获取视频 URL 或本地视频路径。
   - 对 URL 先用 `yt-dlp --get-duration <url>` 估算时长；本地文件可用 `ffprobe`。

2. **分辨率策略**
   - 短视频（< 30 分钟）：默认 1080p，不加 `--resolution`。
   - 长视频（>= 30 分钟）：
     - 访谈/播客/纯对话：可用 `--resolution 480` 或 360。
     - 课程录播、PPT、技术教程、屏幕演示：保留默认 1080p。
     - 拿不准时默认 1080p。

3. **长视频主动切段**
   - 短视频（< 30 分钟）：直接运行转写脚本。
   - 长视频（>= 30 分钟）：默认先切段再转写，不要等失败后才切。
   - 推荐切段长度：
     - 课程录播、PPT/屏幕讲解、技术教程：25 分钟一段。
     - 访谈/播客：45-60 分钟一段；失败再降到 25-30 分钟。
   - 用 `ffmpeg -f segment` 切段：

```bash
ffmpeg -y -i "<video>" -map 0 -c copy \
  -f segment -segment_time 1500 -reset_timestamps 1 \
  "<segments_dir>/part_%03d.mp4"
```

   - 逐段调用同一 `--model`，每段输出单独保存。
   - 重跑时跳过已有分段、已有分段转写和已有最终稿。

4. **合并长视频结果**
   - 每个分段输出目录应保留 Markdown 和 `frames/`。
   - 合并到完整稿时：
     - 为每段添加 `## Part N (MM:SS 起)`。
     - 把各段 `frames/` 复制到最终目录的 `frames/part_NNN/`。
     - 将 Markdown 内图片路径从 `frames/` 改写为 `frames/part_NNN/`。

5. **开头寒暄清理**
   - 每次转写脚本返回后，只读取输出 Markdown 的前 5-10 行，不要把整篇内容读入上下文。
   - 如果开头出现模型自述/寒暄/任务说明，例如：
     - `好的，这是为您转写的视频内容...`
     - `以下是视频转写内容...`
     - `已根据图片信息校对专业术语...`
     - `下面是整理后的转写...`
   - 只删除文件开头这些非视频内容，保留从第一句真实课程/视频内容或第一个图片标记开始的正文。
   - 清理必须保守：只处理文件开头前几行；不要在正文中全局删除“好的”等词，因为那可能是说话人真实内容。

6. **生成精炼版（可选）**
   - 只有用户明确要求精炼版、笔记版、复习版，或命令带 `--refine` 时才生成。
   - 默认只保留完整转写，避免把整篇转写和关键帧再次发送给模型，节省 token。
   - 启用后在最终 Markdown 同目录创建 `<原文件名>_精炼.md`。
   - 要求：不损失有效信息、概念、推理链、例子；只去掉重复和口水话。
   - 上传 Markdown 中引用的关键帧图片，交给模型只保留它认为关键的几张高信息密度图片。
   - 课程转写和普通视频转写使用同一套精炼 prompt 与同一套脚本逻辑。
   - 信息密度高的课程内容应轻度精炼；访谈寒暄可更强压缩。

## 异常处理

- 模型绝不擅自切换。用户或默认指定的 `--model` 必须贯穿始终。
- 分段仍失败时，缩短该分段：
  - 课程录播：25 分钟降到 15 分钟。
  - 访谈/播客：60 分钟降到 30 分钟。
- API 403 或连接失败：优先检查代理、VPN、API key 和余额，不要直接换模型。
- 下载断开：用下载工具的断点续传能力继续，不要删除半成品。
- 图片过滤失败：可对图片列表二分重试。
- token 超限：降低抽帧频率或缩短分段。

## 依赖

- `python3`
- `ffmpeg` / `ffprobe`
- `yt-dlp`
- Python 包：`requests`, `Pillow`, `numpy`，可用分发包的 `requirements.txt` 安装

$ARGUMENTS
