# GradeSense - AI-Powered Grading Tool

## Original Problem Statement
Create a comprehensive web application MVP called "GradeSense" - an AI-powered grading tool for handwritten answer papers with orange and white color palette, desktop-first layout.

## Architecture & Tech Stack
- **Frontend**: React 19 with Tailwind CSS, Shadcn/UI components
- **Backend**: FastAPI (Python)
- **Database**: MongoDB
- **AI**: Google Gemini 3 Flash via Emergent LLM Key (Vision API for handwritten paper analysis)
- **Authentication**: Emergent-managed Google OAuth

## Implemented Features

### Authentication
- Google OAuth login with Emergent Auth integration
- Role-based access (Teacher/Student)
- Session management with httpOnly cookies

### Teacher Features
1. **Dashboard** - Stats cards, recent submissions, quick actions
2. **Upload & Grade** - Multi-step form (6 steps):
   - Exam configuration
   - Question setup with rubrics
   - Grading mode selection (Strict/Balanced/Conceptual/Lenient)
   - Model answer upload
   - Student papers upload
   - AI-powered grading results
3. **Review Papers** - Split-view with PDF preview and grading panel
4. **Class Reports** - Charts, score distribution, top performers
5. **Class Insights** - AI-generated recommendations and analysis
6. **Manage Students** - CRUD operations, batch assignments
7. **Re-evaluation Requests** - Handle student disputes

### Student Features
1. **Dashboard** - Performance stats, trends, study recommendations
2. **My Results** - Detailed exam results with feedback
3. **Request Re-evaluation** - Submit grade disputes

## Database Collections
- users (teachers and students)
- user_sessions (auth sessions)
- batches (class groups)
- subjects
- exams (exam configurations)
- submissions (graded papers)
- re_evaluations (grade disputes)

## API Endpoints
- `/api/auth/*` - Authentication routes
- `/api/batches/*` - Batch management
- `/api/subjects/*` - Subject management  
- `/api/students/*` - Student management
- `/api/exams/*` - Exam management
- `/api/submissions/*` - Submission management
- `/api/re-evaluations/*` - Re-evaluation requests
- `/api/analytics/*` - Dashboard and reports

## Next Tasks
1. Add email notifications for grade release
2. Implement CSV bulk student import
3. Add PDF export for results
4. Implement parent notification system
5. Add performance trends visualization
6. Enhance AI grading with custom rubrics
