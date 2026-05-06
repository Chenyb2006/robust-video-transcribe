---
name: elrc-course-crawler
description: 爬取上海科技大学 ELRC 课程录播页面，批量下载现有录播并交给 video-transcribe 转写。用于用户提供 elrc.shanghaitech.edu.cn/learn/videoreview 课程链接，要求下载课程录播、只要屏幕画面视角、批量转写或整理成 Obsidian/Markdown 阅读文件夹。
---

# ELRC Course Crawler

用于上海科技大学 ELRC 课程录播批量抓取：从课程录播页发现已发布视频，优先选择“屏幕画面”视角，下载 MP4，并调用 `video-transcribe` 生成完整转写和精炼 Markdown。

## 配置

不要在 skill 中硬编码个人 API key、Cookie 或本机路径。调用前确认：

- `COURSE_VAULT_DIR`: 最终课程笔记仓库目录，例如 `/path/to/Obsidian/课程`。
- `OPENROUTER_API_KEY`: 转写使用的 API key，留空时向用户索取。
- `TRANSCRIBE_SCRIPT`: 本地 `video_transcribe.py` 或等价转写脚本路径；如果使用分发包，默认使用分发包内 `scripts/video_transcribe.py`。
- `TRANSCRIBE_MODEL`: 可选，默认 `google/gemini-3-flash-preview`。
- `REFINE_MODEL`: 可选，精炼模型默认 `google/gemini-3-flash-preview`。
- `ELRC_COOKIE_BROWSER`: 可选，默认 `edge`，也可为 `chrome`、`safari`、`firefox`。
- `ELRC_COOKIE_FILE`: 可选，默认 `/tmp/elrc_cookies.txt`。

最终结果统一存到：

```text
${COURSE_VAULT_DIR}/<课程名>/
```

`<课程名>` 使用 ELRC 接口返回的 `courseName`，只清理文件名非法字符，不追加 `_录播转写`。

## 输入解析

用户通常给出：

```text
https://elrc.shanghaitech.edu.cn/learn/videoreview/<courseId>?id=<courseBackId>&schoolYear=<year>&semester=<semester>
```

必须解析：

- `courseId`: 路径最后一段
- `id`: 查询参数里的课程后台 id
- `schoolYear`
- `semester`: URL 解码后的中文值，如 `春季`

## 工作流

1. **准备目录**
   - 临时工作根目录可放在当前工作区：`<课程名>_录播转写_work`。
   - 临时目录结构：
     - `manifest.json`
     - `videos/`
     - `segments/`
     - `segment_transcripts/`
     - `transcripts/`
     - `work/`
     - `_transcribe_tmp/`
   - 最终只保留 `${COURSE_VAULT_DIR}/<课程名>/` 下的阅读结果。

2. **导出 ELRC Cookie**
   - 优先从用户已登录的浏览器导出：

```bash
yt-dlp --cookies-from-browser "${ELRC_COOKIE_BROWSER:-edge}" \
  --cookies "${ELRC_COOKIE_FILE:-/tmp/elrc_cookies.txt}" \
  --skip-download --simulate \
  "https://elrc.shanghaitech.edu.cn/learn/"
```

   - 如果失败，让用户先在浏览器登录 ELRC，并确认校园网/VPN/代理环境可访问。
   - 如果接口返回 `401` 或 `403`，通常是 Cookie 过期或浏览器选错；让用户登录 ELRC 后用 `--refresh-cookies` 强制重新导出。

3. **拉取课程录播清单**
   - 请求：

```text
GET https://elrc.shanghaitech.edu.cn/learn/v1/course/recording/video/info
```

   - 参数：
     - `courseId`
     - `id`
     - `schoolYear`
     - `semester`
   - 使用带 Cookie 的 `requests.Session`。
   - ELRC 清单、下载地址接口和 MP4 下载默认绕过代理；如果本机开了梯子，校园网资源不要走代理。
   - 请求头设置：
     - `Referer`: 原课程页 URL
     - `User-Agent`: 浏览器 UA
   - 只保留 `ifStart` 为真的已发布周次。
   - 遍历 `recordingVideoInfoShows[].recordInfoDetailList[].videoInfoList[]`。
   - 三视角里只选择 `videoName` 包含 `屏幕画面` 的视频，并使用同一 MP4 的音频。
   - 写入 `manifest.json`，每项至少包含：
     - `week`
     - `weekDate`
     - `weekDay`
     - `section`
     - `videoId`
     - `scheduleId`
     - `videoName`
     - `title`
   - 如果已经有 `manifest.json`，或接口临时不可用，可用 `--manifest-file <path>` 从本地清单恢复，不必重新访问清单接口。

4. **获取下载地址**
   - 优先调用官方下载地址接口：

```text
POST https://elrc.shanghaitech.edu.cn/rman/v1/entity/download/fileinfo
Body: ["<videoId>"]
```

   - 若接口返回相对路径，拼上 `https://elrc.shanghaitech.edu.cn`。
   - 该接口可能出现 SSL EOF 或临时失败；不要卡死在此接口。

5. **直链回退**
   - 官方接口失败时使用直链规律：

```text
https://elrc.shanghaitech.edu.cn/bucket-z/unit-cwcc268v239qk92m/video/{year}/{month_no_zero}/{day_no_zero}/{urlencoded_room}/{videoId}.mp4
```

   - `{year}/{month_no_zero}/{day_no_zero}` 来自 `weekDate`，月份和日期去前导零。
   - 常见教室为 `信息学院1B-106`，URL 编码：

```text
%E4%BF%A1%E6%81%AF%E5%AD%A6%E9%99%A21B-106
```

   - 如果直链 404，检查页面或接口响应里是否能看到教室名；替换 room 后再试。
   - 直链可用性先用 `curl -I` 或小范围下载验证。
   - 在已确认教室和直链规则可用时，可加 `--prefer-direct` 跳过下载地址接口，直接下载 MP4。

6. **下载 MP4**
   - 必须断点续传，保留 `.mp4.part`：

```bash
curl -L --fail --retry 8 --retry-delay 5 --noproxy '*' -C - \
  -b "${ELRC_COOKIE_FILE:-/tmp/elrc_cookies.txt}" \
  -o "<video>.mp4.part" \
  "<download_url>"
```

   - 下载成功后再 rename 为 `.mp4`。
   - 常见 `curl: (18) transfer closed...` 是可恢复错误；继续 `-C -` 续传，不要删除 `.part`。
   - 若目标 `.mp4` 已存在且大小合理，跳过下载。

7. **转写和同步**
   - 对每个下载好的屏幕画面视频调用 `video-transcribe` skill。
   - 长课录播默认按约 25 分钟切段，逐段转写、合并。
   - 精炼版是可选项：只有用户明确要求精炼版/复习版/整理版，或命令带 `--refine` 时，才生成 `*_精炼.md`；默认只生成 `*_转写.md` 以节省 token。
   - 精炼会上传完整 Markdown 以及 Markdown 中引用的关键帧图片，让模型根据图片内容只保留高信息密度关键帧；默认使用 `google/gemini-3-flash-preview`。
   - 课程转写和普通视频转写使用 `video_transcribe.py` 中同一套精炼 prompt 与同一套脚本逻辑，不维护课程专用精炼 prompt。
   - 同步最终结果前，按 `video-transcribe` 的规则只检查 Markdown 前 5-10 行；如有模型寒暄/任务说明，只清理文件开头，不读取整篇内容。
   - 最终同步到：

```text
${COURSE_VAULT_DIR}/<课程名>/<title>/
```

   - 每节课目录必须包含：
     - `<title>_转写.md`
     - `frames/`
   - 如果启用 `--refine`，还会包含 `<title>_精炼.md`。
   - 重跑时按最终目录中的 `*_转写.md` 和 `*_精炼.md` 判断是否跳过。

8. **清理中间文件**
   - 全部转写和同步完成后，只保留 `${COURSE_VAULT_DIR}/<课程名>/` 下的最终结果。
   - 删除临时工作根目录中的：
     - `videos/`
     - `segments/`
     - `segment_transcripts/`
     - `transcripts/`
     - `work/`
     - `_transcribe_tmp/`
   - 可选把 `manifest.json` 复制到课程目录；如果用户只要阅读稿，则不保留。
   - 不要删除最终课程目录里的 Markdown 和 `frames/`。

9. **验收**
   - 检查最终目录：

```bash
find "${COURSE_VAULT_DIR}/<课程名>" -mindepth 2 -maxdepth 2 -name '*_精炼.md' -print | wc -l
find "${COURSE_VAULT_DIR}/<课程名>" -mindepth 2 -maxdepth 2 -name '*_转写.md' -print | wc -l
du -sh "${COURSE_VAULT_DIR}/<课程名>"
```

   - 报告完成数量、失败列表、最终阅读目录，以及已清理的中间目录。

## 实现要点

- 使用 `requests.Session` 加载 `${ELRC_COOKIE_FILE}` 的 Mozilla cookie jar。
- 文件名清理 `/\:*?"<>|` 等非法字符。
- `manifest.json` 是断点续跑的核心。
- 恢复任务前先检查已有进程和最终目录，避免重复下载和重复 API 调用。
- API key、Cookie、课程仓库路径都应来自用户配置或环境变量，不要写进 skill。

## 常见问题

- 模型 403 或超时：先确认代理/VPN/API key/余额，不要擅自换模型。
- 下载接口 SSL EOF：使用直链回退。
- 下载中断：继续断点续传。
- 磁盘占用大：原视频和切段视频会各占一份空间；完成后默认全部清理。
- 命令行直接运行可用：`python3 scripts/elrc_course_crawler.py "<ELRC课程链接>" --course-vault-dir "$COURSE_VAULT_DIR" --api-key "$OPENROUTER_API_KEY"`。
- 如果要精炼版，加 `--refine`；默认不精炼以节省 token。
- 精炼模型可用 `--refine-model` 或 `REFINE_MODEL` 覆盖，默认 `google/gemini-3-flash-preview`。

$ARGUMENTS
