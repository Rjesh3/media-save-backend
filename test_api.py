import requests
import json
import time
import base64

API_URL = "http://localhost:8000/analyze"

TEST_URLS = {
    "youtube": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "tiktok": "https://www.tiktok.com/@tiktok/video/7106594312292453675",
    "instagram": "https://www.instagram.com/p/C-j-y-Gv63m/",
    "facebook": "https://www.facebook.com/watch/?v=10153231379946729"
}

def test_platform(platform, url):
    print(f"Testing {platform.capitalize()}...")
    try:
        response = requests.post(API_URL, json={"url": url}, timeout=30)
        if response.status_code == 200:
            data = response.json()
            formats = data.get("formats", [])
            if formats:
                best_format = formats[0]
                download_url = best_format.get("download_url")
                print(f"✅ {platform.capitalize()} Analyze Success! Title: {data.get('title', 'N/A')}")
                
                # Test the /download proxy
                print(f"Testing /download proxy for {platform.capitalize()}...")
                proxy_url = f"http://localhost:8000/download"
                
                # We need to construct the h parameter if headers exist
                headers = best_format.get("headers", {})
                h_param = base64.b64encode(json.dumps(headers).encode('utf-8')).decode('utf-8')
                
                params = {
                    "url": download_url,
                    "filename": f"test_{platform}.mp4",
                    "h": h_param
                }
                
                dl_response = requests.get(proxy_url, params=params, stream=True, timeout=10)
                if dl_response.status_code == 200:
                    bytes_received = len(dl_response.raw.read(1024))
                    print(f"✅ {platform.capitalize()} Download Streaming Success! Read {bytes_received} bytes.")
                    return True
                else:
                    print(f"❌ {platform.capitalize()} Download Failed! Status: {dl_response.status_code} Response: {dl_response.text[:200]}")
                    return False
                
            else:
                print(f"❌ {platform.capitalize()} Failed! Response missing formats.")
                return False
        else:
            print(f"❌ {platform.capitalize()} Failed! Status Code: {response.status_code}. Response: {response.text[:200]}")
            return False
    except Exception as e:
        print(f"❌ {platform.capitalize()} Error! Exception: {e}")
        return False

print("Starting E2E API Verification...\n")
results = {}
for platform, url in TEST_URLS.items():
    results[platform] = test_platform(platform, url)
    time.sleep(1) # Be nice to yt-dlp/APIs

print("\n--- Summary ---")
all_passed = True
for platform, passed in results.items():
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{platform.capitalize()}: {status}")
    if not passed:
        all_passed = False

if all_passed:
    print("\n🎉 ALL core download features are working perfectly!")
else:
    print("\n⚠️ Some features failed. They need to be fixed.")
