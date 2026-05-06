#!/usr/bin/env python3
"""
视频转写工具：下载视频 -> 提取音频+帧 -> 去重 -> 分段Gemini转写 -> 组装Markdown
"""

import argparse
import base64
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path

import numpy as np
import requests
from PIL import Image


# ─────────────────────────── 配置 ───────────────────────────

DEFAULT_FPS = 1
DEFAULT_MODEL = "google/gemini-3-flash-preview"
DEFAULT_REFINE_MODEL = "google/gemini-3-flash-preview"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
SIMILARITY_THRESHOLD = 3000  # MSE 阈值，越小越严格
THUMB_SIZE = (64, 64)
FRAME_JPEG_QUALITY = 60
REFINE_PROMPT = """请把下面的中文转写 Markdown 精炼成方便阅读的版本。
要求：不损失任何有效信息、概念、推理链和例子、观点等核心内容；去掉重复口水话；
删除开头任何模型寒暄或任务说明；只保留你认为关键的几张高信息密度关键帧在合适的位置，宁缺毋滥
输出 Markdown，不要解释。
"""

# 长视频自动分块：课程录播/PPT/屏幕讲解默认主动切段，降低模型截断风险
CHUNK_TRIGGER_MIN = 30
CHUNK_SIZE_MIN = 25


# ─────────────────────────── 工具函数 ───────────────────────────

def ytdlp_base_args(url):
    """Bilibili downloads are much more reliable with browser cookies."""
    if "bilibili.com" in url or "b23.tv" in url:
        return ["yt-dlp", "--cookies-from-browser", "edge"]
    return ["yt-dlp"]


def run_cmd(cmd, desc="", check=True):
    """执行 shell 命令"""
    print(f"  [CMD] {desc or ' '.join(cmd[:3])}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  [ERROR] {result.stderr[:500]}")
        raise RuntimeError(f"Command failed: {' '.join(cmd[:5])}...\n{result.stderr[:500]}")
    return result.stdout.strip()


def seconds_to_timestamp(sec):
    """秒数转 MM:SS"""
    m, s = divmod(int(sec), 60)
    return f"{m:02d}:{s:02d}"


def timestamp_to_seconds(ts):
    """MM:SS 或 HH:MM:SS 转秒数"""
    parts = ts.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


def image_to_base64(path, max_width=1280):
    """图片转 base64，缩小到 max_width"""
    img = Image.open(path)
    if img.width > max_width:
        ratio = max_width / img.width
        img = img.resize((max_width, int(img.height * ratio)), Image.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=FRAME_JPEG_QUALITY)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def file_to_base64(path):
    """文件转 base64"""
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def call_refine_model(content, api_key, model, timeout=600):
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
    }
    r = requests.post(
        OPENROUTER_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def strip_model_preamble(text):
    """只清理开头几行的模型寒暄，不全局删除真实口语内容。"""
    if not text:
        return text
    lines = text.splitlines()
    if not lines:
        return text
    patterns = [
        r"^\s*好的[，,。！!：:].{0,80}(转写|视频内容|内容|关键帧|专业术语).*$",
        r"^\s*以下是.{0,40}(转写|视频内容|整理后).*$",
        r"^\s*这是.{0,40}(转写|视频内容|整理后).*$",
        r"^\s*已根据.{0,40}(图片|关键帧|专业术语).*$",
        r"^\s*下面是.{0,40}(转写|视频内容|整理后).*$",
        r"^\s*(精炼后|整理后|处理后)的?\s*Markdown\s*(稿件|文稿|内容)?\s*(如下)?\s*[：:。.]?\s*$",
        r"^\s*Markdown\s*(稿件|文稿|内容)?\s*(如下)?\s*[：:。.]?\s*$",
    ]
    drop = 0
    for i, line in enumerate(lines[:10]):
        stripped = line.strip()
        if not stripped:
            if drop == i:
                drop = i + 1
            continue
        if any(re.search(p, stripped) for p in patterns):
            drop = i + 1
            continue
        break
    if drop:
        while drop < len(lines) and not lines[drop].strip():
            drop += 1
        return "\n".join(lines[drop:]).lstrip()
    return text


def resolve_markdown_image(md_path, image_path):
    image_path = image_path.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", image_path):
        return None
    candidate = Path(image_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    base = Path(md_path).parent
    candidates = [
        base / image_path,
        base.parent / image_path,
        Path.cwd() / image_path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def collect_markdown_images(md_path, text):
    images = []
    seen = set()
    for match in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", text):
        alt, rel = match.group(1), match.group(2)
        resolved = resolve_markdown_image(md_path, rel)
        if not resolved:
            continue
        key = str(resolved.resolve())
        if key in seen:
            continue
        seen.add(key)
        images.append({
            "alt": alt,
            "markdown": match.group(0),
            "rel": rel,
            "path": resolved,
        })
    return images


def refine_markdown(src, dst, api_key, model):
    text = Path(src).read_text(encoding="utf-8")
    if len(text) < 200:
        Path(dst).write_text(text, encoding="utf-8")
        return
    images = collect_markdown_images(src, text)
    content = [{"type": "text", "text": REFINE_PROMPT}]
    if images:
        content.append({
            "type": "text",
            "text": (
                "\n下面是原 Markdown 中出现的关键帧图片。"
                "每张图片前给出原始 Markdown 图片语法；如果保留该关键帧，"
                "请在精炼稿中使用同一条 Markdown 图片语法。\n"
            ),
        })
        for i, img in enumerate(images, 1):
            content.append({
                "type": "text",
                "text": f"\n关键帧 {i}: {img['markdown']}\n",
            })
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{image_to_base64(img['path'])}"
                },
            })
    content.append({"type": "text", "text": "\n下面是中文转写 Markdown：\n\n" + text})
    refined = call_refine_model(content, api_key, model)
    Path(dst).write_text(strip_model_preamble(refined) + "\n", encoding="utf-8")


# ─────────────────────────── 核心步骤 ───────────────────────────

def _try_separated_download(url, audio_path, video_path, output_dir, resolution=1080):
    """尝试分离下载音频+视频流（更快更小）"""
    try:
        # 音频
        if not os.path.exists(audio_path):
            raw_tmpl = os.path.join(output_dir, "audio_raw.%(ext)s")
            run_cmd(ytdlp_base_args(url) + [
                "-x", "--audio-format", "mp3",
                "-o", raw_tmpl, "--no-playlist", url,
            ], "download audio (separated)")
            raw = glob.glob(os.path.join(output_dir, "audio_raw.*"))
            if raw:
                run_cmd([
                    "ffmpeg", "-y", "-i", raw[0],
                    "-acodec", "libmp3lame", "-ab", "32k", "-ar", "16000", "-ac", "1",
                    audio_path
                ], "compress audio")
                os.remove(raw[0])
            else:
                return False

        # 视频流
        if not os.path.exists(video_path):
            for fmt in [f"bestvideo[height<={resolution}][ext=mp4]",
                        f"bestvideo[height<={resolution}]",
                        "bestvideo[ext=mp4]", "bestvideo"]:
                try:
                    run_cmd(ytdlp_base_args(url) + [
                        "-f", fmt, "-o", video_path, "--no-playlist", url,
                    ], f"download video ({fmt})")
                    break
                except RuntimeError:
                    continue
            if not os.path.exists(video_path):
                return False

        return True
    except RuntimeError:
        # 清理可能的残留
        for f in glob.glob(os.path.join(output_dir, "audio_raw.*")):
            os.remove(f)
        return False


def _fallback_download(url, audio_path, video_path, output_dir):
    """兜底：下载完整视频，本地用 ffmpeg 分离"""
    raw = os.path.join(output_dir, "raw_video.mp4")
    for attempt in range(3):
        try:
            run_cmd(ytdlp_base_args(url) + [
                "--merge-output-format", "mp4",
                "-o", raw, "--no-playlist", url,
            ], "download full video")
            break
        except RuntimeError:
            if attempt < 2:
                print(f"  [WARN] 下载失败，{3+attempt*2}秒后重试 ({attempt+2}/3)...")
                time.sleep(3 + attempt * 2)
            else:
                raise

    if not os.path.exists(audio_path):
        run_cmd([
            "ffmpeg", "-y", "-i", raw,
            "-vn", "-acodec", "libmp3lame", "-ab", "32k", "-ar", "16000", "-ac", "1",
            audio_path
        ], "extract audio")

    if not os.path.exists(video_path):
        os.rename(raw, video_path)
    elif os.path.exists(raw):
        os.remove(raw)


def download_media(url, output_dir, resolution=1080):
    """分别下载音频和视频流（不合并，更快更小）"""
    print(f"\n[1/6] 下载音频+视频流 ({resolution}p)...")
    audio_path = os.path.join(output_dir, "audio.mp3")
    video_path = os.path.join(output_dir, "video.mp4")

    # 获取视频标题
    title = None
    try:
        result = subprocess.run(
            ytdlp_base_args(url) + ["--get-title", "--no-playlist", url],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            title = result.stdout.strip()
            title = re.sub(r'[\\/:*?"<>|]', '_', title).strip()
            print(f"  标题: {title}")
    except Exception:
        pass

    # 策略：先尝试分离下载（更快更小），失败则兜底下完整视频再本地分离
    if not os.path.exists(audio_path) or not os.path.exists(video_path):
        separated = _try_separated_download(url, audio_path, video_path, output_dir, resolution)
        if not separated:
            print("  [INFO] 分离下载失败，改为下载完整视频后本地分离")
            _fallback_download(url, audio_path, video_path, output_dir)

    print(f"  -> 音频: {audio_path} ({os.path.getsize(audio_path)/1024/1024:.1f} MB)")
    print(f"  -> 视频: {video_path} ({os.path.getsize(video_path)/1024/1024:.1f} MB)")

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频流下载失败：{video_path}")

    print(f"  -> 视频流: {video_path} ({os.path.getsize(video_path)/1024/1024:.1f} MB)")
    return video_path, audio_path, title


def get_duration(media_path):
    """获取媒体时长（秒）"""
    out = run_cmd([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        media_path
    ], "get duration")
    return float(out)


def extract_frames(video_path, output_dir, fps=1):
    """提取帧，清理旧文件"""
    print(f"\n[3/6] 提取帧 (fps={fps})...")
    frames_dir = os.path.join(output_dir, "frames")
    # 清理旧帧
    if os.path.exists(frames_dir):
        shutil.rmtree(frames_dir)
    os.makedirs(frames_dir)

    run_cmd([
        "ffmpeg", "-y", "-i", video_path,
        "-vf", f"fps={fps}",
        "-q:v", "2",
        os.path.join(frames_dir, "frame_%05d.jpg")
    ], "extract frames")

    frames = []
    for f in sorted(os.listdir(frames_dir)):
        if not f.startswith("frame_") or not f.endswith(".jpg"):
            continue
        idx = int(f.replace("frame_", "").replace(".jpg", "")) - 1
        timestamp_sec = idx / fps
        frames.append({
            "path": os.path.join(frames_dir, f),
            "filename": f,
            "timestamp_sec": timestamp_sec,
            "timestamp": seconds_to_timestamp(timestamp_sec),
        })

    print(f"  -> 提取了 {len(frames)} 帧")
    return frames


def deduplicate_frames(frames, threshold=SIMILARITY_THRESHOLD):
    """去重 + 缓慢变化检测。

    逻辑：
    - 每帧和上一张保留帧比 MSE
    - MSE < 阈值：相似，跳过（但记住这帧，它可能是跳变前的最后一帧）
    - MSE >= 阈值：跳变！保留"跳变前最后一帧"（信息最满）+ 当前帧（新场景）
    """
    print(f"\n[4/6] 帧去重 (阈值={threshold})...")
    if not frames:
        return []

    def load_thumb(path):
        img = np.array(
            Image.open(path).resize(THUMB_SIZE).convert("L"),
            dtype=np.float32
        )
        crop_h = int(THUMB_SIZE[1] * 0.75)
        return img[:crop_h, :]

    STABLE_THRESHOLD = 100   # MSE < 100 = 画面几乎不动
    STABLE_SECONDS = 5       # 连续5秒稳定才认为进入稳定模式

    # 计算实际 fps（从帧间隔推算）
    if len(frames) >= 2:
        actual_fps = 1.0 / max(0.1, frames[1]["timestamp_sec"] - frames[0]["timestamp_sec"])
    else:
        actual_fps = 1.0
    stable_frames_needed = max(2, int(STABLE_SECONDS * actual_fps))
    unstable_frames_needed = 1  # 一帧不稳定就切回波动模式

    kept = [frames[0]]
    kept_thumb = load_thumb(frames[0]["path"])

    # 追踪状态
    prev_frame = frames[0]
    prev_thumb = kept_thumb
    stable_count = 0
    in_stable_mode = False
    unstable_count = 0
    last_was_jump = False  # 上一帧是否也是跳变（用于连续跳变合并）

    for frame in frames[1:]:
        curr_thumb = load_thumb(frame["path"])
        # MSE 和上一帧比（逐帧）
        mse_prev = float(np.mean((prev_thumb - curr_thumb) ** 2))
        # MSE 和上一个保留帧比（跳变检测）
        mse_kept = float(np.mean((kept_thumb - curr_thumb) ** 2))

        # 更新稳定计数
        if mse_prev < STABLE_THRESHOLD:
            stable_count += 1
            unstable_count = 0
        else:
            unstable_count += 1
            stable_count = 0

        # 模式切换（基于秒数，不受 fps 影响）
        if stable_count >= stable_frames_needed:
            in_stable_mode = True
        elif unstable_count >= unstable_frames_needed:
            in_stable_mode = False

        # 跳变检测（和上一个保留帧比）
        # 稳定模式用低阈值（PPT切页MSE低），波动模式用高阈值
        jump_threshold = 200 if in_stable_mode else threshold
        if mse_kept >= jump_threshold:
            if in_stable_mode:
                if last_was_jump:
                    # 连续跳变：替换上一帧，只保留最后一个（翻页后最终画面）
                    kept[-1] = frame
                else:
                    kept.append(frame)
            else:
                if last_was_jump:
                    kept[-1] = prev_frame
                else:
                    kept.append(prev_frame)

            kept_thumb = curr_thumb
            last_was_jump = True
        else:
            last_was_jump = False

        prev_frame = frame
        prev_thumb = curr_thumb

    # 最后一个场景：如果末帧和最后保留帧差异大，追加保留
    if len(frames) > 1:
        last_thumb = load_thumb(frames[-1]["path"])
        mse_final = float(np.mean((kept_thumb - last_thumb) ** 2))
        jump_threshold_final = 200 if in_stable_mode else threshold
        if mse_final >= jump_threshold_final:
            kept.append(frames[-1])

    print(f"  -> 去重后保留 {len(kept)}/{len(frames)} 帧")
    return kept


def filter_low_info_frames(frames, api_key):
    """用 flash-lite 剔除无信息量的帧（纯人物画面、片头片尾水印、纯色背景等）"""
    if not frames or len(frames) <= 2:
        return frames

    print(f"\n[4.5/7] 过滤低信息量帧 ({len(frames)} 张)...")

    content = [{"type": "text", "text": """你是一个视频关键帧筛选助手。我会给你一组视频关键帧截图（按时间顺序），请判断哪些图片值得保留，返回保留的图片编号。

【核心原则】
这些图片是视频帧，会插入到视频转写文档中供读者阅读。你的目标是：保留真正有信息量的图片，尽可能多的剔除重复信息与低密度信息，宁缺毋滥。

【必须保留】
- 包含图表、公式、PPT、总结等高密度视觉信息的帧
- 注意：视频中的字幕/弹幕不算有信息量（因为字幕内容会被音频转写覆盖），判断时请忽略字幕，只看画面本身的图像内容
【人物镜头处理——非常重要】
- 每个不同的人物只保留一张最清晰的单人特写镜头（目的是让读者知道谁在讲话即可）
- 同一个人的所有其他镜头（不同角度、不同表情、远景、合影中出现等）全部丢弃


【必须丢弃】
- 重复出现的人物（已经保留过特写的人不再保留任何镜头）
- 动画
- 与相邻图片大部分相同的重复帧（只保留最重要的）
- 教程类视频，操作演示帧尽可能精简，避免冗余，因为大部分操作信息在音频中已经包含
- 片头片尾 Logo / 水印 / 纯色过渡
- 模糊、拍糊的转场中间态

请只输出保留的图片编号，用逗号分隔。例如：1,2,4,5,7
不要输出任何其他文字。"""}]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/video-transcribe",
    }
    prompt_text = content[0]["text"]

    def _filter_batch(batch):
        """单批过滤，失败则对半切递归"""
        batch_content = [{"type": "text", "text": prompt_text}]
        for i, frame in enumerate(batch):
            batch_content.append({"type": "text", "text": f"\n图片 {i+1} [{frame['timestamp']}]:"})
            batch_content.append({"type": "image_url", "image_url": {
                "url": f"data:image/jpeg;base64,{image_to_base64(frame['path'])}"}})

        payload = {
            "model": "google/gemini-3-flash-preview",
            "messages": [{"role": "user", "content": batch_content}],
            "temperature": 0.1,
        }

        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=120)
            if resp.status_code == 200 and resp.text.strip():
                result = resp.json()["choices"][0]["message"]["content"].strip()
                keep_ids = set()
                for num in re.findall(r'\d+', result):
                    idx = int(num) - 1
                    if 0 <= idx < len(batch):
                        keep_ids.add(idx)
                if keep_ids:
                    kept = [batch[i] for i in sorted(keep_ids)]
                    print(f"    {len(batch)} → {len(kept)}")
                    return kept
        except Exception:
            pass

        # 失败了：对半切递归
        if len(batch) <= 3:
            print(f"    {len(batch)} 帧过滤失败，全部保留")
            return batch

        mid = len(batch) // 2
        print(f"    {len(batch)} 帧过滤失败，对半切 ({mid} + {len(batch)-mid})...")
        time.sleep(3)
        left = _filter_batch(batch[:mid])
        time.sleep(2)
        right = _filter_batch(batch[mid:])
        return left + right

    print(f"  单次发送 {len(frames)} 帧...")
    all_kept = _filter_batch(frames)

    dropped = len(frames) - len(all_kept)
    if dropped > 0:
        print(f"  -> 保留 {len(all_kept)}/{len(frames)} 帧，丢弃 {dropped} 张")
    else:
        print(f"  -> 全部保留（{len(all_kept)} 帧均有信息量）")
    return all_kept


def _estimate_tokens(audio_duration_sec, n_frames):
    """估算 input tokens：音频 32tok/s + 图片 258tok/张 + prompt ~500"""
    return int(audio_duration_sec * 32 + n_frames * 258 + 500)


def call_gemini_oneshot(unique_frames, audio_path, duration, api_key, model):
    """一次性发完整音频+所有帧，转写同时精选图片（方案B）"""
    ts_end = seconds_to_timestamp(duration)
    print(f"\n  一次性转写+选图 [00:00 - {ts_end}]，{len(unique_frames)} 帧...")

    duration_sec = int(duration)
    n = len(unique_frames)
    prompt = f"""你是一个专业的视频语音转写助手。

我会给你一段视频的完整音频，总时长 {ts_end}（{duration_sec}秒），以及 {n} 张关键帧截图（编号 IMG_01 到 IMG_{n:02d}，按视频时间顺序排列，IMG_01 最早出现，IMG_{n:02d} 最晚出现）。

【你的任务】
1. 仔细听音频，把所有说话内容完整转写出来，一个字都不要漏
2. 参考图片中的信息确保专业术语转写准确
3. 如果是多人谈话，请标注不同的说话人

【图片插入原则——极度克制！】
只从关键帧中精挑细选出最重要的、信息密度高的几张图片插入合适的位置，低密度内容（比如动画）可以全部丢弃，宁缺毋滥！宁缺毋滥！宁缺毋滥！
操作步骤的过程截图（每一步菜单点击）——不插，只在最终结果处插一张。
访谈/对话类视频：人物镜头只在每人首次出现时根据上下文插入在正确的位置即可，后续相同人物全部丢弃，作用是让读者知道人长什么样。

【输出格式】
请直接输出转写文本，在需要的位置插入 {{IMG_XX}}。
不需要时间戳，不需要回应我“好的，这是转写的内容“之类的话，请直接输出内容。
语言使用中文。

【重要】
- 逐字转写，不是总结
- 不用的图片直接跳过"""

    content = [{"type": "text", "text": prompt}]
    for i, frame in enumerate(unique_frames):
        content.append({"type": "text", "text": f"\nIMG_{i+1:02d}:"})
        content.append({"type": "image_url", "image_url": {
            "url": f"data:image/jpeg;base64,{image_to_base64(frame['path'])}"}})

    audio_b64 = file_to_base64(audio_path)
    content.append({"type": "text", "text": "\n以下是完整音频："})
    content.append({"type": "input_audio", "input_audio": {"data": audio_b64, "format": "mp3"}})

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/video-transcribe",
    }

    for attempt in range(3):
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=600)
            if resp.status_code == 200 and resp.text.strip():
                text = strip_model_preamble(resp.json()["choices"][0]["message"]["content"])
                if text and text.strip():
                    used = set(re.findall(r'\{IMG_(\d{2})\}', text))
                    print(f"  -> 转写长度: {len(text)} 字符，精选 {len(used)}/{n} 张图片")
                    return text
            print(f"  [WARN] 第{attempt+1}次尝试失败 (status={resp.status_code})，5秒后重试...")
        except Exception as e:
            print(f"  [WARN] 第{attempt+1}次尝试异常 ({e})，5秒后重试...")
        if attempt < 2:
            time.sleep(5)

    raise RuntimeError("一次性转写连续3次失败")


def call_gemini_chunk(chunk_audio_path, chunk_start_sec, chunk_end_sec,
                      chunk_frames_with_gidx, total_n_frames,
                      api_key, model, is_first):
    """转写单个音频块。chunk_frames_with_gidx 是 [(global_idx, frame_dict), ...]。
    返回的转写文本中 {IMG_XX} 使用全局编号（保持与 assemble_output 兼容）。"""
    chunk_dur = chunk_end_sec - chunk_start_sec
    ts_start = seconds_to_timestamp(chunk_start_sec)
    ts_end = seconds_to_timestamp(chunk_end_sec)
    n_local = len(chunk_frames_with_gidx)

    # 构造帧编号清单说明（用全局编号），让 Gemini 知道可以插哪些
    if chunk_frames_with_gidx:
        img_ids = [f"IMG_{gi+1:02d}" for gi, _ in chunk_frames_with_gidx]
        img_list = ", ".join(img_ids)
        frame_note = f"本块内有 {n_local} 张关键帧（编号 {img_list}，按时间顺序）"
    else:
        frame_note = "本块无关键帧"

    context = "" if is_first else (
        "\n\n注意：这是长音频的中间/后续部分（非整段开头），继续转写即可，"
        "不要输出任何开场白、总结、或\"好的\"之类的回应语。")

    prompt = f"""你是一个专业的视频语音转写助手。

我会给你一段视频音频的一部分（原始时间 {ts_start} 到 {ts_end}，时长 {seconds_to_timestamp(chunk_dur)}），以及此段内对应的关键帧。

{frame_note}

【你的任务】
1. 仔细听音频，把所有说话内容完整转写出来，一个字都不要漏
2. 参考图片中的信息确保专业术语转写准确
3. 如果是多人谈话，请标注不同的说话人

【图片插入原则——极度克制！】
只从本段关键帧中精挑细选最重要的几张插入合适的位置，低密度内容全部丢弃，宁缺毋滥。
访谈/对话类：人物镜头只在该人首次出现时插一张即可。

【输出格式】
直接输出转写文本，在需要的位置插入 {{IMG_XX}}（编号必须是上面给出的全局编号，不要自己重编）。
不需要时间戳，不要回应\"好的\"之类的话，直接输出内容。
语言使用中文。

【重要】
- 逐字转写，不是总结
- 不用的图片直接跳过{context}"""

    content = [{"type": "text", "text": prompt}]
    for global_idx, frame in chunk_frames_with_gidx:
        content.append({"type": "text", "text": f"\nIMG_{global_idx+1:02d}:"})
        content.append({"type": "image_url", "image_url": {
            "url": f"data:image/jpeg;base64,{image_to_base64(frame['path'])}"}})

    audio_b64 = file_to_base64(chunk_audio_path)
    content.append({"type": "text", "text": "\n以下是本段音频："})
    content.append({"type": "input_audio", "input_audio": {"data": audio_b64, "format": "mp3"}})

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.1,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/video-transcribe",
    }

    max_attempts = 6
    for attempt in range(max_attempts):
        try:
            resp = requests.post(OPENROUTER_URL, headers=headers, json=payload, timeout=1800)
            if resp.status_code == 200 and resp.text.strip():
                j = resp.json()
                if "choices" in j and j["choices"]:
                    text = strip_model_preamble(j["choices"][0]["message"]["content"])
                    if text and text.strip():
                        return text.strip()
                print(f"    [WARN] 第{attempt+1}次尝试无 choices: {resp.text[:200]}")
            else:
                print(f"    [WARN] 第{attempt+1}次尝试失败 (status={resp.status_code})，{resp.text[:200]}")
        except Exception as e:
            print(f"    [WARN] 第{attempt+1}次尝试异常 ({e})")
        if attempt < max_attempts - 1:
            wait = min(30 * (attempt + 1), 120)
            print(f"    等 {wait}s 重试...")
            time.sleep(wait)
    raise RuntimeError(f"块 [{ts_start}-{ts_end}] 转写连续 {max_attempts} 次失败")


def call_gemini_chunked(unique_frames, audio_path, duration, api_key, model):
    """长视频分块转写：ffmpeg 切音频 → 逐块调用 → 保留全局 {IMG_XX} 编号 → 拼接。
    带断点续传：每块结果存 part_NN.txt，失败重跑时已完成的块跳过。"""
    chunk_sec = CHUNK_SIZE_MIN * 60
    n_chunks = (int(duration) + chunk_sec - 1) // chunk_sec

    ts_total = seconds_to_timestamp(duration)
    print(f"\n  [长视频分块] 总时长 {ts_total}，切成 {n_chunks} 块 × {CHUNK_SIZE_MIN} 分钟")

    # 临时目录（放在音频旁边，重跑时可续传）
    work_dir = os.path.dirname(os.path.abspath(audio_path))
    tmp_dir = os.path.join(work_dir, "chunks")
    parts_dir = os.path.join(work_dir, "chunk_parts")
    os.makedirs(tmp_dir, exist_ok=True)
    os.makedirs(parts_dir, exist_ok=True)

    # 1. 切音频块
    chunks = []
    for i in range(n_chunks):
        start = i * chunk_sec
        end = min((i + 1) * chunk_sec, int(duration))
        chunk_path = os.path.join(tmp_dir, f"chunk_{i:02d}.mp3")
        if not os.path.exists(chunk_path) or os.path.getsize(chunk_path) < 1024:
            run_cmd([
                "ffmpeg", "-y", "-i", audio_path,
                "-ss", str(start), "-to", str(end),
                "-acodec", "copy", chunk_path
            ], f"split chunk {i+1}/{n_chunks}")
        chunks.append((chunk_path, start, end))
        print(f"    chunk {i+1}/{n_chunks}: {seconds_to_timestamp(start)}-{seconds_to_timestamp(end)}"
              f" ({os.path.getsize(chunk_path)/1024/1024:.1f}MB)")

    # 2. 逐块转写，断点续传
    results = []
    for i, (path, s, e) in enumerate(chunks):
        part_file = os.path.join(parts_dir, f"part_{i:02d}.txt")
        if os.path.exists(part_file) and os.path.getsize(part_file) > 100:
            with open(part_file, encoding="utf-8") as f:
                results.append(f.read())
            print(f"    块 {i+1}/{n_chunks} [{seconds_to_timestamp(s)}-{seconds_to_timestamp(e)}] 已存在，跳过")
            continue

        # 筛选本块的帧（保留全局索引）
        chunk_frames_with_gidx = [
            (gi, f) for gi, f in enumerate(unique_frames)
            if s <= f["timestamp_sec"] < e
        ]

        print(f"    块 {i+1}/{n_chunks} [{seconds_to_timestamp(s)}-{seconds_to_timestamp(e)}] "
              f"{os.path.getsize(path)/1024/1024:.1f}MB，{len(chunk_frames_with_gidx)} 帧...")
        t0 = time.time()
        text = call_gemini_chunk(path, s, e, chunk_frames_with_gidx,
                                 len(unique_frames), api_key, model, is_first=(i == 0))
        elapsed = time.time() - t0
        used_local = set(re.findall(r'\{IMG_(\d{2})\}', text))
        print(f"      -> {len(text)} 字符，精选 {len(used_local)} 张图（{elapsed:.0f}s）")

        with open(part_file, "w", encoding="utf-8") as f:
            f.write(text)
        results.append(text)

    # 3. 合并：块之间用空行分隔（不加分隔线，保持转写连续感）
    combined = "\n\n".join(results)

    # 4. 清理临时分块文件（part 文件保留，便于失败时续传——由主流程清理）
    shutil.rmtree(tmp_dir, ignore_errors=True)

    all_used = set(re.findall(r'\{IMG_(\d{2})\}', combined))
    print(f"\n  -> 分块转写完成：共 {len(combined)} 字符，精选 {len(all_used)}/{len(unique_frames)} 张图片")
    return combined


def transcribe(unique_frames, audio_path, duration, api_key, model):
    """转写入口：长视频自动走分块路径。"""
    est = _estimate_tokens(duration, len(unique_frames))
    print(f"\n[5/7] Gemini 转写 (模型: {model}, 预估 {est:,} tokens)...")

    if duration > CHUNK_TRIGGER_MIN * 60:
        print(f"  [INFO] 时长 {seconds_to_timestamp(duration)} 超过 {CHUNK_TRIGGER_MIN} 分钟，启用分块转写")
        return call_gemini_chunked(unique_frames, audio_path, duration, api_key, model)

    return call_gemini_oneshot(unique_frames, audio_path, duration, api_key, model)


def assemble_output(transcription, frames, title, output_dir, source_url=None):
    """组装 Markdown 文档。
    方案B模式：替换 {IMG_XX} 标记为实际图片，未引用的图片不保存。
    """
    print(f"\n[7/7] 组装输出文档...")

    # 把 [备注：xxx] 的方括号替换为圆括号，防止 Obsidian 解析为链接
    transcription = re.sub(r'\[([^\]]*备注[^\]]*)\]', r'（\1）', transcription)

    # 统计使用了哪些图片
    used_imgs = set(re.findall(r'\{IMG_(\d{2})\}', transcription))

    # 替换 {IMG_XX} 为 Markdown 图片
    def replace_img(match):
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(frames):
            f = frames[idx]
            rel_path = os.path.relpath(f["path"], output_dir)
            return f"\n![{f['timestamp']}]({rel_path})\n**`{f['timestamp']}`**\n"
        return match.group(0)

    result = re.sub(r'\{IMG_(\d{2})\}', replace_img, transcription)

    # 组装
    output_lines = []
    if source_url:
        output_lines.append(f"> 原视频：{source_url}\n")
    output_lines.append(result)

    output_path = os.path.join(output_dir, f"{title}_转写.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(output_lines))

    print(f"  -> 输出: {output_path}")
    print(f"  -> 使用 {len(used_imgs)}/{len(frames)} 张图片")
    return output_path, used_imgs


# ─────────────────────────── 主流程 ───────────────────────────

def main():
    global CHUNK_TRIGGER_MIN, CHUNK_SIZE_MIN
    t_start = time.time()
    parser = argparse.ArgumentParser(description="视频转写工具")
    parser.add_argument("input", help="视频 URL 或本地文件路径")
    parser.add_argument("--fps", type=float, default=DEFAULT_FPS,
                        help=f"帧提取率 (默认: {DEFAULT_FPS})")
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY"),
                        help="OpenRouter API Key，也可用 OPENROUTER_API_KEY 环境变量")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"模型 (默认: {DEFAULT_MODEL})")
    parser.add_argument("--refine-model", default=os.environ.get("REFINE_MODEL", DEFAULT_REFINE_MODEL),
                        help=f"精炼模型 (默认: {DEFAULT_REFINE_MODEL})")
    parser.add_argument("--output-dir", help="输出目录")
    parser.add_argument("--threshold", type=float, default=SIMILARITY_THRESHOLD,
                        help=f"帧去重阈值 (默认: {SIMILARITY_THRESHOLD})")
    parser.add_argument("--verbose-ts", action="store_true",
                        help="完整时间戳模式（默认精简：只在图片旁标时间戳）")
    parser.add_argument("--resolution", type=int, default=1080,
                        help="视频下载分辨率 (默认: 1080, 访谈类长视频建议 360 或 480)")
    parser.add_argument("--save-images", action="store_true",
                        help="保存带图片版本（默认只保存纯文本）")
    parser.add_argument("--save-dir",
                        default=os.environ.get("TRANSCRIBE_SAVE_DIR", "./transcribe_output"),
                        help="转写结果最终保存目录")
    parser.add_argument("--refine", action="store_true",
                        help="额外生成 *_精炼.md；会消耗更多模型 token")
    parser.add_argument("--chunk-trigger-min", type=int, default=CHUNK_TRIGGER_MIN,
                        help=f"超过多少分钟启用分块 (默认: {CHUNK_TRIGGER_MIN})")
    parser.add_argument("--chunk-size-min", type=int, default=CHUNK_SIZE_MIN,
                        help=f"每块多少分钟 (默认: {CHUNK_SIZE_MIN})")
    args = parser.parse_args()
    if not args.api_key:
        parser.error("需要 --api-key 或 OPENROUTER_API_KEY 环境变量")
    CHUNK_TRIGGER_MIN = args.chunk_trigger_min
    CHUNK_SIZE_MIN = args.chunk_size_min

    # 工作目录
    if args.output_dir:
        work_dir = args.output_dir
    else:
        work_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
    os.makedirs(work_dir, exist_ok=True)

    is_url = args.input.startswith("http://") or args.input.startswith("https://")

    source_url = args.input if is_url else None

    if is_url:
        video_path, audio_path, title = download_media(args.input, work_dir, args.resolution)
    else:
        video_path = args.input
        title = None
        if not os.path.exists(video_path):
            print(f"[ERROR] 文件不存在: {video_path}")
            sys.exit(1)
        audio_path = os.path.join(work_dir, "audio.mp3")
        if not os.path.exists(audio_path):
            run_cmd([
                "ffmpeg", "-y", "-i", video_path,
                "-vn", "-acodec", "libmp3lame",
                "-ab", "32k", "-ar", "16000", "-ac", "1",
                audio_path
            ], "extract audio from local file")

    duration = get_duration(audio_path)
    print(f"  时长: {seconds_to_timestamp(duration)} ({duration:.0f}秒)")

    # 提取帧
    fps = args.fps
    estimated_frames = int(duration * fps)
    # 如果估算帧数过多，提前降采样
    if estimated_frames > 800:
        fps = 800 / duration
        print(f"  [INFO] 帧数过多({estimated_frames})，降低采样率到 {fps:.2f} fps")

    frames = extract_frames(video_path, work_dir, fps)

    # 去重（含缓慢变化检测）
    unique_frames = deduplicate_frames(frames, args.threshold)

    # 用 flash-lite 过滤无信息量的帧
    unique_frames = filter_low_info_frames(unique_frames, args.api_key)

    # 转写
    transcription = transcribe(
        unique_frames, audio_path, duration,
        args.api_key, args.model
    )

    if not title:
        title = "视频转写"

    # 组装输出（先在工作目录生成）
    output_path, used_imgs = assemble_output(transcription, unique_frames, title, work_dir,
                                              source_url=source_url)

    # 将结果搬到最终保存目录
    save_dir = args.save_dir
    final_dir = os.path.join(save_dir, title)
    os.makedirs(final_dir, exist_ok=True)

    if args.save_images:
        # 带图片模式：只复制被引用的帧 + 带图 Markdown
        final_frames_dir = os.path.join(final_dir, "frames")
        if os.path.exists(final_frames_dir):
            shutil.rmtree(final_frames_dir)
        os.makedirs(final_frames_dir)
        for idx_str in used_imgs:
            idx = int(idx_str) - 1
            if 0 <= idx < len(unique_frames):
                shutil.copy2(unique_frames[idx]["path"], final_frames_dir)
        final_md = os.path.join(final_dir, f"{title}_转写.md")
        shutil.copy2(output_path, final_md)
    else:
        # 纯文本模式：去掉图片引用和时间戳标记，只保留文字
        with open(output_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        text_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("![") or stripped.startswith("**`"):
                continue
            text_lines.append(line)
        final_md = os.path.join(final_dir, f"{title}_转写.md")
        with open(final_md, "w", encoding="utf-8") as f:
            f.write("".join(text_lines))

    if args.refine:
        refined_md = os.path.join(final_dir, f"{title}_精炼.md")
        refine_markdown(final_md, refined_md, args.api_key, args.refine_model)

    # 清理工作目录中的临时文件
    print("\n[清理] 删除临时文件...")
    for f in ["video.mp4", "audio.mp3", "raw_video.mp4"]:
        p = os.path.join(work_dir, f)
        if os.path.exists(p):
            os.remove(p)
            print(f"  删除 {f}")
    for d in ["frames", "chunks", "chunk_parts"]:
        p = os.path.join(work_dir, d)
        if os.path.exists(p):
            shutil.rmtree(p)
            print(f"  删除 {d}/")

    elapsed = time.time() - t_start
    m, s = divmod(int(elapsed), 60)
    print(f"\n{'='*50}")
    print(f"转写完成！耗时 {m}分{s}秒")
    print(f"保存到: {final_dir}")
    print(f"  - {os.path.basename(final_md)}")
    if args.save_images:
        print(f"  - frames/ ({len(used_imgs)} 张精选帧)")
    else:
        print(f"  (纯文本模式，加 --save-images 可保存带图片版)")
    if args.refine:
        print(f"  - {title}_精炼.md")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
