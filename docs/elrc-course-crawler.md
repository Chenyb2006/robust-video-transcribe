# elrc-course-crawler

Optional companion skill for ShanghaiTech ELRC course recordings.

This skill is not the core project. It exists to automate course recording discovery and downloading, then delegates transcription to `video-transcribe`.

## What It Does

- Parses ELRC `learn/videoreview` course URLs.
- Exports logged-in browser cookies through `yt-dlp`.
- Fetches the course recording manifest.
- Selects only the `屏幕画面` view.
- Downloads MP4 files with resumable `curl`.
- Calls `scripts/video_transcribe.py`.
- Saves final Markdown and frames into one folder per lesson.
- Cleans downloaded videos and temporary work files after success.

## Network Behavior

ELRC requests bypass proxy by default:

- Python `requests.Session.trust_env = False`
- `curl --noproxy '*'`
- cookie export subprocess receives proxy variables removed

This matters because ELRC is a campus resource, while the model API may still need the user's normal proxy or network route.

## Useful Flags

- `--refine`: generate refined Markdown files.
- `--max-items N`: process only the first N recordings, useful for testing.
- `--refresh-cookies`: force re-export browser cookies.
- `--manifest-file manifest.json`: resume from a saved manifest.
- `--prefer-direct`: skip the download-address API and use the known direct MP4 URL pattern first.
- `--no-clean`: keep temporary videos and work files for debugging.

## Output

For each lesson:

```text
<COURSE_VAULT_DIR>/<course name>/<lesson title>/
  <lesson title>_转写.md
  <lesson title>_精炼.md   # only with --refine
  frames/
```

