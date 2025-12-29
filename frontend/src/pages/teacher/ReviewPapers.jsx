import { useState, useEffect } from "react";
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
  EyeOff
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
  const [saving, setSaving] = useState(false);
  const [mobileDetailOpen, setMobileDetailOpen] = useState(false);
  const [showAnnotations, setShowAnnotations] = useState(false);

  useEffect(() => {
    fetchData();
  }, []);

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

  const fetchSubmissionDetails = async (submissionId) => {
    try {
      const response = await axios.get(`${API}/submissions/${submissionId}`);
      setSelectedSubmission(response.data);
      setMobileDetailOpen(true);
    } catch (error) {
      toast.error("Failed to load submission details");
    }
  };

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

  const filteredSubmissions = submissions.filter(s => {
    if (filters.exam_id && s.exam_id !== filters.exam_id) return false;
    if (filters.search && !s.student_name.toLowerCase().includes(filters.search.toLowerCase())) return false;
    return true;
  });

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

  const navigatePaper = (direction) => {
    const newIndex = currentIndex + direction;
    if (newIndex >= 0 && newIndex < filteredSubmissions.length) {
      fetchSubmissionDetails(filteredSubmissions[newIndex].submission_id);
    }
  };

  const DetailContent = () => (
    <>
      {/* Header */}
      <div className="p-4 border-b">
        <div className="flex items-center justify-between">
          <div className="min-w-0 flex-1">
            <h3 className="text-lg font-semibold truncate">{selectedSubmission.student_name}</h3>
            <p className="text-sm text-muted-foreground">
              Score: {selectedSubmission.total_score} ({selectedSubmission.percentage}%)
            </p>
          </div>
          <Badge 
            className={
              selectedSubmission.status === "teacher_reviewed" 
                ? "bg-green-100 text-green-700" 
                : "bg-yellow-100 text-yellow-700"
            }
          >
            {selectedSubmission.status === "teacher_reviewed" ? "Reviewed" : "AI Graded"}
          </Badge>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden flex flex-col lg:flex-row">
        {/* PDF Viewer */}
        <div className="h-48 lg:h-auto lg:flex-1 border-b lg:border-b-0 lg:border-r overflow-auto bg-muted/30 p-2 lg:p-4">
          {selectedSubmission.file_images?.length > 0 ? (
            <div className="space-y-3">
              {/* Annotation Toggle */}
              <div className="flex items-center justify-between sticky top-0 bg-muted/30 py-2 z-10">
                <span className="text-sm font-medium">Answer Sheet</span>
                <div className="flex items-center gap-2">
                  <Checkbox 
                    id="show-annotations"
                    checked={showAnnotations}
                    onCheckedChange={setShowAnnotations}
                  />
                  <Label htmlFor="show-annotations" className="text-xs cursor-pointer flex items-center gap-1">
                    {showAnnotations ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
                    Show Mistakes
                  </Label>
                </div>
              </div>
              
              <div className="flex lg:flex-col gap-2 lg:gap-4 overflow-x-auto lg:overflow-x-visible">
                {selectedSubmission.file_images.map((img, idx) => (
                  <div key={idx} className="relative flex-shrink-0">
                    <img 
                      src={`data:image/jpeg;base64,${img}`}
                      alt={`Page ${idx + 1}`}
                      className="h-40 lg:h-auto lg:w-full rounded-lg shadow-md"
                    />
                    {/* Annotation Indicator - Side Labels Only */}
                    {showAnnotations && (
                      <div className="absolute right-0 top-0 bottom-0 w-8 lg:w-12 flex flex-col justify-around py-2 lg:py-4">
                        {selectedSubmission.question_scores?.map((qs) => {
                          const scorePercentage = (qs.obtained_marks / qs.max_marks) * 100;
                          if (scorePercentage < 60) {
                            return (
                              <div 
                                key={qs.question_number}
                                className="bg-red-500 text-white text-[10px] lg:text-xs px-1 lg:px-2 py-1 rounded shadow-lg flex flex-col items-center justify-center"
                                title={`Q${qs.question_number}: Needs Review`}
                              >
                                <span className="font-bold">Q{qs.question_number}</span>
                                <span className="text-[8px] lg:text-[10px]">{qs.obtained_marks}/{qs.max_marks}</span>
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
          ) : (
            <div className="h-full flex items-center justify-center">
              <p className="text-muted-foreground text-sm">No preview available</p>
            </div>
          )}
        </div>

        {/* Question Breakdown */}
        <ScrollArea className="flex-1 lg:w-[45%]">
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

                {/* Full Question Text */}
                {qs.question_text && (
                  <div className="mb-3 p-2 bg-muted/50 rounded border-l-2 border-primary">
                    <p className="text-xs lg:text-sm text-foreground whitespace-pre-wrap">
                      <strong>Q{qs.question_number}.</strong> {qs.question_text}
                    </p>
                  </div>
                )}

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

                  <div className="flex items-center gap-2">
                    <Checkbox 
                      id={`reviewed-${index}`}
                      checked={qs.is_reviewed}
                      onCheckedChange={(checked) => updateQuestionScore(index, "is_reviewed", checked)}
                    />
                    <Label htmlFor={`reviewed-${index}`} className="text-xs lg:text-sm cursor-pointer">
                      Mark as reviewed
                    </Label>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>
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
    </>
  );

  return (
    <Layout user={user}>
      <div className="space-y-4" data-testid="review-papers-page">
        {/* Mobile: Stack layout, Desktop: Side-by-side */}
        <div className="lg:grid lg:grid-cols-12 lg:gap-6 lg:h-[calc(100vh-8rem)]">
          {/* Submissions List */}
          <div className="lg:col-span-4 flex flex-col">
            <Card className="flex-1 flex flex-col">
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
                      value={filters.search}
                      onChange={(e) => setFilters(prev => ({ ...prev, search: e.target.value }))}
                      className="pl-9 text-sm"
                      data-testid="search-input"
                    />
                  </div>
                  <Select 
                    value={filters.exam_id || "all"} 
                    onValueChange={(v) => setFilters(prev => ({ ...prev, exam_id: v === "all" ? "" : v }))}
                  >
                    <SelectTrigger data-testid="exam-filter" className="text-sm">
                      <SelectValue placeholder="Filter by exam" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="all">All Exams</SelectItem>
                      {exams.map(exam => (
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

          {/* Desktop Detail View */}
          <div className="hidden lg:flex lg:col-span-8 flex-col">
            {!selectedSubmission ? (
              <Card className="flex-1 flex items-center justify-center">
                <div className="text-center">
                  <FileText className="w-16 h-16 mx-auto text-muted-foreground/30 mb-4" />
                  <p className="text-lg font-medium text-muted-foreground">Select a paper to review</p>
                  <p className="text-sm text-muted-foreground">Click on a submission from the list</p>
                </div>
              </Card>
            ) : (
              <Card className="flex-1 flex flex-col overflow-hidden">
                <DetailContent />
              </Card>
            )}
          </div>
        </div>

        {/* Mobile Detail Sheet */}
        <Sheet open={mobileDetailOpen && !!selectedSubmission} onOpenChange={setMobileDetailOpen}>
          <SheetContent side="bottom" className="h-[90vh] p-0 flex flex-col lg:hidden">
            <SheetHeader className="p-4 border-b flex-row items-center justify-between">
              <SheetTitle>Review Paper</SheetTitle>
              <Button variant="ghost" size="icon" onClick={() => setMobileDetailOpen(false)}>
                <X className="w-4 h-4" />
              </Button>
            </SheetHeader>
            {selectedSubmission && <DetailContent />}
          </SheetContent>
        </Sheet>
      </div>
    </Layout>
  );
}
