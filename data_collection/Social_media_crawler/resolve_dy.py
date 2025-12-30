import requests
import re

short_url = "https://v.douyin.com/mp5dWB323NE/"

try:
    res = requests.get(short_url, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 13_2_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/13.0.3 Mobile/15E148 Safari/604.1"})
    long_url = res.url
    print(f"Long URL: {long_url}")
    
    # Extract video ID
    # Pattern: /video/(\d+)
    match = re.search(r'/video/(\d+)', long_url)
    if match:
        video_id = match.group(1)
        print(f"VIDEO_ID: {video_id}")
    else:
        # Check for user profile sec_uid
        match = re.search(r'sec_uid=([A-Za-z0-9_-]+)', long_url)
        if match:
            sec_uid = match.group(1)
            print(f"SEC_USER_ID: {sec_uid}")
        else:
            print("Could not extract video ID")

except Exception as e:
    print(f"Error: {e}")
