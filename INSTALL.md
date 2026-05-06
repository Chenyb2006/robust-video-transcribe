# Shanghaitech ELRC Skills

包含两个 Codex skills 和可运行脚本：

- `elrc-course-crawler`: 批量爬取上海科技大学 ELRC 课程录播，只选“屏幕画面”，并整理成课程 Markdown 文件夹。
- `video-transcribe`: 视频转写为带关键帧图片的 Markdown，长视频会主动切段并生成精炼版。
- `scripts/video_transcribe.py`: 视频转写脚本。
- `scripts/elrc_course_crawler.py`: ELRC 课程批量下载和转写脚本。

## 安装

解压后进入目录，安装依赖：

```bash
cd shanghaitech-elrc-skills-dist
python3 -m pip install -r requirements.txt
```

还需要系统命令：

```bash
brew install ffmpeg yt-dlp
```

把 `skills/` 下的两个目录复制到自己的 Codex skills 目录：

```bash
mkdir -p ~/.codex/skills
cp -R skills/elrc-course-crawler ~/.codex/skills/
cp -R skills/video-transcribe ~/.codex/skills/
```

重启 Codex 或开启新会话后生效。

## 使用前配置

这些值不要写进 skill 文件，建议用环境变量或在对话里告诉 Codex：

```bash
export OPENROUTER_API_KEY="你的 API key"
export COURSE_VAULT_DIR="/path/to/Obsidian/课程"
export TRANSCRIBE_SCRIPT="$(pwd)/scripts/video_transcribe.py"
export TRANSCRIBE_MODEL="google/gemini-3-flash-preview"
export REFINE_MODEL="google/gemini-3-flash-preview"
export ELRC_COOKIE_BROWSER="edge"
export ELRC_COOKIE_FILE="/tmp/elrc_cookies.txt"
```

包里已经包含 `scripts/video_transcribe.py`，只需要配置自己的 API key、最终课程仓库目录和浏览器 Cookie 来源。
爬取 ELRC 清单和下载 MP4 时脚本会绕过代理；模型 API 调用仍按你的系统网络环境或终端代理设置访问。

## 直接命令行使用

转写单个视频：

```bash
python3 scripts/video_transcribe.py "/path/or/url/to/video.mp4" \
  --api-key "$OPENROUTER_API_KEY" \
  --save-dir "$COURSE_VAULT_DIR/临时视频转写" \
  --save-images
```

如需额外生成精炼版，加 `--refine`：

```bash
python3 scripts/video_transcribe.py "/path/or/url/to/video.mp4" \
  --api-key "$OPENROUTER_API_KEY" \
  --save-dir "$COURSE_VAULT_DIR/临时视频转写" \
  --save-images \
  --refine
```

爬取一门 ELRC 课程：

```bash
python3 scripts/elrc_course_crawler.py "https://elrc.shanghaitech.edu.cn/learn/videoreview/..." \
  --course-vault-dir "$COURSE_VAULT_DIR" \
  --api-key "$OPENROUTER_API_KEY"
```

默认会只保留 `$COURSE_VAULT_DIR/<课程名>/` 下的完整转写 Markdown 和 `frames/`，下载视频和切段中间文件会在成功后清理。

如需每节课再生成 `*_精炼.md`，加 `--refine`：

```bash
python3 scripts/elrc_course_crawler.py "https://elrc.shanghaitech.edu.cn/learn/videoreview/..." \
  --course-vault-dir "$COURSE_VAULT_DIR" \
  --api-key "$OPENROUTER_API_KEY" \
  --refine
```

`--refine` 会把完整转写和 Markdown 中引用的关键帧图片再次发给模型整理，会额外消耗较多 token；不需要阅读版时建议不加。
精炼默认使用 `google/gemini-3-flash-preview`，可用 `--refine-model` 或 `REFINE_MODEL` 覆盖；转写也默认使用 `google/gemini-3-flash-preview`。

如果课程清单接口临时不可用，但已经保存过 `manifest.json`，可以恢复运行：

```bash
python3 scripts/elrc_course_crawler.py "https://elrc.shanghaitech.edu.cn/learn/videoreview/..." \
  --course-vault-dir "$COURSE_VAULT_DIR" \
  --api-key "$OPENROUTER_API_KEY" \
  --manifest-file ./manifest.json
```

如果当前网络能访问 ELRC 直链，但下载地址接口不稳定，可以加 `--prefer-direct`：

```bash
python3 scripts/elrc_course_crawler.py "https://elrc.shanghaitech.edu.cn/learn/videoreview/..." \
  --course-vault-dir "$COURSE_VAULT_DIR" \
  --api-key "$OPENROUTER_API_KEY" \
  --prefer-direct
```

如果 ELRC 接口返回 `401` 或 `403`，通常是浏览器 Cookie 过期或选错浏览器。先在浏览器登录 ELRC，再强制刷新 Cookie：

```bash
python3 scripts/elrc_course_crawler.py "https://elrc.shanghaitech.edu.cn/learn/videoreview/..." \
  --course-vault-dir "$COURSE_VAULT_DIR" \
  --api-key "$OPENROUTER_API_KEY" \
  --refresh-cookies
```

## 典型请求

```text
帮我爬这个 ELRC 课程录播链接，只要屏幕画面，转写后放到课程仓库，最后清理中间视频。
https://elrc.shanghaitech.edu.cn/learn/videoreview/...
```

## 注意

- 需要自己有 ELRC 访问权限，并在浏览器登录。
- 校园网资源通常不要走代理；如果开了梯子导致 ELRC 不通，脚本会尽量绕过代理，但系统 VPN/校园网访问仍要可用。
- 大视频下载会占用临时磁盘空间；完成后 `elrc-course-crawler` 会清理中间文件。
- 不要公开分享自己的 API key、Cookie 或课程内容。
