# Robust Video Transcribe

Robust video-to-Markdown transcription for agents and humans.

The core is a plain Python CLI, so any coding agent can run it directly without depending on Codex-specific skill support. It accepts a local video file or a URL supported by `yt-dlp`, extracts audio and keyframes, chunks long videos automatically, transcribes with a multimodal model, and optionally produces a refined reading version.

An optional ShanghaiTech ELRC crawler is included. It discovers course recordings, downloads the screen-view MP4, and then calls the same generic transcription CLI.

## What's Included

- `scripts/video_transcribe.py`: core video transcription CLI.
- `scripts/elrc_course_crawler.py`: optional ShanghaiTech ELRC crawler.
- `skills/video-transcribe`: optional Codex skill adapter for the core CLI.
- `skills/elrc-course-crawler`: optional Codex skill adapter for the ELRC crawler.
- `docs/video-transcribe.md`: core CLI details.
- `docs/elrc-course-crawler.md`: ELRC crawler details.

## Quick Start

```bash
python3 -m pip install -r requirements.txt
brew install ffmpeg yt-dlp
export OPENROUTER_API_KEY="your API key"

python3 scripts/video_transcribe.py "/path/or/url/to/video.mp4" \
  --api-key "$OPENROUTER_API_KEY" \
  --save-dir ./transcribe_output \
  --save-images
```

Add `--refine` to generate a cleaner reading version:

```bash
python3 scripts/video_transcribe.py "/path/or/url/to/video.mp4" \
  --api-key "$OPENROUTER_API_KEY" \
  --save-dir ./transcribe_output \
  --save-images \
  --refine
```

## Agent Usage

For any agent, the default instruction is simple:

1. Run `scripts/video_transcribe.py` for general video transcription.
2. Use `--refine` only when the user wants a polished reading version.
3. For long videos, rely on the built-in automatic chunking.
4. Do not read the full generated transcript into the agent context; inspect only filenames, structure, and the first few lines for preamble cleanup.
5. Keep API keys, cookies, videos, and transcripts out of git.

Codex users can optionally copy the adapters under `skills/` into `~/.codex/skills`, but this is not required for the CLI.

## Privacy

This repository does not include API keys, browser cookies, private paths, source videos, generated transcripts, or course content. Configure credentials and output folders locally.

