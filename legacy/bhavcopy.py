import requests
from datetime import datetime, timedelta
import os
import time
 
# =========================
# SESSION
# =========================
 
session = requests.Session()
 
headers = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
 
session.headers.update(headers)
 
# Generate cookies
session.get("https://www.nseindia.com", headers=headers)
 
# =========================
# DOWNLOAD FUNCTION
# =========================
 
def download_bhavcopy(start_date, end_date, save_folder):
 
    if not os.path.exists(save_folder):
        os.makedirs(save_folder)
 
    current_date = start_date
 
    while current_date <= end_date:
 
        # Skip weekends
        if current_date.weekday() < 5:
 
            # NEW NSE FORMAT
            date_str = current_date.strftime("%Y%m%d")
 
            file_name = (
                f"BhavCopy_NSE_CM_0_0_0_{date_str}_F_0000.csv.zip"
            )
 
            url = (
                "https://nsearchives.nseindia.com/content/cm/"
                + file_name
            )
 
            print(f"\nDownloading: {file_name}")
 
            try:
 
                response = session.get(url, timeout=20)
 
                if response.status_code == 200:
 
                    save_path = os.path.join(
                        save_folder,
                        file_name
                    )
 
                    with open(save_path, "wb") as f:
                        f.write(response.content)
 
                    print(
                        f"✅ Saved: "
                        f"{current_date.strftime('%d-%m-%Y')}"
                    )
 
                else:
 
                    print(
                        f"❌ Not found: "
                        f"{current_date.strftime('%d-%m-%Y')}"
                    )
 
            except Exception as e:
 
                print(
                    f"⚠️ Error: {e}"
                )
 
            time.sleep(1)
 
        current_date += timedelta(days=1)
 
    print("\n🎉 Download Completed!")
 
# =========================
# DATE RANGE
# =========================
 
start = datetime(2026, 4, 14)
end = datetime(2026, 5, 20)
 
# =========================
# SAVE LOCATION
# =========================
 
save_path = r"C:\Users\ADMIN\Downloads\Bhavcopy"
 
# =========================
# RUN
# =========================
 
download_bhavcopy(start, end, save_path)