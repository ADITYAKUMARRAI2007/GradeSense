# P0 Fix: Frontend UI Not Updating on Job Completion

## Problem Statement
After the MongoDB task queue was implemented, grading jobs completed successfully in the background, but the frontend UI failed to reflect this. The progress bar did not move, and the status remained "Processing..." indefinitely, even though the database record showed the job as "completed."

## Root Cause Analysis

### The Core Issue
When a user navigated away from the "Upload & Grade" page during an active grading job and then returned, the component would:
1. ✅ Restore the `activeJobId` from localStorage
2. ✅ Set `processing = true` 
3. ✅ Return to Step 5
4. ❌ **NEVER restart the polling interval**

The original polling interval was created inside the `handleStartGrading` function. When the user navigated away, the component unmounted, and the interval reference was lost. Upon returning, while the state was restored, **no new polling interval was created**, leaving the UI stuck in a "Processing..." state with no updates.

## The Fix

### 1. Centralized Polling Function
Created a `startPollingJob` function using `useCallback` that can be called both:
- When starting a new grading job (`handleStartGrading`)
- When restoring state from localStorage (state restoration `useEffect`)

### 2. Used useRef for Interval Management
Changed from using state (`pollIntervalRef` as state) to `useRef` to avoid unnecessary re-renders and dependency issues:
```javascript
const pollIntervalRef = useRef(null);
```

### 3. Restart Polling on State Restoration
Added a critical line in the state restoration logic:
```javascript
// CRITICAL FIX: Restart polling for the active job
startPollingJob(state.activeJobId);
```

### 4. Proper Cleanup
Added a cleanup effect to clear the polling interval when the component unmounts:
```javascript
useEffect(() => {
  return () => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
    }
  };
}, []);
```

## Files Modified
- `/app/frontend/src/pages/teacher/UploadGrade.jsx`
  - Added `useRef` import
  - Created centralized `startPollingJob` function
  - Updated state restoration to restart polling
  - Fixed undefined `setUploading` → `setLoading`
  - Added cleanup effect

## Testing Checklist
- [ ] Start a grading job with 3+ papers
- [ ] Navigate away to another page (e.g., "Review Papers")
- [ ] Navigate back to "Upload & Grade"
- [ ] Verify progress bar updates correctly
- [ ] Wait for job completion
- [ ] Verify UI transitions to Step 6 (Results) automatically
- [ ] Verify "Completed" toast notification appears
- [ ] Verify GlobalGradingProgress banner also shows completion

## Expected Behavior After Fix
1. ✅ Polling continues even after navigation
2. ✅ Progress bar updates in real-time
3. ✅ UI automatically transitions to Step 6 when job completes
4. ✅ Success/error toasts are displayed
5. ✅ localStorage is cleared upon completion
6. ✅ GlobalGradingProgress banner reflects completion status

## Technical Details
- **Polling Interval**: 2 seconds
- **Safety Timeout**: 20 minutes (prevents infinite polling)
- **State Management**: Uses both localStorage and React state
- **Cross-Page Sync**: GlobalGradingProgress component polls independently

## Related Components
- `/app/frontend/src/components/GlobalGradingProgress.jsx` - Already had correct polling logic
- `/app/backend/background_grading.py` - Backend correctly updates job status
- `/app/backend/task_worker.py` - Worker correctly processes jobs

## Key Insights
The bug was a classic **state restoration without side-effect restoration** issue. While React state was properly saved and restored, the side effect (polling interval) was not, creating a disconnect between the UI's displayed state and the actual backend state.
