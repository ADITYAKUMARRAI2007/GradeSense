# GradeSense - AI-Powered Grading Tool

## Product Overview
GradeSense is a comprehensive web application MVP for AI-powered grading of handwritten answer papers. It provides automated grading with teacher review capabilities.

## Core Features
- **Authentication:** Separate login for "Teacher" and "Student" with Google OAuth (BROKEN) and email/password fallback
- **AI Grading:** Uses Google Gemini 2.5 Flash for intelligent grading of handwritten papers
- **Annotation System:** AI-generated step-wise feedback with visual annotations overlaid on student answer sheets
- **Review Papers:** Teachers can review, modify scores, and provide feedback on AI-graded papers
- **Multi-page Document Viewer:** Scrollable modal for viewing multi-page documents (answer sheets, model answers)

## Technical Stack
- **Frontend:** React with Shadcn/UI components
- **Backend:** FastAPI (Python)
- **Database:** MongoDB
- **AI Integration:** Google Gemini 2.5 Flash via Emergent LLM Key

## Architecture
```
/app/
├── backend/
│   ├── server.py              # Main FastAPI server
│   ├── annotation_utils.py    # Image annotation utilities
│   ├── background_grading.py  # Background task processing
│   └── models/                # Database models
└── frontend/
    └── src/
        ├── pages/
        │   └── teacher/
        │       └── ReviewPapers.jsx  # Main review interface
        └── components/ui/            # Shadcn UI components
```

## Completed Work (January 2025)

### Session - January 29, 2025
- **Multi-page Modal Bug Fix:** Resolved issue where the image viewer modal didn't open/close instantly
  - Root cause: Modal Dialog was nested inside DetailContent useMemo which was inside another Dialog
  - Fix: Moved multi-page Dialog to component's top-level return statement
  - All modal operations (open/close via click, X button, ESC) now work instantly
- **UI Verification:** Confirmed "Apply this correction to all papers" checkbox is correctly placed in "Improve AI" feedback dialog

### Previous Sessions
- **Annotation System:** Built backend (annotation_utils.py) and frontend for visual annotations
- **Password Migration:** Implemented "Set Password" flow for Google OAuth users
- **Profile Setup Loop Fix:** Fixed bug forcing existing users into profile setup
- **Grading Timeout Fix:** Increased background grading timeout to 10 minutes

## Known Issues

### P0 - Critical
- **Google OAuth Authentication:** BROKEN - Emergent-managed Google Auth not working

### P1 - High
- Incomplete Multi-Format File Uploads

### P2 - Medium
- State Restoration on "Upload & Grade" Page is Broken

### P3 - Low
- Intermittent 404 Errors for `/api/notifications`
- Console warnings about missing DialogTitle and aria-describedby for accessibility

## Upcoming Tasks

### P1 Priority
- Get user verification for Annotation Feature
- Create Exam Analytics Page
- Build Student Profile Drawer component
- Enhance Student-Upload Workflow

### P2 Priority
- Implement "Few-Shot Learning" for Grading
- Pagination/Virtualization for large lists
- Re-evaluation request feature for students

### P3 Priority
- Refactor monolithic server.py

## 3rd Party Integrations
- **Google Gemini 2.5 Flash:** AI grading (uses Emergent LLM Key)
- **Emergent-managed Google Auth:** User authentication (BROKEN)
