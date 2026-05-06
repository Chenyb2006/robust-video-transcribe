# Install

This package contains one core video transcription skill and one optional ShanghaiTech ELRC crawler skill.

## 1. Install Dependencies

```bash
python3 -m pip install -r requirements.txt
brew install ffmpeg yt-dlp
```

## 2. Install The Core Skill

Install `video-transcribe` first. This is the main skill and can be used without ELRC.

```bash
mkdir -p ~/.codex/skills
cp -R skills/video-transcribe ~/.codex/skills/
```

Recommended environment variables:

```bash
export OPENROUTER_API_KEY="your API key"
export TRANSCRIBE_SCRIPT="$(pwd)/scripts/video_transcribe.py"
export TRANSCRIBE_MODEL="google/gemini-3-flash-preview"
export REFINE_MODEL="google/gemini-3-flash-preview"
export TRANSCRIBE_SAVE_DIR="/path/to/notes"
```

Transcribe one video:

```bash
python3 scripts/video_transcribe.py "/path/or/url/to/video.mp4" \
  --api-key "$OPENROUTER_API_KEY" \
  --save-dir "$TRANSCRIBE_SAVE_DIR" \
  --save-images
```

Generate an additional refined reading version:

```bash
python3 scripts/video_transcribe.py "/path/or/url/to/video.mp4" \
  --api-key "$OPENROUTER_API_KEY" \
  --save-dir "$TRANSCRIBE_SAVE_DIR" \
  --save-images \
  --refine
```

`--refine` sends the full Markdown and referenced keyframes to the refine model, so it costs extra tokens. It is optional by design.

## 3. Optional: Install The ELRC Crawler

Install this only if you need ShanghaiTech ELRC course recording crawling.

```bash
cp -R skills/elrc-course-crawler ~/.codex/skills/
```

Additional environment variables:

```bash
export COURSE_VAULT_DIR="/path/to/Obsidian/课程"
export ELRC_COOKIE_BROWSER="edge"
export ELRC_COOKIE_FILE="/tmp/elrc_cookies.txt"
```

Crawl one ELRC course and transcribe all available screen-view recordings:

```bash
python3 scripts/elrc_course_crawler.py "https://elrc.shanghaitech.edu.cn/learn/videoreview/..." \
  --course-vault-dir "$COURSE_VAULT_DIR" \
  --api-key "$OPENROUTER_API_KEY"
```

Generate refined files too:

```bash
python3 scripts/elrc_course_crawler.py "https://elrc.shanghaitech.edu.cn/learn/videoreview/..." \
  --course-vault-dir "$COURSE_VAULT_DIR" \
  --api-key "$OPENROUTER_API_KEY" \
  --refine
```

ELRC network notes:

- ELRC course manifest and MP4 download requests bypass proxy by default.
- Model API calls keep using your normal terminal/system network environment.
- If ELRC returns `401` or `403`, log in through your browser and rerun with `--refresh-cookies`.
- If the manifest API is unavailable but you have `manifest.json`, use `--manifest-file`.
- If the download-address API is unstable, use `--prefer-direct`.

Example recovery commands:

```bash
python3 scripts/elrc_course_crawler.py "https://elrc.shanghaitech.edu.cn/learn/videoreview/..." \
  --course-vault-dir "$COURSE_VAULT_DIR" \
  --api-key "$OPENROUTER_API_KEY" \
  --manifest-file ./manifest.json \
  --prefer-direct
```

## 4. Restart Codex

Restart Codex or open a new session after copying skills.

## Privacy

Do not commit or share API keys, browser cookies, source videos, generated transcripts, or private course content.

