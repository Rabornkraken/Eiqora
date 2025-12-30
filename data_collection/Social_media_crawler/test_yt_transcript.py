from youtube_transcript_api import YouTubeTranscriptApi
print(dir(YouTubeTranscriptApi))
try:
    print(YouTubeTranscriptApi.get_transcript("xZPjmItf09w"))
except Exception as e:
    print(e)
