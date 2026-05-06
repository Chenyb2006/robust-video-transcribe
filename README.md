# Video Transcribe Skills

Codex skills and scripts for turning videos into readable Markdown notes with selected keyframes.

The core project is `video-transcribe`: it accepts a video URL or local video file, extracts audio and keyframes, automatically chunks long videos, transcribes with a multimodal model, and optionally produces a refined reading version.

`elrc-course-crawler` is an optional companion skill for ShanghaiTech ELRC course recordings. It crawls course pages, downloads the screen-view recording, and then calls the same `video-transcribe` script.

## Included

- `skills/video-transcribe`: the main Codex skill for general video transcription.
- `scripts/video_transcribe.py`: the main transcription script.
- `skills/elrc-course-crawler`: optional ShanghaiTech ELRC crawler skill.
- `scripts/elrc_course_crawler.py`: optional ELRC crawler and batch runner.

## Documentation

- `INSTALL.md`: install and command-line usage.
- `docs/video-transcribe.md`: details for the core transcription skill.
- `docs/elrc-course-crawler.md`: details for the optional ELRC crawler.

## Privacy

This repository does not include API keys, browser cookies, private paths, source videos, generated transcripts, or course content. Configure credentials and output folders locally.

