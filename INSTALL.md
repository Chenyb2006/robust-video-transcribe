# Install

Robust Video Transcribe is usable as a normal command-line toolkit. Agent-specific skill files are optional adapters.

## 1. Dependencies

Python packages:

```bash
python3 -m pip install -r requirements.txt
```

System tools:

```bash
brew install ffmpeg yt-dlp
```

Linux users can install `ffmpeg` through their package manager and `yt-dlp` through pip or the distribution package manager.

## 2. Configure

```bash
export OPENROUTER_API_KEY="your API key"
export TRANSCRIBE_MODEL="google/gemini-3-flash-preview"
export REFINE_MODEL="google/gemini-3-flash-preview"
export TRANSCRIBE_SAVE_DIR="/path/to/notes"
```

## 3. Core CLI Usage

Transcribe one video:

```bash
python3 scripts/video_transcribe.py "/path/or/url/to/video.mp4" \
  --api-key "$OPENROUTER_API_KEY" \
  --model "${TRANSCRIBE_MODEL:-google/gemini-3-flash-preview}" \
  --refine-model "${REFINE_MODEL:-google/gemini-3-flash-preview}" \
  --save-dir "${TRANSCRIBE_SAVE_DIR:-./transcribe_output}" \
  --save-images
```

Generate an additional refined reading version:

```bash
python3 scripts/video_transcribe.py "/path/or/url/to/video.mp4" \
  --api-key "$OPENROUTER_API_KEY" \
  --save-dir "${TRANSCRIBE_SAVE_DIR:-./transcribe_output}" \
  --save-images \
  --refine
```

Useful flags:

- `--chunk-trigger-min 30`: enable automatic chunking above this duration.
- `--chunk-size-min 25`: long-video chunk size.
- `--resolution 480`: lower URL video download resolution when visuals are not important.
- `--fps 1`: frame extraction rate before automatic downsampling.
- `--refine`: send Markdown and referenced keyframes to the refine model.

`--refine` is optional because it costs extra model tokens.

## 4. Optional ELRC Crawler

Use this only for ShanghaiTech ELRC course recordings.

```bash
export COURSE_VAULT_DIR="/path/to/Obsidian/课程"
export ELRC_COOKIE_BROWSER="edge"
export ELRC_COOKIE_FILE="/tmp/elrc_cookies.txt"

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

ELRC recovery flags:

- `--refresh-cookies`: force re-export browser cookies after login.
- `--manifest-file manifest.json`: resume from a saved course manifest.
- `--prefer-direct`: use the direct MP4 URL pattern before the download-address API.
- `--max-items N`: process only the first N recordings for testing.
- `--no-clean`: keep temporary videos and work files for debugging.

ELRC requests bypass proxy by default; model API calls keep using your normal terminal/system network route.

## 5. Optional Codex Skill Adapters

If you use Codex and want automatic skill triggering, copy the adapters:

```bash
mkdir -p ~/.codex/skills
cp -R skills/video-transcribe ~/.codex/skills/
cp -R skills/elrc-course-crawler ~/.codex/skills/
```

Restart Codex or open a new session after copying.

Other agents can ignore `skills/` and call the scripts directly.

## Privacy

Do not commit or share API keys, browser cookies, source videos, generated transcripts, or private course content.

