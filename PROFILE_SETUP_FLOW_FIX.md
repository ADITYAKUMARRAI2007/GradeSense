# Profile Setup Flow - Complete Fix

## User's Expected Flow

### 1. Login Page
- User sees the **Login Page** with "Sign in as Teacher" and "Sign in as Student" buttons
- User clicks on the appropriate role button

### 2. Google OAuth
- User is redirected to **Google OAuth** page for authentication
- User authenticates with their Google account

### 3. Post-Authentication Flow

#### For NEW Users (First Time Login):
1. Backend creates user with `profile_completed: false`
2. AuthCallback checks profile status via `/api/profile/check`
3. Detects `profile_completed === false`
4. Redirects to **Profile Setup** page
5. User fills in profile details (name, contact, teacher type, etc.)
6. On submit, backend sets `profile_completed: true`
7. User is redirected to **Dashboard**
8. **Profile setup is NEVER shown again** for this user

#### For EXISTING Users (Returning Users):
1. Backend marks user with `profile_completed: true` (or `null` for legacy users)
2. AuthCallback checks profile status via `/api/profile/check`
3. Detects profile is complete (`true` or `null`)
4. Redirects directly to **Dashboard**
5. **Profile setup is NEVER shown**

## Implementation Details

### Backend (`/app/backend/server.py`)

#### 1. `/api/auth/session` Endpoint (Lines 465-580)
```python
# For NEW users:
new_user = {
    ...
    "profile_completed": False,  # New users need to complete profile
}

# For EXISTING users:
await db.users.update_one(
    {"user_id": user_id},
    {"$set": {
        "profile_completed": True,  # Existing users are considered complete
    }}
)
```

#### 2. `/api/profile/check` Endpoint (Lines 1316-1335)
```python
# If profile_completed is None (legacy users), treat as complete
if profile_completed is None:
    profile_completed = True

return {
    "profile_completed": profile_completed,
    ...
}
```

#### 3. `/api/profile/complete` Endpoint (Lines 1272-1314)
```python
# When user completes profile setup:
update_data = {
    ...
    "profile_completed": True,  # Mark as complete
}
```

### Frontend

#### 1. AuthCallback (`/app/frontend/src/pages/AuthCallback.jsx`)
**NEW LOGIC:**
```javascript
// After successful OAuth session creation:
const profileResponse = await axios.get(`${API}/profile/check`);

// If NEW user, redirect to profile setup
if (profileResponse.data.profile_completed === false) {
    navigate('/profile/setup', { replace: true });
    return;
}

// If EXISTING user, redirect to dashboard
navigate(redirectPath, { replace: true });
```

#### 2. ProtectedRoute (`/app/frontend/src/App.js`)
**UPDATED LOGIC:**
- `/profile/setup` is now a **protected route** (requires authentication)
- Only checks profile status if user navigates directly (not from AuthCallback)
- Redirects to profile setup only if `profile_completed === false`

#### 3. ProfileSetup (`/app/frontend/src/pages/ProfileSetup.jsx`)
**UPDATED LOGIC:**
- When form is submitted, calls `/api/profile/complete`
- Backend sets `profile_completed: true`
- User is redirected to dashboard
- Profile setup will never show again

## Key Changes Made

### 1. Backend Changes
- ✅ New users get `profile_completed: false` on creation
- ✅ Existing users get `profile_completed: true` on login
- ✅ Legacy users with `null` are treated as complete

### 2. Frontend Changes
- ✅ Made `/profile/setup` a protected route
- ✅ AuthCallback checks profile status after OAuth
- ✅ New users are redirected to profile setup from AuthCallback
- ✅ Existing users go directly to dashboard
- ✅ ProtectedRoute only checks profile for direct navigation

## Testing Checklist

### For NEW Users:
1. ☐ Visit login page
2. ☐ Click "Sign in as Teacher"
3. ☐ Complete Google OAuth
4. ☐ Verify redirect to Profile Setup page
5. ☐ Fill in profile details and submit
6. ☐ Verify redirect to Dashboard
7. ☐ Navigate to other pages (Upload & Grade, Review Papers)
8. ☐ Verify NO redirect to profile setup
9. ☐ Logout and login again
10. ☐ Verify goes directly to dashboard (not profile setup)

### For EXISTING Users:
1. ☐ Visit login page
2. ☐ Click "Sign in as Teacher"
3. ☐ Complete Google OAuth
4. ☐ Verify redirect directly to Dashboard (skip profile setup)
5. ☐ Navigate to other pages
6. ☐ Verify NO redirect to profile setup at any time

## Database States

### User Profile States:
1. **`profile_completed: false`** → NEW user, needs profile setup
2. **`profile_completed: true`** → Profile setup completed
3. **`profile_completed: null`** → Legacy user (treated as complete)

## Files Modified

1. `/app/backend/server.py`
   - Line 1316-1335: Updated `/api/profile/check` to handle `null` values
   - Line 465-580: Existing logic for new/existing user detection

2. `/app/frontend/src/App.js`
   - Line 148-153: Made `/profile/setup` a protected route
   - Line 72-77: Updated ProtectedRoute to handle AuthCallback users

3. `/app/frontend/src/pages/AuthCallback.jsx`
   - Line 44-56: Added profile check after OAuth
   - Redirects new users to profile setup

## Important Notes

- Profile setup is shown **ONLY ONCE** for new users
- After completion, `profile_completed` is set to `true` permanently
- Existing users **NEVER** see profile setup
- The `/profile/setup` route is protected (requires authentication)
- Profile check happens in AuthCallback (for new logins) and ProtectedRoute (for direct navigation)
