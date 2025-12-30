# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。

import os

# YouTube crawler config

# detail 模式下指定视频ID列表（video_id, 如: dQw4w9WgXcQ）
YT_SPECIFIED_ID_LIST = []

# creator 模式下指定频道URL列表
YT_CREATOR_ID_LIST = [
    "https://www.youtube.com/@小左美股第一视角/videos",
]

# 优先尝试的字幕语言（逗号分隔）；会优先选择“人工字幕”，否则选择“自动字幕”
YOUTUBE_TRANSCRIPT_LANGS = "zh-Hans,zh-Hant,zh,en"

# 是否尝试抓取 YouTube 字幕（caption/auto caption）
YOUTUBE_ENABLE_TRANSCRIPT = True

# 当字幕获取失败时，是否回退到“下载音频 + 本地ASR转写”
# 需要安装 yt-dlp，且本地ASR需要 funasr 等依赖（见 tools/transcriber.py 日志提示）
YOUTUBE_ENABLE_AUDIO_FALLBACK = True

# 是否跳过“频道会员专享”等无法访问的视频（members-only）
YOUTUBE_SKIP_MEMBERS_ONLY = True

# creator 模式下，最多扫描多少个频道视频条目（用于在大量 members-only 时仍能抓到普通视频）
YOUTUBE_CREATOR_FETCH_LIMIT = 200

# 可选：yt-dlp remote components（用于解决 YouTube 的 JS challenge / EJS）
# 设为空表示不启用；推荐值：["ejs:github"]
YOUTUBE_REMOTE_COMPONENTS = ["ejs:github"]

# 可选：为 requests/yt-dlp 设置单个代理（例如: http://user:pass@host:port）
# 若为空则使用系统环境变量 HTTP_PROXY/HTTPS_PROXY（如已设置）
YOUTUBE_PROXY = os.getenv("YOUTUBE_PROXY")

# 可选：指定浏览器以获取 cookies (例如: 'chrome', 'firefox', 'edge', 'safari' 等)
# 对应 yt-dlp 的 --cookies-from-browser 参数
# 如果遇到 "Sign in to confirm you’re not a bot" 错误，请尝试设置此项
YOUTUBE_COOKIES_FROM_BROWSER = "chrome"
