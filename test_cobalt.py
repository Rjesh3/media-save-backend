import requests
import json

def test_cobalt(url):
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json"
    }
    data = {
        "url": url
    }
    
    # Trying the official public instance or co.wuk.sh
    # According to cobalt docs, it's https://api.cobalt.tools/api/json
    try:
        response = requests.post("https://api.cobalt.tools/api/json", headers=headers, json=data, timeout=15)
        print("Status Code:", response.status_code)
        try:
            print("Response:", json.dumps(response.json(), indent=2))
        except:
            print("Response Text:", response.text)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    test_cobalt("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
