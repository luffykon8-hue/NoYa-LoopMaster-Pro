import hashlib
import datetime
import sys
import os

SECRET_SALT = "NoYa_Remaster_Secret_2024" # Must match the salt in app.py
if hasattr(sys, '_MEIPASS'):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "license_log.txt")

def generate_license_key(device_id, expiry_date_str):
    data = f"{device_id}|{expiry_date_str}|{SECRET_SALT}"
    return hashlib.sha256(data.encode()).hexdigest()[:16].upper()

def search_log(device_id):
    """Searches the log file for entries matching the device ID."""
    if not os.path.exists(LOG_FILE):
        print("\n[!] Log file not found.")
        return

    print(f"\n--- Search Results for {device_id} ---")
    found = False
    try:
        with open(LOG_FILE, "r") as f:
            for line in f:
                if device_id in line:
                    print(line.strip())
                    found = True
    except Exception as e:
        print(f"Error reading log: {e}")
    
    if not found:
        print("No entries found.")
    print("----------------------------------------")

if __name__ == "__main__":
    while True:
        print("\n========================================")
        print("   NoYa Remaster Admin Key Generator    ")
        print("========================================")
        
        print("1. Generate New License Key")
        print("2. Search Log for Device ID")
        print("3. Exit")
        main_choice = input("\nSelect an option (1-3): ").strip()

        if main_choice == "3":
            print("Exiting...")
            break

        if main_choice == "2":
            search_id = input("\nEnter Device ID to search: ").strip().upper()
            if search_id:
                search_log(search_id)
            input("\nPress Enter to return to menu...")
            continue

        if main_choice == "1":
            device_id = input("\nEnter User's Device ID (XX:XX:XX:XX:XX:XX): ").strip().upper()
            
            if device_id:
                if len(device_id) != 17 or ":" not in device_id:
                    print("\n[!] Warning: The Device ID format looks unusual. Ensure it was copied correctly.")
                    if input("Continue anyway? (y/n): ").lower() != 'y':
                        continue

                print("\nSelect License Duration:")
                print("1. 7 days   (Actual: 10 days)")
                print("2. 14 days  (Actual: 21 days)")
                print("3. 1 month  (Actual: 1 month 14 days)")
                print("4. 3 months (Actual: 5 months)")
                print("5. 6 months (Actual: 11 months)")
                print("6. 1 year   (Actual: 2 years)")
                print("7. Permanent")
                
                choice = input("\nEnter choice (1-7): ").strip()
                
                durations = {
                    "1": 10,
                    "2": 21,
                    "3": 44,
                    "4": 150,
                    "5": 330,
                    "6": 730
                }
                
                if choice == "7":
                    expiry_date_str = "99991231"
                    display_date = "Permanent"
                elif choice in durations:
                    expiry_date = datetime.datetime.now() + datetime.timedelta(days=durations[choice])
                    expiry_date_str = expiry_date.strftime("%Y%m%d")
                    display_date = expiry_date.strftime('%Y-%m-%d')
                else:
                    print("\n[!] Invalid choice.")
                    input("Press Enter to return to menu...")
                    continue
                
                key = generate_license_key(device_id, expiry_date_str)
                full_license = f"{expiry_date_str}-{key}"
                
                print(f"\nGenerated License Key: {full_license}")
                print(f"Expires on: {display_date}")

                try:
                    log_entry = f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ID: {device_id} | Expiry: {display_date} | Key: {full_license}\n"
                    with open(LOG_FILE, "a") as f:
                        f.write(log_entry)
                    print(f"Key logged to: {LOG_FILE}")
                except Exception as e:
                    print(f"Warning: Could not write to log file: {e}")
                
                print("\nProvide the full string above to the user.")
                input("Press Enter to return to menu...")
            continue

        print("\n[!] Invalid option selected.")
        input("Press Enter to return to menu...")
