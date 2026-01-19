# 🔐 Admin API - User Data Export

## Your Secret Admin API Key

```
YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU
```

**⚠️ IMPORTANT: Keep this key secret! Don't share it with anyone.**

---

## 🌐 Base URL

Your app URL: Check your REACT_APP_BACKEND_URL in `/app/frontend/.env`

Example: `https://your-app.emergentagent.com`

---

## 📊 Export All Users (JSON)

### Basic Usage - Get All Users

**URL:**
```
https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU
```

**Method:** GET

**Response Example:**
```json
{
  "total_users": 147,
  "exported_at": "2025-01-19T10:30:00Z",
  "filters_applied": {
    "role": null,
    "created_after": null,
    "last_login_after": null,
    "fields": null
  },
  "users": [
    {
      "user_id": "user_abc123",
      "email": "teacher@example.com",
      "name": "John Doe",
      "role": "teacher",
      "picture": "https://...",
      "batches": [],
      "created_at": "2025-01-15T08:30:00Z",
      "last_login": "2025-01-19T09:45:00Z",
      "active_sessions_count": 2,
      "exams_created": 15,
      "sessions": [
        {
          "session_token": "session_xyz...",
          "user_id": "user_abc123",
          "created_at": "2025-01-19T09:45:00Z",
          "expires_at": "2025-01-26T09:45:00Z"
        }
      ]
    }
  ]
}
```

---

## 📥 Export as CSV (Spreadsheet)

**URL:**
```
https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU&format=csv
```

This will download a CSV file that you can open in Excel or Google Sheets.

---

## 🔍 Filtering Options

### 1. Filter by Role

**Teachers only:**
```
https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU&role=teacher
```

**Students only:**
```
https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU&role=student
```

### 2. Filter by Creation Date

**Users registered after January 1, 2025:**
```
https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU&created_after=2025-01-01
```

### 3. Filter by Last Login

**Users who logged in after January 15, 2025:**
```
https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU&last_login_after=2025-01-15
```

### 4. Select Specific Fields

**Only get email, name, and last login:**
```
https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU&fields=email,name,last_login
```

### 5. Combine Filters

**Teachers who logged in this month as CSV:**
```
https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU&role=teacher&last_login_after=2025-01-01&format=csv
```

---

## 💻 Usage Methods

### Method 1: Browser (Easiest)

1. Copy any URL above
2. Paste in browser address bar
3. Press Enter
4. Data appears (JSON) or downloads (CSV)

### Method 2: Command Line (Terminal/CMD)

**Download as JSON:**
```bash
curl "https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU" > users.json
```

**Download as CSV:**
```bash
curl "https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU&format=csv" > users.csv
```

### Method 3: Python Script

```python
import requests
import pandas as pd

# Your config
API_KEY = "YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU"
BASE_URL = "https://your-app.emergentagent.com"

# Fetch data
response = requests.get(
    f"{BASE_URL}/api/admin/export-users",
    params={"api_key": API_KEY}
)

data = response.json()
users = data["users"]

print(f"Total users: {data['total_users']}")
print(f"Exported at: {data['exported_at']}")

# Convert to DataFrame
df = pd.DataFrame(users)

# Save to Excel
df.to_excel("users_export.xlsx", index=False)
print("Saved to users_export.xlsx")

# Save to CSV
df.to_csv("users_export.csv", index=False)
print("Saved to users_export.csv")
```

### Method 4: Postman

1. Open Postman
2. Create new GET request
3. URL: `https://your-app.emergentagent.com/api/admin/export-users`
4. Add query parameter:
   - Key: `api_key`
   - Value: `YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU`
5. Click Send
6. View/Download response

---

## 📋 Available Fields

### User Fields
- `user_id` - Unique user identifier
- `email` - User's email address
- `name` - Full name
- `role` - "teacher" or "student"
- `picture` - Profile picture URL
- `batches` - Array of batch IDs (for students/teachers)
- `created_at` - Account creation timestamp
- `last_login` - Last login timestamp (optional)

### Enriched Data
- `active_sessions_count` - Number of active sessions
- `sessions` - Array of session objects
- `exams_created` - Number of exams created (teachers only)
- `submissions_count` - Number of submissions (students only)

### Session Object Fields
- `session_token` - Session identifier
- `user_id` - Associated user
- `created_at` - When session was created
- `expires_at` - When session expires (7 days from creation)

---

## 🔐 Security Notes

1. **Keep API key secret** - Never share or commit to GitHub
2. **Use HTTPS only** - Always use secure connection
3. **Change key if compromised** - Update in `/app/backend/.env`
4. **API key location** - Stored in: `/app/backend/.env` as `ADMIN_API_KEY`

### How to Change API Key

1. SSH into server
2. Edit `/app/backend/.env`
3. Change `ADMIN_API_KEY=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU` to new value
4. Restart backend: `sudo supervisorctl restart backend`
5. Update this documentation

### Generate New API Key

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

---

## ❌ Error Handling

### Wrong API Key
**Request:**
```
?api_key=wrong_key
```
**Response:**
```json
{
  "detail": "Unauthorized - Invalid API key"
}
```
**Status Code:** 403

### Missing API Key
**Request:**
```
/api/admin/export-users
```
**Response:**
```json
{
  "detail": "Field required"
}
```
**Status Code:** 422

---

## 📊 Example Use Cases

### 1. Daily User Report

Get all users who logged in today:
```bash
TODAY=$(date +%Y-%m-%d)
curl "https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU&last_login_after=$TODAY&format=csv" > daily_logins.csv
```

### 2. Teacher List

Export all teachers with contact info:
```
?api_key=YOUR_KEY&role=teacher&fields=email,name,created_at&format=csv
```

### 3. New Users This Week

Get users registered in last 7 days:
```bash
WEEK_AGO=$(date -d '7 days ago' +%Y-%m-%d)
curl "https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU&created_after=$WEEK_AGO" > new_users.json
```

### 4. Active Users Analysis

Get users with active sessions:
```python
import requests

response = requests.get(
    "https://your-app.emergentagent.com/api/admin/export-users",
    params={"api_key": "YOUR_KEY"}
)

users = response.json()["users"]
active_users = [u for u in users if u["active_sessions_count"] > 0]
print(f"Active users: {len(active_users)}/{len(users)}")
```

---

## 🆘 Support

If you encounter any issues:

1. Check API key is correct
2. Verify backend is running: `sudo supervisorctl status backend`
3. Check backend logs: `tail -f /var/log/supervisor/backend.err.log`
4. Test without filters first
5. Ensure HTTPS is used

---

## ✅ Quick Test

Test if endpoint is working:
```
https://your-app.emergentagent.com/api/admin/export-users?api_key=YoD4OjkOVz1l1gdAEKNiPXpPvORaJ6MlFoiO_SdT9lU&fields=email&role=teacher
```

Should return list of teacher emails only.

---

**Last Updated:** January 19, 2025
**API Version:** 1.0
**Endpoint:** `/api/admin/export-users`
