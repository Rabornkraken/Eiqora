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

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence
from urllib.parse import parse_qs, urlparse

from tools import utils

try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api._errors import TranscriptsDisabled
except Exception:  # pragma: no cover
    YouTubeTranscriptApi = None  # type: ignore[assignment]
    TranscriptsDisabled = Exception  # type: ignore[assignment]


@dataclass(frozen=True)
class YouTubeTranscriptResult:
    video_id: str
    language_code: str
    is_generated: bool
    text: str
    segments: List[Dict]


def extract_youtube_video_id(url_or_id: str) -> str:
    """
    Extract YouTube video id from common URL shapes:
    - https://www.youtube.com/watch?v=VIDEO_ID
    - https://youtu.be/VIDEO_ID
    - https://www.youtube.com/shorts/VIDEO_ID
    - https://www.youtube.com/embed/VIDEO_ID
    - direct VIDEO_ID
    """
    raw = (url_or_id or "").strip()
    if not raw:
        return ""

    # Direct id (typical 11 chars, but don't hard-fail on length)
    if "://" not in raw and "/" not in raw and "?" not in raw and "&" not in raw:
        return raw

    try:
        parsed = urlparse(raw)
    except Exception:
        return ""

    host = (parsed.netloc or "").lower()
    path = parsed.path or ""

    # youtu.be/<id>
    if "youtu.be" in host:
        vid = path.strip("/").split("/")[0]
        return vid

    # youtube.com/watch?v=<id>
    qs = parse_qs(parsed.query or "")
    if "v" in qs and qs["v"]:
        return qs["v"][0]

    # /shorts/<id> /embed/<id> /live/<id>
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] in {"shorts", "embed", "live"}:
        return parts[1]

    return ""


def _normalize_langs(langs: Sequence[str] | str | None) -> List[str]:
    if langs is None:
        return []
    if isinstance(langs, str):
        return [x.strip() for x in langs.split(",") if x.strip()]
    return [x.strip() for x in langs if x and x.strip()]


def _proxy_to_requests_mapping(proxy: Optional[str]) -> Optional[Dict[str, str]]:
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _join_segments(segments: List[Dict]) -> str:
    texts: List[str] = []
    for seg in segments:
        t = (seg or {}).get("text", "")
        if t:
            texts.append(t)
    return "\n".join(texts).strip()


def fetch_youtube_transcript(
    video_id: str,
    preferred_langs: Sequence[str] | str | None = None,
    proxy: Optional[str] = None,
) -> Optional[YouTubeTranscriptResult]:
    """
    Fetch captions/auto-captions via youtube-transcript-api.
    Returns None when unavailable/disabled.
    """
    if not video_id:
        return None
    if YouTubeTranscriptApi is None:  # pragma: no cover
        utils.logger.error("youtube-transcript-api not installed. Please add it to requirements and install.")
        return None

    langs = _normalize_langs(preferred_langs)
    proxies = _proxy_to_requests_mapping(proxy)

    try:
        try:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, proxies=proxies)
        except TypeError:
            transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)

        transcript = None
        if langs:
            try:
                transcript = transcript_list.find_manually_created_transcript(langs)
            except Exception:
                transcript = None
            if transcript is None:
                try:
                    transcript = transcript_list.find_generated_transcript(langs)
                except Exception:
                    transcript = None

        if transcript is None:
            for t in transcript_list:
                transcript = t
                break
        if transcript is None:
            return None

        segments = transcript.fetch()
        text = _join_segments(segments)
        return YouTubeTranscriptResult(
            video_id=video_id,
            language_code=getattr(transcript, "language_code", ""),
            is_generated=bool(getattr(transcript, "is_generated", False)),
            text=text,
            segments=segments,
        )
    except TranscriptsDisabled:
        return None
    except Exception as e:
        utils.logger.warning(f"[fetch_youtube_transcript] failed for {video_id}: {e}")
        return None
