# GradeSense Analytics Overview

## Frontend - What Teachers See

### 1. **Class Reports Page** (`/teacher/reports`)
This is the main analytics dashboard with comprehensive data visualization.

#### Features Displayed:

**A. Overview Cards (Top Section)**
- Total Students count
- Class Average percentage (color-coded: green ≥70%, amber 50-69%, red <50%)
- Highest Score
- Lowest Score
- Pass Rate percentage

**B. Filters**
- Batch filter (dropdown)
- Subject filter (dropdown)
- Exam filter (dropdown)
- Export to CSV button

**C. Quick Actions Card** (appears when exam is selected)
- "Generate Review Packet" button - Creates AI-powered practice questions

**D. Topic Mastery Heatmap** (Interactive Grid)
- Visual heatmap showing mastery levels for each topic
- Color-coded tiles:
  - Green: Mastered (≥70%)
  - Amber: Developing (50-69%)
  - Red: Critical (<50%)
- Shows: Topic name, average percentage, question count, struggling students count
- **Clickable** - Opens topic detail modal with:
  - Class average for that topic
  - Questions in that topic
  - Students needing attention
  - Recommended actions

**E. Common Misconceptions Section** (The "Why" Engine)
- AI-powered analysis of why students fail specific questions
- Shows:
  - AI Insights summary (confusion patterns & recommendations)
  - Question breakdown with:
    - Question number
    - Average percentage
    - Fail rate
    - Number of students who failed
    - Question text preview
- **Clickable** - Opens question insight modal

**F. Charts**
1. **Score Distribution** - Bar chart showing student count in ranges (0-20, 21-40, 41-60, 61-80, 81-100)
2. **Question-wise Performance** - Horizontal bar chart showing avg percentage per question

**G. Student Lists**
1. **Top Performers** - Green cards with top students, scores
2. **Needs Attention** - Red cards with struggling students
- **Both clickable** - Opens Student Deep-Dive modal

**H. Modals/Dialogs**

1. **Student Deep-Dive Modal**
   - Overall average, total exams, areas to improve count
   - AI Analysis with:
     - Summary of student's performance
     - Personalized recommendations
     - Concepts to review (as badges)
   - Areas Needing Improvement (questions with <60%)
   - Performance trend line chart

2. **Question Insight Modal**
   - Question text
   - Average score & failure rate
   - Sample wrong answers from students with feedback

3. **Review Packet Modal**
   - AI-generated practice questions targeting weak areas
   - Each question shows: number, marks, difficulty, hint, topic
   - Download button

4. **Topic Detail Modal**
   - Class average for the topic
   - All questions in that topic
   - Students needing attention
   - Recommended actions based on performance

---

### 2. **Class Insights Page** (`/teacher/insights`)
A simpler, more actionable view focused on quick insights.

#### Features Displayed:

**A. Filters**
- Batch selector
- Exam selector
- Refresh button

**B. Quick Actions Card**
- Review Papers button
- View Reports button
- Generate Practice Questions button

**C. Main Content (4 Cards)**

1. **Strengths Card** (Green)
   - Lists topics/areas where students excel
   - Shows percentage for each
   - **Clickable** - Navigates to full reports

2. **Areas for Improvement Card** (Red)
   - Lists topics that need attention
   - Shows low percentages
   - **Clickable** - Triggers practice question generation

3. **AI Recommendations Card** (Spans 2 columns)
   - Grid of actionable recommendations
   - Color-coded tiles (blue, purple, amber)
   - Some tiles are clickable to trigger actions

4. **Topic Performance Overview** (If weak topics exist)
   - Grid of topic tiles with percentages
   - Color-coded by performance
   - **Clickable** - Shows toast with focus suggestion

**D. Review Packet Dialog** (Same as in Reports page)

---

## Backend - How It Works

### Analytics Endpoints:

#### 1. **GET `/api/analytics/class-report`**
**Query Params:** `batch_id`, `subject_id`, `exam_id`

**Returns:**
```json
{
  "overview": {
    "total_students": 25,
    "avg_score": 67.5,
    "highest_score": 95,
    "lowest_score": 32,
    "pass_percentage": 72
  },
  "score_distribution": [
    { "range": "0-20", "count": 1 },
    { "range": "21-40", "count": 2 },
    ...
  ],
  "top_performers": [
    { "name": "Alice", "score": 95, "percentage": 95, "student_id": "..." }
  ],
  "needs_attention": [...],
  "question_analysis": [
    { "question": 1, "percentage": 78 }
  ]
}
```

**Logic:**
- Fetches all submissions based on filters
- Calculates aggregate statistics
- Groups students into performers/struggling
- Analyzes question-wise performance

---

#### 2. **GET `/api/analytics/insights`**
**Query Params:** `batch_id`, `exam_id`

**Returns:**
```json
{
  "summary": "Overall class performance is good with...",
  "strengths": [
    { "topic": "Calculus", "percentage": 85 }
  ],
  "weaknesses": [
    { "topic": "Trigonometry", "percentage": 45 }
  ],
  "recommendations": [
    "Schedule review session for Trigonometry",
    "Focus on weak areas identified..."
  ],
  "weak_topics": [...]
}
```

**Logic:**
- Aggregates performance data
- Identifies patterns using topic extraction from questions
- Generates AI-powered insights (if implemented)
- Creates actionable recommendations

---

#### 3. **GET `/api/analytics/misconceptions`**
**Query Params:** `exam_id` (required)

**Returns:**
```json
{
  "ai_analysis": [
    {
      "question": 1,
      "confusion": "Students confused derivative rules",
      "recommendation": "Review chain rule with examples"
    }
  ],
  "question_insights": [
    {
      "question_number": 1,
      "question_text": "...",
      "total_students": 25,
      "failing_students": 12,
      "fail_rate": 48,
      "avg_percentage": 52,
      "wrong_answers": [
        {
          "student_name": "Bob",
          "obtained": 2,
          "max": 5,
          "feedback": "Incorrect application of formula"
        }
      ]
    }
  ]
}
```

**Logic:**
- Analyzes each question's performance
- Identifies questions with high failure rates (≥20%)
- Collects sample wrong answers
- Uses AI to identify common misconceptions and confusion patterns

---

#### 4. **GET `/api/analytics/topic-mastery`**
**Query Params:** `batch_id`, `exam_id`

**Returns:**
```json
{
  "topics": [
    {
      "topic": "Calculus",
      "avg_percentage": 78,
      "sample_count": 50,
      "question_count": 5,
      "struggling_count": 3,
      "color": "amber"
    }
  ],
  "questions_by_topic": {
    "Calculus": [
      {
        "exam_name": "Midterm",
        "question_number": 1,
        "rubric": "...",
        "max_marks": 10
      }
    ]
  },
  "students_by_topic": {
    "Calculus": [
      {
        "student_id": "...",
        "name": "Charlie",
        "avg_score": 45
      }
    ]
  }
}
```

**Logic:**
- Extracts topics from question rubrics using AI
- Groups all question scores by topic
- Calculates average mastery per topic
- Identifies struggling students per topic
- Color-codes topics: green (≥70%), amber (50-69%), red (<50%)

---

#### 5. **GET `/api/analytics/student-deep-dive/{student_id}`**
**Query Params:** `exam_id` (optional)

**Returns:**
```json
{
  "overall_average": 72,
  "total_exams": 5,
  "performance_trend": [
    { "exam_name": "Quiz 1", "percentage": 65 },
    { "exam_name": "Midterm", "percentage": 78 }
  ],
  "worst_questions": [
    {
      "exam_name": "Midterm",
      "question_number": 3,
      "percentage": 40,
      "question_text": "...",
      "ai_feedback": "Student needs to review..."
    }
  ],
  "ai_analysis": {
    "summary": "Charlie shows improvement in...",
    "recommendations": ["Focus on problem-solving", "Practice more"],
    "concepts_to_review": ["Integration", "Limits"]
  }
}
```

**Logic:**
- Fetches all submissions for the student
- Calculates overall statistics
- Identifies worst-performing questions (<60%)
- Generates AI-powered personalized analysis
- Creates performance trend over time

---

#### 6. **POST `/api/analytics/generate-review-packet`**
**Query Params:** `exam_id` (required)

**Returns:**
```json
{
  "exam_name": "Midterm",
  "subject": "Mathematics",
  "weak_areas_identified": 3,
  "practice_questions": [
    {
      "question_number": 1,
      "question": "Solve the differential equation...",
      "marks": 5,
      "difficulty": "medium",
      "topic": "Differential Equations",
      "hint": "Use separation of variables"
    }
  ]
}
```

**Logic:**
- Analyzes exam to find questions with <60% class average
- Identifies weak topics
- Uses AI (Gemini) to generate new practice questions targeting those topics
- Questions are tailored to difficulty and mark distribution

---

## Key Backend Components

### Data Processing:
1. **Topic Extraction**: Uses regex and AI to extract topics from question rubrics
2. **AI Analysis**: Gemini 2.5 Flash for misconception analysis and review packet generation
3. **Aggregation**: MongoDB aggregation pipelines for performance statistics
4. **Caching**: Memory-based caching for model answers and question data

### Performance Optimization:
- Filters by teacher_id to ensure data isolation
- Uses projections to exclude large fields (_id, file_data, file_images)
- Limits result sets (e.g., .to_list(1000))

### Color Coding Logic:
- **Green**: ≥70% (Mastered/Good)
- **Amber**: 50-69% (Developing/Average)
- **Red**: <50% (Critical/Needs Attention)

---

## Known Issues (Per Handoff Summary)

### Issue 2: Analytics Features Not Working Correctly
**Problems:**
1. **Topic Mastery Heatmap**: Not interactive (need to verify clickability)
2. **Student Deep-Dive Modal**: Shows incorrect data
3. **Class Insights Page**: Described as "bland" (may need UX improvements)

**Likely Root Causes:**
- Topic extraction logic may be failing or returning wrong data
- Student deep-dive API might have data fetching issues
- AI analysis might not be generating properly
- Modal click handlers might not be wired correctly

**Next Steps for Fixing:**
1. Test topic mastery heatmap interactivity (click on tiles)
2. Verify student deep-dive data accuracy by comparing DB vs UI
3. Check if AI analysis is actually being called and returning data
4. Review Class Insights page for missing features or stale data
