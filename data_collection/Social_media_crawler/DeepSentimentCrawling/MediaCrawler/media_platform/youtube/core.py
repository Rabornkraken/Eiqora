# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

from __future__ import annotations

import asyncio
import os
import tempfile
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import httpx

import config
from base.base_crawler import AbstractCrawler
from store import youtube as youtube_store
from tools import utils
from tools.transcriber import VideoTranscriber
from tools.youtube_transcript import extract_youtube_video_id
from var import crawler_type_var, source_keyword_var

try:
    from yt_dlp import YoutubeDL
except Exception:  # pragma: no cover
    YoutubeDL = None  # type: ignore[assignment]


class _YtDlpLogger:
    def debug(self, msg: str) -> None:
        if msg:
            utils.logger.debug(msg)

    def warning(self, msg: str) -> None:
        if msg:
            utils.logger.warning(msg)

    def error(self, msg: str) -> None:
        if msg:
            lowered = msg.lower()
            if "members-only" in lowered or "join this channel" in lowered or "members on level" in lowered:
                utils.logger.warning(msg)
                return
            utils.logger.error(msg)


class _CapturingYtDlpLogger:
    """
    yt-dlp logger that captures messages without emitting to our app logger.
    Used for probe extract calls where we decide whether to skip a video (e.g. members-only).
    """

    def __init__(self) -> None:
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def debug(self, msg: str) -> None:  # pragma: no cover
        return

    def warning(self, msg: str) -> None:
        if msg:
            self.warnings.append(msg)

    def error(self, msg: str) -> None:
        if msg:
            self.errors.append(msg)


def _looks_like_members_only(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        "members-only" in lowered
        or "join this channel" in lowered
        or "members on level" in lowered
        or "channel's members" in lowered
    )


def _extract_video_id_from_entry(entry: Dict) -> str:
    vid = str(entry.get("id") or "").strip()
    if vid:
        return vid
    url = str(entry.get("url") or "").strip()
    if url and len(url) == 11 and "://" not in url:
        return url
    return extract_youtube_video_id(entry.get("webpage_url") or entry.get("url") or "") or ""


def _normalize_creator_url(value: str) -> str:
    """
    Normalize a creator/channel URL to a tab that reliably lists uploads.
    For handle/channel homepages, yt-dlp may return only the channel id; /videos works consistently.
    """
    raw = (value or "").strip()
    if not raw:
        return raw

    lowered = raw.lower()
    if "youtube.com" not in lowered:
        return raw
    if "/watch" in lowered or "watch?v=" in lowered:
        return raw

    parts = urlsplit(raw)
    path = (parts.path or "").rstrip("/")
    for suffix in ("/videos", "/shorts", "/streams", "/live", "/featured"):
        if path.lower().endswith(suffix):
            return raw
    path = f"{path}/videos"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _normalize_ytdlp_remote_components(value) -> List[str]:
    if not value:
        return []
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    try:
        items = [str(x).strip() for x in value]  # type: ignore[arg-type]
        return [x for x in items if x]
    except Exception:
        v = str(value).strip()
        return [v] if v else []


class YouTubeCrawler(AbstractCrawler):
    """
    YouTube crawler (minimal):
    - search: yt-dlp 的 ytsearch 抓取视频列表 + 字幕
    - detail: 根据配置 YT_SPECIFIED_ID_LIST 抓取字幕
    """

    async def start(self) -> None:
        crawler_type_var.set(config.CRAWLER_TYPE)

        if config.CRAWLER_TYPE == "search":
            await self.search()
        elif config.CRAWLER_TYPE == "detail":
            await self.get_specified_videos()
        elif config.CRAWLER_TYPE == "creator":
            await self.get_creator_videos()
        else:
            raise ValueError(f"Unsupported crawler type: {config.CRAWLER_TYPE}")

        utils.logger.info("[YouTubeCrawler.start] YouTube crawler finished ...")

    async def search(self) -> None:
        if YoutubeDL is None:  # pragma: no cover
            raise RuntimeError("yt-dlp not installed. Please add it to requirements and install.")

        max_count = int(config.CRAWLER_MAX_NOTES_COUNT or 10)
        for keyword in (config.KEYWORDS or "").split(","):
            keyword = keyword.strip()
            if not keyword:
                continue
            source_keyword_var.set(keyword)
            query = f"ytsearch{max_count}:{keyword}"
            utils.logger.info(f"[YouTubeCrawler.search] searching: {query}")

            info = await asyncio.to_thread(self._ytdlp_extract, query, download=False)
            entries = (info or {}).get("entries") or []
            for entry in entries:
                await self._handle_video_entry(entry)

    async def get_specified_videos(self) -> None:
        if YoutubeDL is None:  # pragma: no cover
            raise RuntimeError("yt-dlp not installed. Please add it to requirements and install.")

        ids = getattr(config, "YT_SPECIFIED_ID_LIST", []) or []
        for raw in ids:
            video_id = extract_youtube_video_id(str(raw))
            if not video_id:
                continue
            entry = await asyncio.to_thread(self._ytdlp_extract, f"https://www.youtube.com/watch?v={video_id}", download=False)
            await self._handle_video_entry(entry or {})

    async def get_creator_videos(self) -> None:
        if YoutubeDL is None:
            raise RuntimeError("yt-dlp not installed.")

        max_count = int(config.CRAWLER_MAX_NOTES_COUNT or 5)
        # 优先使用 YT_CREATOR_ID_LIST，如果为空则回退到 KEYWORDS
        creators = getattr(config, "YT_CREATOR_ID_LIST", [])
        if not creators:
            creators = (config.KEYWORDS or "").split(",")
            
        skip_members_only = bool(getattr(config, "YOUTUBE_SKIP_MEMBERS_ONLY", True))
        creator_fetch_limit = int(getattr(config, "YOUTUBE_CREATOR_FETCH_LIMIT", 200) or 200)
        
        for creator in creators:
            creator = creator.strip()
            if not creator:
                continue
            
            source_keyword_var.set(creator)
            creator_url = _normalize_creator_url(creator)
            utils.logger.info(f"[YouTubeCrawler.get_creator_videos] crawling: {creator_url}")
            
            # Fetch a larger batch to account for potential skips (e.g. members-only videos)
            fetch_limit = max(max_count, creator_fetch_limit)
            
            info = await asyncio.to_thread(
                self._ytdlp_extract, 
                creator_url,
                download=False, 
                noplaylist=False,
                extract_flat="in_playlist",
                playlistend=fetch_limit,
            )
            
            entries = (info or {}).get("entries") or []
            
            # Duplicate Detection
            candidate_ids = []
            valid_entries = []
            for entry in entries:
                if not entry: continue
                vid = _extract_video_id_from_entry(entry)
                if not vid or vid.startswith("UC") or entry.get("_type") == "playlist":
                    continue
                candidate_ids.append(vid)
                valid_entries.append(entry)
            
            existing_ids = set(await youtube_store.get_existing_video_ids(candidate_ids))
            utils.logger.info(f"[YouTubeCrawler] Found {len(valid_entries)} entries. {len(existing_ids)} exist in DB.")
            
            processed_count = 0
            for entry in valid_entries:
                vid = _extract_video_id_from_entry(entry)
                if vid in existing_ids:
                    utils.logger.info(f"[YouTubeCrawler] Encountered existing video: {vid}. Stopping for this creator.")
                    break

                if skip_members_only:
                    capture_logger = _CapturingYtDlpLogger()
                    try:
                        # Fetch per-video info to detect members-only/private videos and avoid storing them.
                        full = await asyncio.to_thread(
                            self._ytdlp_extract,
                            f"https://www.youtube.com/watch?v={vid}",
                            download=False,
                            ignoreerrors=False,
                            logger=capture_logger,
                        )
                    except Exception as e:
                        joined = "\n".join(capture_logger.errors + capture_logger.warnings + [str(e)])
                        if _looks_like_members_only(joined):
                            utils.logger.info(f"[YouTubeCrawler.get_creator_videos] skip members-only: {vid}")
                            continue
                        utils.logger.warning(f"[YouTubeCrawler.get_creator_videos] skip unavailable video: {vid} ({e})")
                        continue

                    if not full:
                        joined = "\n".join(capture_logger.errors + capture_logger.warnings)
                        if _looks_like_members_only(joined):
                            utils.logger.info(f"[YouTubeCrawler.get_creator_videos] skip members-only: {vid}")
                        continue
                    if self._is_members_only_video(full):
                        utils.logger.info(f"[YouTubeCrawler.get_creator_videos] skip members-only: {vid}")
                        continue

                    await self._handle_video_entry(full)
                    processed_count += 1
                else:
                    await self._handle_video_entry(entry)
                    processed_count += 1
                
                if processed_count >= max_count:
                    break

            if processed_count < max_count:
                utils.logger.info(
                    f"[YouTubeCrawler.get_creator_videos] only got {processed_count}/{max_count} accessible videos "
                    f"(playlist scanned up to {fetch_limit})"
                )

    def _ytdlp_extract(self, url_or_search: str, download: bool, **kwargs) -> Dict:
        proxy = getattr(config, "YOUTUBE_PROXY", "") or None
        cookies_browser = getattr(config, "YOUTUBE_COOKIES_FROM_BROWSER", None)
        sub_langs = getattr(config, "YOUTUBE_TRANSCRIPT_LANGS", "zh,en").split(",")
        remote_components = _normalize_ytdlp_remote_components(getattr(config, "YOUTUBE_REMOTE_COMPONENTS", []))
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": not download,
            "proxy": proxy,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": sub_langs,
            "nocheckcertificate": True,
            "ignoreerrors": True,
            "logger": _YtDlpLogger(),
        }
        if remote_components:
            ydl_opts["remote_components"] = remote_components
        if cookies_browser:
            ydl_opts["cookiesfrombrowser"] = (cookies_browser,)
        
        # Merge extra options (like playlistend)
        ydl_opts.update(kwargs)

        with YoutubeDL(ydl_opts) as ydl:  # type: ignore[misc]
            return ydl.extract_info(url_or_search, download=download) or {}

    @staticmethod
    def _is_members_only_video(info: Dict) -> bool:
        availability = str(info.get("availability") or "").strip().lower()
        if availability in {"subscriber_only", "members_only", "premium_only", "needs_premium"}:
            return True
        if "member" in availability or "subscriber" in availability:
            return True
        return False

    async def _handle_video_entry(self, entry: Dict) -> None:
        video_id = entry.get("id") or extract_youtube_video_id(entry.get("webpage_url") or "")
        if not video_id:
            return

        title = entry.get("title") or ""
        description = entry.get("description") or ""
        channel = entry.get("uploader") or entry.get("channel") or ""
        channel_id = entry.get("channel_id") or entry.get("uploader_id") or ""
        publish_time = entry.get("upload_date") or ""
        duration = entry.get("duration") or 0
        view_count = entry.get("view_count") or 0
        like_count = entry.get("like_count") or 0
        comment_count = entry.get("comment_count") or 0
        url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
        thumbnail = entry.get("thumbnail") or ""

        transcription = ""
        if getattr(config, "YOUTUBE_ENABLE_TRANSCRIPT", True):
            transcription = await self._fetch_and_parse_transcript(entry)

        if not transcription and getattr(config, "YOUTUBE_ENABLE_AUDIO_FALLBACK", False):
            transcription = await self._fallback_audio_transcribe(url)

        await youtube_store.upsert_youtube_video(
            {
                "video_id": video_id,
                "title": title,
                "description": description,
                "channel": channel,
                "channel_id": channel_id,
                "publish_time": str(publish_time),
                "duration": int(duration or 0),
                "view_count": int(view_count or 0),
                "like_count": int(like_count or 0),
                "comment_count": int(comment_count or 0),
                "url": url,
                "thumbnail": thumbnail,
                "transcription": transcription,
                "source_keyword": source_keyword_var.get(),
                "last_modify_ts": utils.get_current_timestamp(),
            }
        )

    async def _fetch_and_parse_transcript(self, entry: Dict) -> str:
        """
        Fetch transcript from yt-dlp extracted entry (subtitles/automatic_captions).
        Prefers manual subtitles over automatic ones.
        Prefers languages in config YOUTUBE_TRANSCRIPT_LANGS.
        """
        preferred_langs = getattr(config, "YOUTUBE_TRANSCRIPT_LANGS", "zh,en").split(",")
        # Prioritize manual subtitles, then automatic
        sources = [entry.get("subtitles"), entry.get("automatic_captions")]
        
        target_url = None
        
        for subs in sources:
            if not subs:
                continue
            # Check for preferred languages
            for lang in preferred_langs:
                lang = lang.strip()
                if not lang:
                    continue
                    
                # Find matching language keys (e.g. 'en' matches 'en', 'en-orig', 'en-US' etc if we are lenient, 
                # but yt-dlp keys are usually specific. Let's try exact or prefix match)
                for l_key in subs.keys():
                    if l_key == lang or l_key.startswith(lang + "-"):
                        # Found language, check formats
                        formats = subs[l_key]
                        # Prefer json3 for easier parsing
                        for fmt in formats:
                            if fmt.get("ext") == "json3":
                                target_url = fmt.get("url")
                                break
                        if target_url:
                            break
                if target_url:
                    break
            if target_url:
                break
                
        if not target_url:
            return ""

        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.get(target_url, timeout=10)
                if resp.status_code != 200:
                    utils.logger.warning(f"[YouTubeCrawler] Failed to fetch transcript url: {resp.status_code}")
                    return ""
                
                data = resp.json()
                # Parse json3 format
                # Structure: { events: [ { segs: [ { utf8: "text" }, ... ] }, ... ] }
                events = data.get("events", [])
                text_parts = []
                for event in events:
                    segs = event.get("segs", [])
                    for seg in segs:
                        t = seg.get("utf8", "").strip()
                        if t and t != "\n":
                            text_parts.append(t)
                return " ".join(text_parts)
        except Exception as e:
            utils.logger.warning(f"[YouTubeCrawler] Error parsing transcript: {e}")
            return ""

    async def _fallback_audio_transcribe(self, url: str) -> str:
        if YoutubeDL is None:  # pragma: no cover
            return ""

        proxy = getattr(config, "YOUTUBE_PROXY", "") or None
        cookies_browser = getattr(config, "YOUTUBE_COOKIES_FROM_BROWSER", None)
        audio_format = getattr(config, "YOUTUBE_AUDIO_FORMAT", "bestaudio[ext=m4a]/bestaudio") or "bestaudio[ext=m4a]/bestaudio"
        postprocess_codec = getattr(config, "YOUTUBE_AUDIO_POSTPROCESS_CODEC", "") or ""
        remote_components = _normalize_ytdlp_remote_components(getattr(config, "YOUTUBE_REMOTE_COMPONENTS", []))

        # Guard against accidental invalid selectors (e.g. a trailing "\" copied from shell line-continuations),
        # which will cause "Requested format is not available" even when formats exist.
        audio_format = str(audio_format).strip()
        while audio_format.endswith("\\"):
            audio_format = audio_format[:-1].rstrip()
        audio_format = " ".join(audio_format.split())
        postprocess_codec = str(postprocess_codec).strip()

        # Use a persistent cache directory
        cache_dir = os.path.join(os.getcwd(), "data", "youtube", "cache")
        os.makedirs(cache_dir, exist_ok=True)
        
        outtmpl = os.path.join(cache_dir, "%(id)s.%(ext)s")
        
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "proxy": proxy,
            "format": audio_format,
            "outtmpl": outtmpl,
            "nocheckcertificate": True,
            "concurrent_fragment_downloads": 4,
            "socket_timeout": 30,
            "retries": 10,
            "fragment_retries": 10,
        }
        if remote_components:
            ydl_opts["remote_components"] = remote_components
        if postprocess_codec:
            ydl_opts["postprocessors"] = [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": postprocess_codec,
                }
            ]
        if cookies_browser:
            ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

        try:
            audio_ids = await asyncio.to_thread(
                self._ytdlp_list_preferred_audio_format_ids, url, proxy, cookies_browser, audio_format
            )
            if not audio_ids:
                utils.logger.warning(
                    "[YouTubeCrawler] No audio-only formats found for this video under current network/cookies; "
                    "it may be region-restricted, require sign-in, or blocked by the proxy/IP."
                )
                return ""

            # yt-dlp may fail if the configured selector is too strict for some videos.
            # Retry with safer fallbacks to avoid hard failures like:
            # "Requested format is not available".
            format_candidates = []
            for fmt in [*audio_ids, audio_format, "bestaudio/best", "bestaudio", "best"]:
                fmt = (fmt or "").strip()
                if fmt and fmt not in format_candidates:
                    format_candidates.append(fmt)

            last_err: Optional[Exception] = None
            info: Dict = {}
            for fmt in format_candidates:
                try:
                    ydl_opts_try = dict(ydl_opts)
                    ydl_opts_try["format"] = fmt
                    info = await asyncio.to_thread(self._ytdlp_download_audio, url, ydl_opts_try)
                    last_err = None
                    break
                except Exception as e:
                    last_err = e
                    msg = str(e)
                    if "Requested format is not available" in msg:
                        utils.logger.warning(f"[YouTubeCrawler] Audio format not available: {fmt} (trying next fallback)")
                        continue
                    raise

            if last_err is not None:
                raise last_err

            audio_path = info.get("_filename") or info.get("requested_downloads", [{}])[0].get("filepath") or ""
            if postprocess_codec and audio_path:
                candidate = os.path.splitext(audio_path)[0] + f".{postprocess_codec}"
                if os.path.exists(candidate):
                    audio_path = candidate
            
            if not audio_path or not os.path.exists(audio_path):
                return ""
                
            loop = asyncio.get_running_loop()
            transcription = await loop.run_in_executor(None, VideoTranscriber.transcribe_video, audio_path)
            
            # Clean up the audio file after successful transcription
            try:
                os.remove(audio_path)
            except Exception as e:
                utils.logger.warning(f"[YouTubeCrawler] Failed to remove cached file {audio_path}: {e}")
                
            return transcription
            
        except Exception as e:
            utils.logger.error(f"[YouTubeCrawler] Audio fallback failed: {e}")
            return ""

    def _ytdlp_pick_best_audio_format_id(
        self,
        url: str,
        proxy: Optional[str],
        cookies_browser: Optional[str],
        audio_format_hint: str,
    ) -> Tuple[str, str]:
        """
        Preflight extract to pick an audio-only format_id that is actually available under the current
        network/proxy/cookies, to avoid brittle format selectors.
        """
        if YoutubeDL is None:  # pragma: no cover
            return "", ""

        hint = (audio_format_hint or "").lower()
        prefer_opus = "opus" in hint
        prefer_m4a = "m4a" in hint

        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "proxy": proxy,
            "nocheckcertificate": True,
            "skip_download": True,
            "logger": _YtDlpLogger(),
        }
        if cookies_browser:
            ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

        with YoutubeDL(ydl_opts) as ydl:  # type: ignore[misc]
            info = ydl.extract_info(url, download=False) or {}

        formats = info.get("formats") or []
        audio_only = []
        for f in formats:
            if not isinstance(f, dict):
                continue
            if f.get("vcodec") != "none":
                continue
            acodec = f.get("acodec") or ""
            if not acodec or acodec == "none":
                continue
            audio_only.append(f)

        if not audio_only:
            return "", ""

        def score(f: Dict) -> Tuple[int, float]:
            ext = (f.get("ext") or "").lower()
            acodec = (f.get("acodec") or "").lower()
            abr = float(f.get("abr") or 0.0)
            codec_bonus = 0
            if prefer_opus:
                codec_bonus = 2 if ("opus" in acodec or ext == "webm") else 0
            elif prefer_m4a:
                codec_bonus = 2 if ext == "m4a" else 0
            return (codec_bonus, abr)

        best = max(audio_only, key=score)
        fmt_id = str(best.get("format_id") or "").strip()
        desc = f"{best.get('ext','')}/{best.get('acodec','')} abr={best.get('abr','')}"
        return fmt_id, desc

    def _ytdlp_list_preferred_audio_format_ids(
        self,
        url: str,
        proxy: Optional[str],
        cookies_browser: Optional[str],
        audio_format_hint: str,
    ) -> List[str]:
        """
        Return audio-only format ids available under current proxy/cookies, ordered by preference.
        Preference is inferred from `audio_format_hint` (e.g. prefer opus/webm or m4a), and prefers
        non-DRC variants when both exist (e.g. 139 over 139-drc).
        """
        if YoutubeDL is None:  # pragma: no cover
            return []

        hint = (audio_format_hint or "").lower()
        prefer_opus = "opus" in hint
        prefer_m4a = "m4a" in hint
        remote_components = _normalize_ytdlp_remote_components(getattr(config, "YOUTUBE_REMOTE_COMPONENTS", []))

        def extract_with_remote_components(rc: List[str]) -> Dict:
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "proxy": proxy,
                "nocheckcertificate": True,
                "skip_download": True,
                "logger": _YtDlpLogger(),
            }
            if rc:
                ydl_opts["remote_components"] = rc
            if cookies_browser:
                ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

            with YoutubeDL(ydl_opts) as ydl:  # type: ignore[misc]
                return ydl.extract_info(url, download=False) or {}

        info = extract_with_remote_components(remote_components)

        formats = info.get("formats") or []
        audio_only = []
        for f in formats:
            if not isinstance(f, dict):
                continue
            if f.get("vcodec") != "none":
                continue
            acodec = f.get("acodec") or ""
            if not acodec or acodec == "none":
                continue
            audio_only.append(f)

        if not audio_only and not remote_components:
            # Auto-retry with recommended EJS remote components when JS challenge blocks formats.
            info = extract_with_remote_components(["ejs:github"])
            formats = info.get("formats") or []
            for f in formats:
                if not isinstance(f, dict):
                    continue
                if f.get("vcodec") != "none":
                    continue
                acodec = f.get("acodec") or ""
                if not acodec or acodec == "none":
                    continue
                audio_only.append(f)

        if not audio_only:
            return []

        def score(f: Dict) -> Tuple[int, int, float]:
            ext = (f.get("ext") or "").lower()
            acodec = (f.get("acodec") or "").lower()
            abr = float(f.get("abr") or 0.0)
            fmt_id = str(f.get("format_id") or "").lower()
            fmt_note = str(f.get("format_note") or "").lower()

            codec_bonus = 0
            if prefer_opus:
                codec_bonus = 2 if ("opus" in acodec or ext == "webm") else 0
            elif prefer_m4a:
                codec_bonus = 2 if ext == "m4a" else 0

            non_drc_bonus = 1 if ("drc" not in fmt_id and "drc" not in fmt_note) else 0
            return (codec_bonus, non_drc_bonus, abr)

        audio_sorted = sorted(audio_only, key=score, reverse=True)
        ids: List[str] = []
        for f in audio_sorted:
            fmt_id = str(f.get("format_id") or "").strip()
            if fmt_id and fmt_id not in ids:
                ids.append(fmt_id)
        return ids

    def _ytdlp_download_audio(self, url: str, ydl_opts: Dict) -> Dict:
        if "logger" not in ydl_opts:
            ydl_opts = dict(ydl_opts)
            ydl_opts["logger"] = _YtDlpLogger()
        with YoutubeDL(ydl_opts) as ydl:  # type: ignore[misc]
            info = ydl.extract_info(url, download=True) or {}
            try:
                info["_filename"] = ydl.prepare_filename(info)
            except Exception:
                pass
            return info

    async def launch_browser(self, chromium, playwright_proxy: Optional[Dict], user_agent: Optional[str], headless: bool = True):
        raise NotImplementedError("YouTube crawler does not use Playwright browser")
