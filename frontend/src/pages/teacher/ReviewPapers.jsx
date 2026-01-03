import { useState, useEffect, useMemo, useCallback } from "react";
import axios from "axios";
import { API } from "../../App";
import Layout from "../../components/Layout";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import { Badge } from "../../components/ui/badge";
import { Checkbox } from "../../components/ui/checkbox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { ScrollArea } from "../../components/ui/scroll-area";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { Panel, Group as PanelGroup, Separator as PanelResizeHandle } from "react-resizable-panels";
import { toast } from "sonner";
import { 
  Search, 
  ChevronLeft, 
  ChevronRight, 
  Save, 
  CheckCircle,
  CheckCircle2,
  FileText,
  RefreshCw,
  X,
  Eye,
  EyeOff,
  ZoomIn,
  ZoomOut,
  Maximize2,
  MessageSquarePlus,
  Send,
  Lightbulb
} from "lucide-react";

export default function ReviewPapers({ user }) {
  const [submissions, setSubmissions] = useState([]);
  const [exams, setExams] = useState([]);
  const [batches, setBatches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedSubmission, setSelectedSubmission] = useState(null);
  const [filters, setFilters] = useState({
    exam_id: "",
    batch_id: "",
    search: ""
  });
  const [searchInput, setSearchInput] = useState(""); // Separate state for input
  const [saving, setSaving] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [showAnnotations, setShowAnnotations] = useState(false);
  const [showModelAnswer, setShowModelAnswer] = useState(false);
  const [showQuestionPaper, setShowQuestionPaper] = useState(false);
  const [modelAnswerImages, setModelAnswerImages] = useState([]);
  const [questionPaperImages, setQuestionPaperImages] = useState([]);
  const [examQuestions, setExamQuestions] = useState([]);
  const [zoomedImage, setZoomedImage] = useState(null);
  const [imageZoom, setImageZoom] = useState(100);
  const [feedbackDialogOpen, setFeedbackDialogOpen] = useState(false);
  const [feedbackQuestion, setFeedbackQuestion] = useState(null);
  const [feedbackForm, setFeedbackForm] = useState({
    feedback_type: "question_grading",
    teacher_expected_grade: "",
    teacher_correction: ""
  });
  const [submittingFeedback, setSubmittingFeedback] = useState(false);

  useEffect(() => {
    fetchData();
  }, []);

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setFilters(prev => ({ ...prev, search: searchInput }));
    }, 300);
    return () => clearTimeout(timer);
  }, [searchInput]);

  const fetchData = async () => {
    try {
      const [submissionsRes, examsRes, batchesRes] = await Promise.all([
        axios.get(`${API}/submissions`),
        axios.get(`${API}/exams`),
        axios.get(`${API}/batches`)
      ]);
      setSubmissions(submissionsRes.data);
      setExams(examsRes.data);
      setBatches(batchesRes.data);
    } catch (error) {
      console.error("Error fetching data:", error);
    } finally {
      setLoading(false);
    }
  };

  const fetchSubmissionDetails = useCallback(async (submissionId) => {
    try {
      const response = await axios.get(`${API}/submissions/${submissionId}`);
      setSelectedSubmission(response.data);
      setDialogOpen(true);
      
      // Fetch exam to get model answer, question paper and questions
      if (response.data.exam_id) {
        const examResponse = await axios.get(`${API}/exams/${response.data.exam_id}`);
        setModelAnswerImages(examResponse.data.model_answer_images || []);
        setQuestionPaperImages(examResponse.data.question_paper_images || []);
        setExamQuestions(examResponse.data.questions || []);
      }
    } catch (error) {
      toast.error("Failed to load submission details");
    }
  }, []);

  const handleSaveChanges = async () => {
    if (!selectedSubmission) return;
    
    setSaving(true);
    try {
      await axios.put(`${API}/submissions/${selectedSubmission.submission_id}`, {
        question_scores: selectedSubmission.question_scores
      });
      toast.success("Changes saved");
      
      setSubmissions(prev => prev.map(s => 
        s.submission_id === selectedSubmission.submission_id 
          ? { ...s, status: "teacher_reviewed" }
          : s
      ));
    } catch (error) {
      toast.error("Failed to save changes");
    } finally {
      setSaving(false);
    }
  };

  const updateQuestionScore = (index, field, value) => {
    setSelectedSubmission(prev => {
      const newScores = [...prev.question_scores];
      newScores[index] = { ...newScores[index], [field]: value };
      
      const totalScore = newScores.reduce((sum, qs) => sum + qs.obtained_marks, 0);
      const exam = exams.find(e => e.exam_id === prev.exam_id);
      const totalMarks = exam?.total_marks || 100;
      
      return {
        ...prev,
        question_scores: newScores,
        total_score: totalScore,
        percentage: Math.round((totalScore / totalMarks) * 100 * 100) / 100
      };
    });
  };

  const filteredSubmissions = useMemo(() => {
    return submissions.filter(s => {
      // Filter by batch
      if (filters.batch_id) {
        const exam = exams.find(e => e.exam_id === s.exam_id);
        if (!exam || exam.batch_id !== filters.batch_id) return false;
      }
      // Filter by exam
      if (filters.exam_id && s.exam_id !== filters.exam_id) return false;
      // Filter by search
      if (filters.search && !s.student_name.toLowerCase().includes(filters.search.toLowerCase())) return false;
      return true;
    });
  }, [submissions, filters, exams]);

  const handleBulkApprove = async () => {
    if (!filters.exam_id) {
      toast.error("Please select an exam first");
      return;
    }

    const unreviewedCount = filteredSubmissions.filter(s => s.status !== "teacher_reviewed").length;
    
    if (unreviewedCount === 0) {
      toast.info("All papers are already reviewed");
      return;
    }

    if (!confirm(`Mark ${unreviewedCount} unreviewed papers as approved?\n\nThis will:\n- Mark all papers as "teacher_reviewed"\n- Keep existing scores and feedback\n- Skip papers already reviewed`)) {
      return;
    }

    try {
      const response = await axios.post(`${API}/exams/${filters.exam_id}/bulk-approve`);
      toast.success(response.data.message || "Papers approved successfully");
      await fetchData(); // Refresh all data
    } catch (error) {
      console.error("Bulk approve error:", error);
      const errorMessage = error.response?.data?.detail || error.message || "Failed to bulk approve";
      toast.error(errorMessage);
    }
  };

  const currentIndex = selectedSubmission 
    ? filteredSubmissions.findIndex(s => s.submission_id === selectedSubmission.submission_id)
    : -1;

  const handleUnapprove = async () => {
    if (!selectedSubmission) return;
    
    try {
      await axios.put(`${API}/submissions/${selectedSubmission.submission_id}/unapprove`);
      toast.success("Submission reverted to pending review");
      
      // Refresh data
      await fetchData();
      
      // Update current submission
      setSelectedSubmission(prev => ({
        ...prev,
        status: "pending_review",
        is_reviewed: false
      }));
    } catch (error) {
      console.error("Unapprove error:", error);
      toast.error(error.response?.data?.detail || "Failed to unapprove");
    }
  };

  const navigatePaper = (direction) => {
    const newIndex = currentIndex + direction;
    if (newIndex >= 0 && newIndex < filteredSubmissions.length) {
      fetchSubmissionDetails(filteredSubmissions[newIndex].submission_id);
    }
  };

  const openFeedbackDialog = (questionScore) => {
    setFeedbackQuestion(questionScore);
    setFeedbackForm({
      feedback_type: "question_grading",
      teacher_expected_grade: questionScore.obtained_marks.toString(),
      teacher_correction: ""
    });
    setFeedbackDialogOpen(true);
  };

  const handleSubmitFeedback = async () => {
    if (!feedbackQuestion || !feedbackForm.teacher_correction.trim()) {
      toast.error("Please provide your feedback");
      return;
    }

    setSubmittingFeedback(true);
    try {
      await axios.post(`${API}/feedback/submit`, {
        submission_id: selectedSubmission?.submission_id,
        question_number: feedbackQuestion.question_number,
        feedback_type: feedbackForm.feedback_type,
        question_text: feedbackQuestion.question_text || "",
        ai_grade: feedbackQuestion.obtained_marks,
        ai_feedback: feedbackQuestion.ai_feedback,
        teacher_expected_grade: parseFloat(feedbackForm.teacher_expected_grade) || feedbackQuestion.obtained_marks,
        teacher_correction: feedbackForm.teacher_correction
      });
      
      toast.success("Feedback submitted successfully! This will help improve AI grading.");
      setFeedbackDialogOpen(false);
      setFeedbackQuestion(null);
      setFeedbackForm({
        feedback_type: "question_grading",
        teacher_expected_grade: "",
        teacher_correction: ""
      });
    } catch (error) {
      console.error("Feedback submission error:", error);
      toast.error(error.response?.data?.detail || "Failed to submit feedback");
    } finally {
      setSubmittingFeedback(false);
    }
  };

  // Memoize DetailContent to prevent recreation on every render
  // This fixes the textarea editing issue
  const DetailContent = useMemo(() => {
    if (!selectedSubmission) return null;
    
    return (
    <>
      {/* Header */}
      <div className="p-3 md:p-4 border-b bg-gradient-to-r from-primary/5 to-transparent">
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-3 mb-3">
          <div className="min-w-0 flex-1">
            <h3 className="text-lg md:text-xl font-semibold truncate">{selectedSubmission.student_name}</h3>
            <p className="text-xs md:text-sm text-muted-foreground">
              Score: {selectedSubmission.total_score?.toFixed(1)} / {exams.find(e => e.exam_id === selectedSubmission.exam_id)?.total_marks || selectedSubmission.question_scores?.reduce((sum, q) => sum + q.max_marks, 0)} ({selectedSubmission.percentage?.toFixed(1)}%)
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge 
              className={
                selectedSubmission.status === "teacher_reviewed" 
                  ? "bg-green-100 text-green-700" 
                  : "bg-yellow-100 text-yellow-700"
              }
            >
              {selectedSubmission.status === "teacher_reviewed" ? "Reviewed" : "AI Graded"}
            </Badge>
            {selectedSubmission.status === "teacher_reviewed" && (
              <Button
                variant="outline"
                size="sm"
                onClick={handleUnapprove}
                className="text-orange-600 border-orange-300 hover:bg-orange-50"
              >
                <RefreshCw className="w-3 h-3 mr-1" />
                Unapprove
              </Button>
            )}
            {modelAnswerImages.length > 0 && (
              <Button
                variant={showModelAnswer ? "default" : "outline"}
                size="sm"
                onClick={() => setShowModelAnswer(!showModelAnswer)}
              >
                <FileText className="w-3 h-3 md:w-4 md:h-4 mr-1" />
                <span className="hidden sm:inline">{showModelAnswer ? "Hide" : "Show"} Model</span>
                <span className="sm:hidden">Model</span>
              </Button>
            )}
          </div>
        </div>
        
        {/* Navigation */}
        <div className="flex items-center justify-between">
          <Button
            variant="outline"
            size="sm"
            onClick={() => navigatePaper(-1)}
            disabled={currentIndex === 0}
          >
            <ChevronLeft className="w-4 h-4 mr-1" />
            Previous
          </Button>
          <span className="text-sm text-muted-foreground">
            {currentIndex + 1} of {filteredSubmissions.length}
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => navigatePaper(1)}
            disabled={currentIndex === filteredSubmissions.length - 1}
          >
            Next
            <ChevronRight className="w-4 h-4 ml-1" />
          </Button>
        </div>
      </div>

      {/* Content - Resizable Panels for Desktop */}
      <div className="flex-1 overflow-hidden flex flex-col lg:hidden">
        {/* Mobile: Stack vertically without resize */}
        <div className="h-48 border-b overflow-auto bg-muted/30 p-2">
          {selectedSubmission.file_images?.length > 0 ? (
            <div className="space-y-3">
              {/* Toggle Controls */}
              <div className="flex items-center justify-between sticky top-0 bg-muted/30 py-2 z-10 gap-2 flex-wrap">
                <span className="text-sm font-medium">Answer Sheet</span>
                <div className="flex items-center gap-3">
                  {/* Model Answer Toggle */}
                  {modelAnswerImages.length > 0 && (
                    <div className="flex items-center gap-2">
                      <Checkbox 
                        id="show-model-answer-mobile"
                        checked={showModelAnswer}
                        onCheckedChange={setShowModelAnswer}
                      />
                      <Label htmlFor="show-model-answer-mobile" className="text-xs cursor-pointer flex items-center gap-1">
                        <FileText className="w-3 h-3" />
                        Model
                      </Label>
                    </div>
                  )}
                  
                  {/* Mistakes Toggle */}
                  <div className="flex items-center gap-2">
                    <Checkbox 
                      id="show-annotations-mobile"
                      checked={showAnnotations}
                      onCheckedChange={setShowAnnotations}
                    />
                    <Label htmlFor="show-annotations-mobile" className="text-xs cursor-pointer flex items-center gap-1">
                      {showAnnotations ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
                      Mistakes
                    </Label>
                  </div>
                </div>
              </div>
              
              {/* Answer Sheets */}
              <div className="space-y-4">
                {selectedSubmission.file_images.map((img, idx) => (
                  <img 
                    key={idx}
                    src={`data:image/jpeg;base64,${img}`}
                    alt={`Page ${idx + 1}`}
                    className="w-full rounded-lg shadow-md cursor-zoom-in"
                    onClick={() => setZoomedImage({ src: `data:image/jpeg;base64,${img}`, title: `Student Answer - Page ${idx + 1}` })}
                  />
                ))}
              </div>
            </div>
          ) : (
            <div className="h-full flex items-center justify-center">
              <p className="text-muted-foreground text-sm">No preview available</p>
            </div>
          )}
        </div>
        
        {/* Mobile: Questions Section */}
        <ScrollArea className="flex-1">
          <div className="p-4 space-y-3">
            {selectedSubmission.question_scores?.map((qs, index) => (
              <div 
                key={index}
                className={`p-3 rounded-lg border question-card ${qs.is_reviewed ? "reviewed" : ""}`}
              >
                <div className="flex items-center justify-between mb-2">
                  <h4 className="font-semibold text-sm">Question {qs.question_number}</h4>
                  <div className="flex items-center gap-1">
                    <Input 
                      type="number"
                      value={qs.obtained_marks}
                      onChange={(e) => updateQuestionScore(index, "obtained_marks", parseFloat(e.target.value) || 0)}
                      className="w-16 text-center text-sm"
                    />
                    <span className="text-muted-foreground text-sm">/ {qs.max_marks}</span>
                  </div>
                </div>

                {qs.question_text && (
                  <div className="mb-3 p-2 bg-muted/50 rounded border-l-2 border-primary">
                    <p className="text-xs text-foreground whitespace-pre-wrap">
                      <strong>Q{qs.question_number}.</strong> {qs.question_text}
                    </p>
                  </div>
                )}

                <div className="space-y-2">
                  <div>
                    <Label className="text-xs text-muted-foreground">AI Feedback</Label>
                    <Textarea 
                      value={qs.ai_feedback}
                      onChange={(e) => updateQuestionScore(index, "ai_feedback", e.target.value)}
                      className="mt-1 text-xs"
                      rows={2}
                    />
                  </div>

                  <div>
                    <Label className="text-xs text-muted-foreground">Teacher Comment</Label>
                    <Textarea 
                      value={qs.teacher_comment || ""}
                      onChange={(e) => updateQuestionScore(index, "teacher_comment", e.target.value)}
                      placeholder="Add your comments..."
                      className="mt-1 text-xs"
                      rows={2}
                    />
                  </div>

                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <div className="flex items-center gap-2">
                      <Checkbox 
                        id={`reviewed-mobile-${index}`}
                        checked={qs.is_reviewed}
                        onCheckedChange={(checked) => updateQuestionScore(index, "is_reviewed", checked)}
                      />
                      <Label htmlFor={`reviewed-mobile-${index}`} className="text-xs cursor-pointer">
                        Mark as reviewed
                      </Label>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => openFeedbackDialog(qs)}
                      className="text-xs text-orange-600 hover:text-orange-700 hover:bg-orange-50"
                    >
                      <MessageSquarePlus className="w-3 h-3 mr-1" />
                      Improve AI
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>
      </div>

      {/* Desktop: Resizable Panels */}
      <div className="hidden lg:flex flex-1 overflow-hidden" style={{ height: 'calc(100% - 60px)' }}>
        <PanelGroup direction="horizontal" className="h-full">
          {/* Left Panel - Answer Sheets */}
          <Panel defaultSize={55} minSize={30} maxSize={70} className="h-full">
            <div className="h-full overflow-auto bg-muted/30 p-4">
              {selectedSubmission.file_images?.length > 0 ? (
                <div className="space-y-3">
                  {/* Toggle Controls */}
                  <div className="flex items-center justify-between sticky top-0 bg-muted/30 py-2 z-10 gap-2 flex-wrap">
                    <span className="text-sm font-medium">Answer Sheet</span>
                    <div className="flex items-center gap-3">
                      {/* Question Paper Toggle */}
                      {questionPaperImages.length > 0 && (
                        <div className="flex items-center gap-2">
                          <Checkbox 
                            id="show-question-paper"
                            checked={showQuestionPaper}
                            onCheckedChange={setShowQuestionPaper}
                          />
                          <Label htmlFor="show-question-paper" className="text-xs cursor-pointer flex items-center gap-1">
                            <FileText className="w-3 h-3 text-blue-600" />
                            Questions
                          </Label>
                        </div>
                      )}
                      
                      {/* Model Answer Toggle */}
                      {modelAnswerImages.length > 0 && (
                        <div className="flex items-center gap-2">
                          <Checkbox 
                            id="show-model-answer"
                            checked={showModelAnswer}
                            onCheckedChange={setShowModelAnswer}
                          />
                          <Label htmlFor="show-model-answer" className="text-xs cursor-pointer flex items-center gap-1">
                            <FileText className="w-3 h-3" />
                            Model Answer
                          </Label>
                        </div>
                      )}
                      
                      {/* Mistakes Toggle */}
                      <div className="flex items-center gap-2">
                        <Checkbox 
                          id="show-annotations"
                          checked={showAnnotations}
                          onCheckedChange={setShowAnnotations}
                        />
                        <Label htmlFor="show-annotations" className="text-xs cursor-pointer flex items-center gap-1">
                          {showAnnotations ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
                          Show Errors
                        </Label>
                      </div>
                    </div>
                  </div>
                  
                  {/* Error Legend */}
                  {showAnnotations && (
                    <div className="flex items-center gap-4 text-xs bg-muted/50 p-2 rounded-lg">
                      <span className="font-medium">Error Severity:</span>
                      <div className="flex items-center gap-1">
                        <div className="w-3 h-3 rounded bg-red-500" />
                        <span>Major</span>
                      </div>
                      <div className="flex items-center gap-1">
                        <div className="w-3 h-3 rounded bg-orange-500" />
                        <span>Moderate</span>
                      </div>
                      <div className="flex items-center gap-1">
                        <div className="w-3 h-3 rounded bg-yellow-500" />
                        <span>Minor</span>
                      </div>
                    </div>
                  )}
                  
                  {/* Answer Sheets Display */}
                  <div className={showModelAnswer ? "grid grid-cols-2 gap-4" : ""}>
                    {/* Student Answer */}
                    <div className="space-y-2">
                      {showModelAnswer && (
                        <h3 className="text-xs font-semibold text-blue-700 sticky top-0 bg-muted/30 py-1">Student&apos;s Answer</h3>
                      )}
                      <div className="space-y-4">
                        {selectedSubmission.file_images.map((img, idx) => (
                          <div key={idx} className="relative group">
                            <div 
                              className="relative cursor-zoom-in hover:shadow-xl transition-shadow"
                              onClick={() => setZoomedImage({ src: `data:image/jpeg;base64,${img}`, title: `Student Answer - Page ${idx + 1}` })}
                            >
                              <img 
                                src={`data:image/jpeg;base64,${img}`}
                                alt={`Page ${idx + 1}`}
                                className="w-full rounded-lg shadow-md"
                                style={{ minHeight: '400px', objectFit: 'contain' }}
                              />
                              {/* Zoom Overlay */}
                              <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-all rounded-lg flex items-center justify-center opacity-0 group-hover:opacity-100">
                                <div className="bg-white/90 px-3 py-2 rounded-lg flex items-center gap-2">
                                  <Maximize2 className="w-4 h-4" />
                                  <span className="text-sm font-medium">Click to enlarge</span>
                                </div>
                              </div>
                              
                              {/* Error Annotations Overlay */}
                              {showAnnotations && (
                                <>
                                  {selectedSubmission.question_scores?.flatMap((qs) => 
                                    (qs.error_annotations || [])
                                      .filter(ann => ann.page === idx + 1)
                                      .map((ann, annIdx) => {
                                        const regionTop = ann.region === 'top' ? '5%' : ann.region === 'middle' ? '38%' : '70%';
                                        const severityColor = ann.severity === 'major' ? 'bg-red-500' : ann.severity === 'moderate' ? 'bg-orange-500' : 'bg-yellow-500';
                                        return (
                                          <div
                                            key={`${qs.question_number}-${annIdx}`}
                                            className={`absolute left-2 right-2 h-[28%] ${severityColor}/20 border-2 ${severityColor.replace('bg-', 'border-')} rounded-lg pointer-events-none`}
                                            style={{ top: regionTop }}
                                            title={`Q${qs.question_number}: ${ann.description}`}
                                          >
                                            <div className={`absolute -top-3 left-2 ${severityColor} text-white text-xs px-2 py-0.5 rounded shadow-lg`}>
                                              Q{qs.question_number}: {ann.error_type.replace('_', ' ')}
                                            </div>
                                          </div>
                                        );
                                      })
                                  )}
                                </>
                              )}
                            </div>
                            {/* Side Annotation Indicator */}
                            {showAnnotations && (
                              <div className="absolute right-2 top-2 bottom-2 w-12 flex flex-col justify-start py-2 gap-1 overflow-y-auto">
                                {selectedSubmission.question_scores?.map((qs) => {
                                  const scorePercentage = (qs.obtained_marks / qs.max_marks) * 100;
                                  const hasErrors = (qs.error_annotations || []).length > 0;
                                  if (scorePercentage < 60 || hasErrors) {
                                    const severityColor = scorePercentage < 40 ? 'bg-red-500' : scorePercentage < 60 ? 'bg-orange-500' : 'bg-yellow-500';
                                    return (
                                      <div 
                                        key={qs.question_number}
                                        className={`${severityColor} text-white text-xs px-1 py-1 rounded shadow-lg flex flex-col items-center justify-center cursor-pointer hover:scale-105 transition-transform`}
                                        title={`Q${qs.question_number}: ${qs.ai_feedback?.slice(0, 100)}...`}
                                      >
                                        <span className="font-bold text-[10px]">Q{qs.question_number}</span>
                                        <span className="text-[9px]">{qs.obtained_marks}/{qs.max_marks}</span>
                                        {hasErrors && <span className="text-[8px]">⚠️</span>}
                                      </div>
                                    );
                                  }
                                  return null;
                                })}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>

                    {/* Model Answer */}
                    {showModelAnswer && modelAnswerImages.length > 0 && (
                      <div className="space-y-2">
                        <h3 className="text-xs font-semibold text-green-700 sticky top-0 bg-muted/30 py-1">Model Answer (Correct)</h3>
                        <div className="space-y-4">
                          {modelAnswerImages.map((img, idx) => (
                            <div key={idx} className="relative group">
                              <div 
                                className="relative cursor-zoom-in hover:shadow-xl transition-shadow"
                                onClick={() => setZoomedImage({ src: `data:image/jpeg;base64,${img}`, title: `Model Answer - Page ${idx + 1}` })}
                              >
                                <img 
                                  src={`data:image/jpeg;base64,${img}`}
                                  alt={`Model Page ${idx + 1}`}
                                  className="w-full rounded-lg shadow-md border-2 border-green-200"
                                  style={{ minHeight: '400px', objectFit: 'contain' }}
                                />
                                {/* Zoom Overlay */}
                                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-all rounded-lg flex items-center justify-center opacity-0 group-hover:opacity-100">
                                  <div className="bg-white/90 px-3 py-2 rounded-lg flex items-center gap-2">
                                    <Maximize2 className="w-4 h-4" />
                                    <span className="text-sm font-medium">Click to enlarge</span>
                                  </div>
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                    
                    {/* Question Paper */}
                    {showQuestionPaper && questionPaperImages.length > 0 && (
                      <div className="space-y-2">
                        <h3 className="text-xs font-semibold text-blue-700 sticky top-0 bg-muted/30 py-1">Question Paper</h3>
                        <div className="space-y-4">
                          {questionPaperImages.map((img, idx) => (
                            <div key={idx} className="relative group">
                              <div 
                                className="relative cursor-zoom-in hover:shadow-xl transition-shadow"
                                onClick={() => setZoomedImage({ src: `data:image/jpeg;base64,${img}`, title: `Question Paper - Page ${idx + 1}` })}
                              >
                                <img 
                                  src={`data:image/jpeg;base64,${img}`}
                                  alt={`Question Paper Page ${idx + 1}`}
                                  className="w-full rounded-lg shadow-md border-2 border-blue-200"
                                  style={{ minHeight: '400px', objectFit: 'contain' }}
                                />
                                {/* Zoom Overlay */}
                                <div className="absolute inset-0 bg-black/0 group-hover:bg-black/10 transition-all rounded-lg flex items-center justify-center opacity-0 group-hover:opacity-100">
                                  <div className="bg-white/90 px-3 py-2 rounded-lg flex items-center gap-2">
                                    <Maximize2 className="w-4 h-4" />
                                    <span className="text-sm font-medium">Click to enlarge</span>
                                  </div>
                                </div>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="h-full flex items-center justify-center">
                  <p className="text-muted-foreground text-sm">No preview available</p>
                </div>
              )}
            </div>
          </Panel>

          {/* Resize Handle */}
          <PanelResizeHandle className="w-3 bg-border hover:bg-orange-300 transition-colors cursor-col-resize flex items-center justify-center group">
            <div className="w-1 h-12 bg-muted-foreground/40 group-hover:bg-orange-500 rounded-full transition-colors" />
          </PanelResizeHandle>

          {/* Right Panel - Questions Breakdown */}
          <Panel defaultSize={45} minSize={30} maxSize={70}>
            <ScrollArea className="h-full">
              <div className="p-4 space-y-3">
                {selectedSubmission.question_scores?.map((qs, index) => (
                  <div 
                    key={index}
                    className={`p-3 lg:p-4 rounded-lg border question-card ${
                      qs.is_reviewed ? "reviewed" : ""
                    }`}
                  >
                    <div className="flex items-center justify-between mb-2 lg:mb-3">
                      <h4 className="font-semibold text-sm lg:text-base">Question {qs.question_number}</h4>
                      <div className="flex items-center gap-1 lg:gap-2">
                        <Input 
                          type="number"
                          value={qs.obtained_marks}
                          onChange={(e) => updateQuestionScore(index, "obtained_marks", parseFloat(e.target.value) || 0)}
                          className="w-16 lg:w-20 text-center text-sm"
                          data-testid={`score-q${qs.question_number}`}
                        />
                        <span className="text-muted-foreground text-sm">/ {qs.max_marks}</span>
                      </div>
                    </div>

                    {/* Full Question Text - from exam questions or submission */}
                    {(() => {
                      const examQuestion = examQuestions.find(q => q.question_number === qs.question_number);
                      const questionText = qs.question_text || examQuestion?.rubric || examQuestion?.question_text;
                      
                      // If no question text, show AI's assessment as a fallback
                      if (!questionText && qs.ai_feedback) {
                        return (
                          <div className="mb-3 p-3 bg-blue-50/50 rounded border-l-4 border-blue-300">
                            <p className="text-xs font-medium text-blue-800 mb-1">Question {qs.question_number} (from AI assessment):</p>
                            <p className="text-xs text-gray-700 line-clamp-3">{qs.ai_feedback.slice(0, 200)}...</p>
                            <p className="text-xs text-muted-foreground italic mt-1">
                              Note: View model answer or use "Extract Questions" in Manage Exams for full question text
                            </p>
                          </div>
                        );
                      }
                      
                      return questionText ? (
                        <div className="mb-3 p-2 bg-blue-50 rounded border-l-4 border-blue-500">
                          <p className="text-xs lg:text-sm text-foreground whitespace-pre-wrap">
                            <strong className="text-blue-700">Q{qs.question_number}:</strong> {questionText}
                          </p>
                        </div>
                      ) : (
                        <div className="mb-3 p-2 bg-amber-50 rounded border-l-2 border-amber-400">
                          <p className="text-xs text-amber-800 font-medium">⚠️ Question {qs.question_number}</p>
                          <p className="text-xs text-muted-foreground mt-1">
                            Question text not available. To see full questions:
                          </p>
                          <ul className="text-xs text-muted-foreground mt-1 ml-4 list-disc">
                            <li>View model answer (left panel)</li>
                            <li>Or go to Manage Exams → Select exam → Click "Extract Questions"</li>
                          </ul>
                        </div>
                      );
                    })()}

                    <div className="space-y-2 lg:space-y-3">
                      <div>
                        <Label className="text-xs lg:text-sm text-muted-foreground">AI Feedback</Label>
                        <Textarea 
                          value={qs.ai_feedback}
                          onChange={(e) => updateQuestionScore(index, "ai_feedback", e.target.value)}
                          className="mt-1 text-xs lg:text-sm"
                          rows={2}
                        />
                      </div>

                      <div>
                        <Label className="text-xs lg:text-sm text-muted-foreground">Teacher Comment</Label>
                        <Textarea 
                          value={qs.teacher_comment || ""}
                          onChange={(e) => updateQuestionScore(index, "teacher_comment", e.target.value)}
                          placeholder="Add your comments..."
                          className="mt-1 text-xs lg:text-sm"
                          rows={2}
                        />
                      </div>

                      <div className="flex items-center justify-between gap-2 flex-wrap">
                        <div className="flex items-center gap-2">
                          <Checkbox 
                            id={`reviewed-desktop-${index}`}
                            checked={qs.is_reviewed}
                            onCheckedChange={(checked) => updateQuestionScore(index, "is_reviewed", checked)}
                          />
                          <Label htmlFor={`reviewed-desktop-${index}`} className="text-xs lg:text-sm cursor-pointer">
                            Mark as reviewed
                          </Label>
                        </div>
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => openFeedbackDialog(qs)}
                          className="text-xs text-orange-600 hover:text-orange-700 hover:bg-orange-50"
                          title="Submit feedback to improve AI grading"
                        >
                          <MessageSquarePlus className="w-3 h-3 mr-1" />
                          Improve AI
                        </Button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          </Panel>
        </PanelGroup>
      </div>

      {/* Footer Actions */}
      <div className="border-t p-3 lg:p-4 flex items-center justify-between bg-muted/30">
        <div className="flex items-center gap-1 lg:gap-2">
          <Button 
            variant="outline" 
            size="sm"
            onClick={() => navigatePaper(-1)}
            disabled={currentIndex <= 0}
            className="px-2 lg:px-3"
          >
            <ChevronLeft className="w-4 h-4" />
            <span className="hidden sm:inline ml-1">Prev</span>
          </Button>
          <Button 
            variant="outline" 
            size="sm"
            onClick={() => navigatePaper(1)}
            disabled={currentIndex >= filteredSubmissions.length - 1}
            className="px-2 lg:px-3"
          >
            <span className="hidden sm:inline mr-1">Next</span>
            <ChevronRight className="w-4 h-4" />
          </Button>
          <span className="text-xs lg:text-sm text-muted-foreground ml-1 lg:ml-2">
            {currentIndex + 1}/{filteredSubmissions.length}
          </span>
        </div>

        <div className="flex items-center gap-1 lg:gap-2">
          <Button 
            variant="outline"
            size="sm"
            onClick={handleSaveChanges}
            disabled={saving}
            data-testid="save-changes-btn"
            className="text-xs lg:text-sm"
          >
            {saving ? <RefreshCw className="w-3 h-3 lg:w-4 lg:h-4 animate-spin" /> : <Save className="w-3 h-3 lg:w-4 lg:h-4" />}
            <span className="ml-1 lg:ml-2">Save</span>
          </Button>
          <Button 
            size="sm"
            onClick={() => {
              handleSaveChanges();
              if (currentIndex < filteredSubmissions.length - 1) {
                navigatePaper(1);
              }
            }}
            disabled={saving}
            data-testid="approve-finalize-btn"
            className="text-xs lg:text-sm"
          >
            <CheckCircle className="w-3 h-3 lg:w-4 lg:h-4" />
            <span className="ml-1 lg:ml-2 hidden sm:inline">Approve</span>
          </Button>
        </div>
      </div>

      {/* Image Zoom Modal */}
      <Dialog open={!!zoomedImage} onOpenChange={() => setZoomedImage(null)}>
        <DialogContent className="max-w-[95vw] max-h-[95vh] p-0">
          <DialogHeader className="p-4 border-b">
            <div className="flex items-center justify-between">
              <DialogTitle>{zoomedImage?.title || "Image Viewer"}</DialogTitle>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setImageZoom(Math.max(50, imageZoom - 25))}
                >
                  <ZoomOut className="w-4 h-4" />
                </Button>
                <span className="text-sm font-medium min-w-[60px] text-center">{imageZoom}%</span>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setImageZoom(Math.min(200, imageZoom + 25))}
                >
                  <ZoomIn className="w-4 h-4" />
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setImageZoom(100)}
                >
                  Reset
                </Button>
              </div>
            </div>
          </DialogHeader>
          <div className="overflow-auto p-4" style={{ maxHeight: 'calc(95vh - 80px)' }}>
            {zoomedImage && (
              <img 
                src={zoomedImage.src}
                alt={zoomedImage.title}
                className="mx-auto"
                style={{ 
                  width: `${imageZoom}%`,
                  transition: 'width 0.2s'
                }}
              />
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* AI Feedback Dialog */}
      <Dialog open={feedbackDialogOpen} onOpenChange={setFeedbackDialogOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Lightbulb className="w-5 h-5 text-orange-500" />
              Improve AI Grading
            </DialogTitle>
          </DialogHeader>
          
          {feedbackQuestion && (
            <div className="space-y-4">
              <div className="p-3 bg-muted/50 rounded-lg">
                <p className="text-sm font-medium mb-1">Question {feedbackQuestion.question_number}</p>
                {feedbackQuestion.question_text && (
                  <p className="text-xs text-muted-foreground line-clamp-2">{feedbackQuestion.question_text}</p>
                )}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label className="text-xs">AI Grade</Label>
                  <div className="p-2 bg-muted rounded text-center font-medium">
                    {feedbackQuestion.obtained_marks} / {feedbackQuestion.max_marks}
                  </div>
                </div>
                <div>
                  <Label className="text-xs">Your Expected Grade</Label>
                  <Input 
                    type="number"
                    min="0"
                    max={feedbackQuestion.max_marks}
                    step="0.5"
                    value={feedbackForm.teacher_expected_grade}
                    onChange={(e) => setFeedbackForm(prev => ({ ...prev, teacher_expected_grade: e.target.value }))}
                    className="text-center"
                  />
                </div>
              </div>

              <div>
                <Label className="text-xs">AI&apos;s Feedback</Label>
                <div className="p-2 bg-muted/50 rounded text-xs text-muted-foreground max-h-20 overflow-y-auto">
                  {feedbackQuestion.ai_feedback || "No AI feedback available"}
                </div>
              </div>

              <div>
                <Label className="text-xs">Feedback Type</Label>
                <Select 
                  value={feedbackForm.feedback_type}
                  onValueChange={(v) => setFeedbackForm(prev => ({ ...prev, feedback_type: v }))}
                >
                  <SelectTrigger className="text-sm">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="question_grading">Grading Issue</SelectItem>
                    <SelectItem value="correction">AI Mistake</SelectItem>
                    <SelectItem value="general_suggestion">General Suggestion</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div>
                <Label className="text-xs">Your Correction / Feedback *</Label>
                <Textarea 
                  value={feedbackForm.teacher_correction}
                  onChange={(e) => setFeedbackForm(prev => ({ ...prev, teacher_correction: e.target.value }))}
                  placeholder="Explain what the AI got wrong and how it should grade this type of answer..."
                  rows={3}
                  className="text-sm"
                />
                <p className="text-xs text-muted-foreground mt-1">
                  Your feedback helps train the AI to grade more accurately.
                </p>
              </div>

              <div className="flex justify-end gap-2 pt-2">
                <Button 
                  variant="outline" 
                  onClick={() => setFeedbackDialogOpen(false)}
                  disabled={submittingFeedback}
                >
                  Cancel
                </Button>
                <Button 
                  onClick={handleSubmitFeedback}
                  disabled={submittingFeedback || !feedbackForm.teacher_correction.trim()}
                  className="bg-orange-500 hover:bg-orange-600"
                >
                  {submittingFeedback ? (
                    <RefreshCw className="w-4 h-4 animate-spin mr-2" />
                  ) : (
                    <Send className="w-4 h-4 mr-2" />
                  )}
                  Submit Feedback
                </Button>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </>
  );
  }, [
    selectedSubmission, 
    exams, 
    modelAnswerImages, 
    questionPaperImages,
    showModelAnswer, 
    showQuestionPaper,
    showAnnotations,
    examQuestions,
    imageZoom,
    zoomedImage,
    feedbackDialogOpen,
    feedbackQuestion,
    feedbackForm,
    submittingFeedback
  ]);

  return (
    <Layout user={user}>
      <div className="space-y-4" data-testid="review-papers-page">
        {/* Submissions List - Full Width */}
        <div className="max-w-7xl mx-auto">
          {/* Submissions List */}
          <Card className="flex flex-col">
            <CardHeader className="p-3 lg:p-4 pb-2 lg:pb-3">
              <div className="flex items-center justify-between">
                <CardTitle className="text-base lg:text-lg">Papers to Review</CardTitle>
                {filters.exam_id && filteredSubmissions.length > 0 && (
                  <Button 
                    onClick={handleBulkApprove}
                    size="sm"
                    className="bg-green-600 hover:bg-green-700 text-xs"
                    data-testid="bulk-approve-btn"
                  >
                    <CheckCircle2 className="w-3 h-3 mr-1" />
                    Approve All
                  </Button>
                )}
              </div>
                
                {/* Filters */}
                <div className="space-y-2 mt-2 lg:mt-3">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <Input 
                      placeholder="Search student..."
                      value={searchInput}
                      onChange={(e) => setSearchInput(e.target.value)}
                      className="pl-9 text-sm"
                      data-testid="search-input"
                    />
                  </div>
                  
                  {/* Batch Filter */}
                  <Select 
                    value={filters.batch_id || "all"} 
                    onValueChange={(v) => setFilters(prev => ({ ...prev, batch_id: v === "all" ? "" : v, exam_id: "" }))}
                  >
                    <SelectTrigger data-testid="batch-filter" className="text-sm">
                      <SelectValue placeholder="Filter by batch" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All Batches</SelectItem>
                      {batches.map(batch => (
                        <SelectItem key={batch.batch_id} value={batch.batch_id}>
                          {batch.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  
                  {/* Exam Filter */}
                  <Select 
                    value={filters.exam_id || "all"} 
                    onValueChange={(v) => setFilters(prev => ({ ...prev, exam_id: v === "all" ? "" : v }))}
                  >
                    <SelectTrigger data-testid="exam-filter" className="text-sm">
                      <SelectValue placeholder="Filter by exam" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All Exams</SelectItem>
                      {exams
                        .filter(exam => !filters.batch_id || exam.batch_id === filters.batch_id)
                        .map(exam => (
                          <SelectItem key={exam.exam_id} value={exam.exam_id}>
                            {exam.exam_name}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </div>
              </CardHeader>
              
              <CardContent className="flex-1 overflow-hidden p-0">
                <ScrollArea className="h-[300px] lg:h-full px-3 lg:px-4 pb-3 lg:pb-4">
                  {loading ? (
                    <div className="space-y-2">
                      {[1, 2, 3, 4, 5].map(i => (
                        <div key={i} className="h-16 lg:h-20 bg-muted animate-pulse rounded-lg" />
                      ))}
                    </div>
                  ) : filteredSubmissions.length === 0 ? (
                    <div className="text-center py-6 lg:py-8">
                      <FileText className="w-10 h-10 lg:w-12 lg:h-12 mx-auto text-muted-foreground/50 mb-3" />
                      <p className="text-sm text-muted-foreground">No submissions found</p>
                    </div>
                  ) : (
                    <div className="space-y-2">
                      {filteredSubmissions.map((submission) => (
                        <div 
                          key={submission.submission_id}
                          onClick={() => fetchSubmissionDetails(submission.submission_id)}
                          className={`p-3 lg:p-4 rounded-lg border cursor-pointer transition-all ${
                            selectedSubmission?.submission_id === submission.submission_id
                              ? "border-primary bg-primary/5"
                              : "border-border hover:border-primary/50 hover:bg-muted/50"
                          }`}
                          data-testid={`submission-${submission.submission_id}`}
                        >
                          <div className="flex items-center justify-between">
                            <div className="min-w-0">
                              <p className="font-medium text-sm lg:text-base truncate">{submission.student_name}</p>
                              <p className="text-xs lg:text-sm text-muted-foreground truncate">
                                {submission.exam_name || "Unknown Exam"}
                              </p>
                            </div>
                            <div className="text-right flex-shrink-0 ml-2">
                              <p className="font-bold text-base lg:text-lg">
                                {submission.total_score}
                                <span className="text-xs lg:text-sm font-normal text-muted-foreground">
                                  /{exams.find(e => e.exam_id === submission.exam_id)?.total_marks || 100}
                                </span>
                              </p>
                              <Badge 
                                variant="secondary"
                                className={`text-xs ${
                                  submission.status === "teacher_reviewed" 
                                    ? "bg-green-100 text-green-700" 
                                    : "bg-yellow-100 text-yellow-700"
                                }`}
                              >
                                {submission.status === "teacher_reviewed" ? "Done" : "Review"}
                              </Badge>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </ScrollArea>
              </CardContent>
            </Card>
          </div>

          {/* Review Dialog - Full Screen */}
          <Dialog open={dialogOpen && !!selectedSubmission} onOpenChange={setDialogOpen}>
            <DialogContent className="max-w-[98vw] w-full max-h-[95vh] h-[95vh] p-0 flex flex-col">
              {selectedSubmission && DetailContent}
            </DialogContent>
          </Dialog>
        </div>
      </Layout>
    );
  }
