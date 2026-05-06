# Core Video Transcription CLI

`scripts/video_transcribe.py` is the core of this repository. It is a normal Python command-line tool and can be used by any agent.

## Features

- Accepts local videos and URLs supported by `yt-dlp`.
- Extracts audio and representative frames.
- Deduplicates and filters low-information frames.
- Automatically chunks long videos before transcription.
- Generates a full transcript Markdown file.
- Optionally generates a refined Markdown file with fewer repetitions and selected high-density keyframes.

## Long Videos

Long videos are chunked automatically:

- Trigger: videos longer than 30 minutes.
- Default chunk size: 25 minutes.
- CLI flags: `--chunk-trigger-min`, `--chunk-size-min`.

This is the default path for lectures, screen recordings, tutorials, and long talks.

## Refine Mode

Refine mode is optional:

```bash
--refine
```

It sends the full Markdown and referenced keyframe images to the refine model. The prompt asks the model to remove repetition without losing effective information, concepts, reasoning chains, examples, or viewpoints.

Default models:

- Transcription: `google/gemini-3-flash-preview`
- Refinement: `google/gemini-3-flash-preview`

Override with:

```bash
--model <model>
--refine-model <model>
```

## Output

Each run creates a directory under `--save-dir` containing:

- `<title>_转写.md`
- `frames/` when `--save-images` is used
- `<title>_精炼.md` when `--refine` is used

The script only strips possible model greetings from the beginning of returned Markdown, and does not scan the whole transcript content.

## Recommended Agent Contract

Agents should treat generated transcripts as artifacts, not context. Inspect the output path, counts, and the first few lines only unless the user explicitly asks to analyze the transcript content.

