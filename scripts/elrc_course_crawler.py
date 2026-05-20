#!/usr/bin/env python3
"""ShanghaiTech ELRC course recording crawler.

Downloads the screen-view MP4 recordings for an ELRC course, transcribes them
with the bundled video_transcribe.py script, syncs final Markdown notes to a
course vault, and removes temporary videos/work files by default.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from http.cookiejar import MozillaCookieJar
from pathlib import Path

import requests


BASE_URL = "https://elrc.shanghaitech.edu.cn"
DEFAULT_MODEL = "google/gemini-3-flash-preview"
DEFAULT_REFINE_MODEL = "google/gemini-3-flash-preview"
DEFAULT_ROOM = "信息学院1B-106"


def without_proxy_env():
    env = os.environ.copy()
    for key in [
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
        "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    ]:
        env.pop(key, None)
    return env


def redacted_cmd(cmd):
    redacted = []
    hide_next = False
    for value in cmd:
        text = str(value)
        if hide_next:
            redacted.append("***")
            hide_next = False
            continue
        redacted.append(text)
        if text in {"--api-key", "--cookies", "--cookie-file"}:
            hide_next = True
    return redacted


def run(cmd, check=True, **kwargs):
    print("+", " ".join(redacted_cmd(cmd[:10])), flush=True)
    result = subprocess.run(cmd, text=True, **kwargs)
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(redacted_cmd(cmd))}")
    return result


def safe_name(value):
    value = re.sub(r'[\\/:*?"<>|]', "_", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:180]


def parse_course_url(url):
    parsed = urllib.parse.urlparse(url)
    course_id = parsed.path.rstrip("/").split("/")[-1]
    qs = urllib.parse.parse_qs(parsed.query)
    def one(name):
        vals = qs.get(name)
        if not vals:
            raise ValueError(f"course URL missing query parameter: {name}")
        return vals[0]
    return {
        "course_url": url,
        "course_id": course_id,
        "course_back_id": one("id"),
        "school_year": one("schoolYear"),
        "semester": one("semester"),
    }


def ensure_cookies(cookie_file, browser, refresh=False):
    cookie_file = Path(cookie_file)
    if refresh and cookie_file.exists():
        cookie_file.unlink()
    if cookie_file.exists() and cookie_file.stat().st_size > 1000:
        return
    run([
        "yt-dlp",
        "--cookies-from-browser", browser,
        "--cookies", str(cookie_file),
        "--skip-download",
        "--simulate",
        f"{BASE_URL}/learn/",
    ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=without_proxy_env())
    if not cookie_file.exists() or cookie_file.stat().st_size < 1000:
        raise RuntimeError(f"failed to export ELRC cookies from {browser}")


def build_session(cookie_file, referer):
    jar = MozillaCookieJar(str(cookie_file))
    jar.load(ignore_discard=True, ignore_expires=True)
    s = requests.Session()
    s.trust_env = False
    s.cookies = jar
    s.headers.update({
        "Referer": referer,
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json, text/plain, */*",
    })
    return s


def load_manifest(path, meta=None):
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if meta:
        manifest = {**meta, **manifest}
    if "courseName" not in manifest or "items" not in manifest:
        raise ValueError(f"invalid manifest file: {path}")
    manifest["count"] = len(manifest["items"])
    return manifest


def request_json_with_retry(method, s, url, *, attempts=10, delay=5, **kwargs):
    last_error = None
    for i in range(attempts):
        try:
            resp = s.request(method, url, timeout=60, **kwargs)
            if resp.status_code in (401, 403):
                raise PermissionError(
                    f"{method} {url} returned {resp.status_code}; "
                    "ELRC cookies may be expired or from the wrong browser"
                )
            resp.raise_for_status()
            return resp.json()
        except Exception as ex:
            last_error = ex
            print(f"{method} {url} failed attempt {i+1}/{attempts}: {ex!r}", flush=True)
            if isinstance(ex, PermissionError):
                break
            if i < attempts - 1:
                time.sleep(delay)
    raise RuntimeError(f"{method} {url} failed after {attempts} attempts: {last_error!r}")


def fetch_manifest(s, meta, work_root, fallback_file=None):
    if fallback_file:
        print(f"load manifest file: {fallback_file}", flush=True)
        return load_manifest(fallback_file, meta)

    url = f"{BASE_URL}/learn/v1/course/recording/video/info"
    params = {
        "courseId": meta["course_id"],
        "id": meta["course_back_id"],
        "schoolYear": meta["school_year"],
        "semester": meta["semester"],
    }
    try:
        data = request_json_with_retry("GET", s, url, params=params)
    except Exception:
        cached = work_root / "manifest.json"
        if cached.exists():
            print(f"manifest API failed, using cached manifest: {cached}", flush=True)
            return load_manifest(cached, meta)
        raise
    if data.get("status") != 200:
        raise RuntimeError(f"course manifest API failed: {data}")

    items = []
    for week in data["data"]["recordingVideoInfoShows"]:
        if not week.get("ifStart"):
            continue
        for section in week.get("recordInfoDetailList") or []:
            videos = section.get("videoInfoList") or []
            screen = [v for v in videos if "屏幕画面" in v.get("videoName", "")]
            if not screen:
                continue
            v = screen[0]
            title = (
                f"{int(week['week']):02d}_{section['weekDate']}_"
                f"{section['weekDay']}_{section['section']}节_屏幕画面"
            )
            items.append({
                "week": week["week"],
                "weekDate": section["weekDate"],
                "weekDay": section["weekDay"],
                "section": section["section"],
                "videoId": v["videoId"],
                "scheduleId": v.get("scheduleId"),
                "videoName": v.get("videoName"),
                "title": safe_name(title),
            })
    manifest = {
        "courseName": data["data"]["courseName"],
        **meta,
        "count": len(items),
        "items": items,
    }
    work_root.mkdir(parents=True, exist_ok=True)
    (work_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest


def get_download_url(s, video_id):
    url = f"{BASE_URL}/rman/v1/entity/download/fileinfo"
    data = request_json_with_retry("POST", s, url, json=[video_id])
    if not data.get("success") or not data.get("data"):
        raise RuntimeError(f"download API failed: {video_id} {data}")
    address = data["data"][0]["downloadAddress"][0]
    return address if address.startswith("http") else BASE_URL + address


def direct_url(item, room):
    y, mo, d = item["weekDate"].split("-")
    encoded_room = urllib.parse.quote(room)
    return (
        f"{BASE_URL}/bucket-z/unit-cwcc268v239qk92m/video/"
        f"{int(y)}/{int(mo)}/{int(d)}/{encoded_room}/{item['videoId']}.mp4"
    )


def download_video(item, url, cookie_file, work_root):
    video_dir = work_root / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)
    path = video_dir / f"{item['title']}_{item['videoId']}.mp4"
    if path.exists() and path.stat().st_size > 50_000_000:
        print(f"exists, skip download: {path.name}", flush=True)
        return path
    partial = path.with_suffix(".mp4.part")
    run([
        "curl", "-L", "--fail", "--retry", "8", "--retry-delay", "5",
        "--noproxy", "*", "-C", "-", "-b", str(cookie_file), "-o", str(partial), url,
    ], env=without_proxy_env())
    partial.rename(path)
    return path


def count_final(course_dir):
    if not course_dir.exists():
        return 0, 0
    refined = list(course_dir.glob("*/*_精炼.md"))
    full = list(course_dir.glob("*/*_转写.md"))
    return len(full), len(refined)


def final_exists(item, course_dir, need_refine):
    final_dir = course_dir / item["title"]
    final_md = final_dir / f"{item['title']}_转写.md"
    refined_md = final_dir / f"{item['title']}_精炼.md"
    return final_md.exists() and (not need_refine or refined_md.exists())


def transcribe_one(item, video_path, course_dir, work_root, args):
    final_dir = course_dir / item["title"]
    final_md = final_dir / f"{item['title']}_转写.md"
    refined_md = final_dir / f"{item['title']}_精炼.md"
    if final_exists(item, course_dir, args.refine):
        print(f"skip existing final: {item['title']}", flush=True)
        return

    tmp_save = work_root / "_transcribe_tmp"
    work_dir = work_root / "work" / item["title"]
    if tmp_save.exists():
        shutil.rmtree(tmp_save)
    tmp_save.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    title = item["title"]
    run([
        sys.executable, str(args.transcribe_script), str(video_path),
        "--api-key", args.api_key,
        "--model", args.model,
        "--refine-model", args.refine_model,
        "--output-dir", str(work_dir),
        "--save-dir", str(tmp_save),
        "--save-images",
        "--chunk-trigger-min", str(args.chunk_trigger_min),
        "--chunk-size-min", str(args.chunk_size_min),
    ] + (["--refine"] if args.refine else []))
    generated = tmp_save / "视频转写"
    if not generated.exists():
        raise RuntimeError(f"missing transcribe output directory: {generated}")
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if final_dir.exists():
        shutil.rmtree(final_dir)
    shutil.move(str(generated), str(final_dir))
    default_md = final_dir / "视频转写_转写.md"
    if default_md.exists():
        default_md.rename(final_md)
    else:
        mds = sorted(final_dir.glob("*_转写.md"))
        if mds and mds[0] != final_md:
            mds[0].rename(final_md)
    default_refined = final_dir / "视频转写_精炼.md"
    if default_refined.exists():
        default_refined.rename(refined_md)
    elif args.refine:
        refineds = sorted(final_dir.glob("*_精炼.md"))
        if refineds and refineds[0] != refined_md:
            refineds[0].rename(refined_md)
    print(f"final: {final_dir}", flush=True)


def cleanup(work_root, keep_manifest, course_dir):
    if keep_manifest and (work_root / "manifest.json").exists():
        shutil.copy2(work_root / "manifest.json", course_dir / "manifest.json")
    for name in ["videos", "segments", "segment_transcripts", "transcripts", "work", "_transcribe_tmp"]:
        p = work_root / name
        if p.exists():
            shutil.rmtree(p)
            print(f"removed {p}", flush=True)
    if not keep_manifest:
        p = work_root / "manifest.json"
        if p.exists():
            p.unlink()
    try:
        work_root.rmdir()
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description="Crawl ShanghaiTech ELRC course recordings")
    parser.add_argument("course_url", help="ELRC /learn/videoreview course URL")
    parser.add_argument("--course-vault-dir", default=os.environ.get("COURSE_VAULT_DIR"),
                        help="final course notes root, or COURSE_VAULT_DIR")
    parser.add_argument("--api-key", default=os.environ.get("OPENROUTER_API_KEY"),
                        help="OpenRouter API key, or OPENROUTER_API_KEY")
    parser.add_argument("--transcribe-script",
                        default=os.environ.get("TRANSCRIBE_SCRIPT") or str(Path(__file__).with_name("video_transcribe.py")),
                        help="path to video_transcribe.py")
    parser.add_argument("--model", default=os.environ.get("TRANSCRIBE_MODEL", DEFAULT_MODEL))
    parser.add_argument("--refine-model", default=os.environ.get("REFINE_MODEL", DEFAULT_REFINE_MODEL),
                        help=f"model for --refine (default: {DEFAULT_REFINE_MODEL})")
    parser.add_argument("--cookie-browser", default=os.environ.get("ELRC_COOKIE_BROWSER", "edge"))
    parser.add_argument("--cookie-file", default=os.environ.get("ELRC_COOKIE_FILE", "/tmp/elrc_cookies.txt"))
    parser.add_argument("--refresh-cookies", action="store_true",
                        help="delete cookie file and re-export cookies from the selected browser")
    parser.add_argument("--room", default=os.environ.get("ELRC_ROOM", DEFAULT_ROOM))
    parser.add_argument("--work-root", help="temporary work root")
    parser.add_argument("--keep-manifest", action="store_true", help="copy manifest.json to final course dir")
    parser.add_argument("--no-clean", action="store_true", help="do not remove temporary videos/work files")
    parser.add_argument("--refine", action="store_true", help="also generate *_精炼.md files; costs extra model tokens")
    parser.add_argument("--manifest-file", help="use an existing manifest.json instead of fetching the ELRC manifest API")
    parser.add_argument("--prefer-direct", action="store_true",
                        help="use ELRC direct MP4 URL first instead of the download-address API")
    parser.add_argument("--max-items", type=int, help="process only the first N manifest items; useful for testing")
    parser.add_argument("--chunk-trigger-min", type=int, default=30)
    parser.add_argument("--chunk-size-min", type=int, default=25)
    args = parser.parse_args()

    if not args.course_vault_dir:
        parser.error("需要 --course-vault-dir 或 COURSE_VAULT_DIR")
    if not args.api_key:
        parser.error("需要 --api-key 或 OPENROUTER_API_KEY")

    meta = parse_course_url(args.course_url)
    cookie_file = Path(args.cookie_file)
    ensure_cookies(cookie_file, args.cookie_browser, args.refresh_cookies)
    s = build_session(cookie_file, args.course_url)

    bootstrap_root = Path(args.work_root or "./elrc_work")
    manifest = fetch_manifest(s, meta, bootstrap_root, args.manifest_file)
    course_name = safe_name(manifest["courseName"])
    work_root = Path(args.work_root or f"./{course_name}_录播转写_work")
    if work_root != bootstrap_root:
        if (bootstrap_root / "manifest.json").exists():
            work_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(bootstrap_root / "manifest.json"), str(work_root / "manifest.json"))
        try:
            bootstrap_root.rmdir()
        except OSError:
            pass
    course_dir = Path(args.course_vault_dir) / course_name
    course_dir.mkdir(parents=True, exist_ok=True)

    print(f"course: {course_name}", flush=True)
    print(f"found {manifest['count']} screen recordings", flush=True)
    failed = []
    items = manifest["items"][:args.max_items] if args.max_items else manifest["items"]
    for idx, item in enumerate(items, 1):
        print(f"\n[{idx}/{manifest['count']}] {item['title']}", flush=True)
        try:
            if final_exists(item, course_dir, args.refine):
                print(f"skip existing final: {item['title']}", flush=True)
                continue

            if args.prefer_direct:
                url = direct_url(item, args.room)
            else:
                try:
                    url = get_download_url(s, item["videoId"])
                except Exception as ex:
                    print(f"download API failed, using direct URL: {ex}", flush=True)
                    url = direct_url(item, args.room)

            video = None
            for attempt in range(40):
                try:
                    video = download_video(item, url, cookie_file, work_root)
                    break
                except Exception as ex:
                    if attempt == 0 and not args.prefer_direct:
                        print(f"official download URL failed, switching to direct URL: {ex!r}", flush=True)
                        url = direct_url(item, args.room)
                        continue
                    print(f"download attempt {attempt+1} failed: {ex!r}", flush=True)
                    time.sleep(8)
            if video is None:
                raise RuntimeError("download failed after retries")
            transcribe_one(item, video, course_dir, work_root, args)
        except Exception as ex:
            failed.append((idx, item["title"], repr(ex)))
            print(f"FAILED {item['title']}: {ex!r}", flush=True)

    full, refined = count_final(course_dir)
    print(f"\nfinal full={full} refined={refined} dir={course_dir}", flush=True)
    print(f"FAILED: {failed}", flush=True)
    if not failed and not args.no_clean:
        cleanup(work_root, args.keep_manifest, course_dir)


if __name__ == "__main__":
    main()
