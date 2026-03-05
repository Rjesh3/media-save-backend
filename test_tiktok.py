import requests
import yt_dlp

url = "https://www.tiktok.com/@tiktok/video/7106594312292453675"

ydl_opts = {
    'quiet': True,
    'format': 'best',
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36',
}

with yt_dlp.YoutubeDL(ydl_opts) as ydl:
    info = ydl.extract_info(url, download=False)
    
    # find best video
    formats = info.get('formats', [])
    for f in formats[::-1]:
        if f.get('vcodec') != 'none' and 'watermark' not in f.get('format_id', '').lower():
            target_format = f
            break
    
    download_url = target_format.get('url')
    yt_headers = info.get('http_headers', {}).copy()
    yt_headers.update(target_format.get('http_headers', {}))
    
    print("YT-DLP Headers:", yt_headers)
    
    # Try downloading with EXACT yt-dlp headers
    r = requests.get(download_url, headers=yt_headers, stream=True)
    print("First attempt status:", r.status_code)
    
    import traceback
    try:
        from curl_cffi import requests as c_req
        r3 = c_req.get(download_url, headers=yt_headers, impersonate="chrome")
        print("curl_cffi attempt (ALL headers) status:", r3.status_code)
        
        test_headers2 = yt_headers.copy()
        test_headers2.pop('Host', None)
        r4 = c_req.get(download_url, headers=test_headers2, impersonate="chrome")
        print("curl_cffi attempt (No Host) status:", r4.status_code)

        test_headers3 = {'User-Agent': yt_headers.get('User-Agent')}
        r5 = c_req.get(download_url, headers=test_headers3, impersonate="chrome")
        print("curl_cffi attempt (Only UA) status:", r5.status_code)

    except Exception as e:
        print("curl_cffi attempt failed:", e)
        traceback.print_exc()

