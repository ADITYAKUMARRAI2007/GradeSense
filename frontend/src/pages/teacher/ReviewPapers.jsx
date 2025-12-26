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
import { toast } from "sonner";
import { 
  Search, 
  ChevronLeft, 
  ChevronRight, 
  Download, 
  Save, 
  CheckCircle,
  FileText,
  Filter,
  RefreshCw
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
      
      // Update local state
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
      
      // Recalculate total
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

  const currentIndex = selectedSubmission 
    ? filteredSubmissions.findIndex(s => s.submission_id === selectedSubmission.submission_id)
    : -1;

  const navigatePaper = (direction) => {
    const newIndex = currentIndex + direction;
    if (newIndex >= 0 && newIndex < filteredSubmissions.length) {
      fetchSubmissionDetails(filteredSubmissions[newIndex].submission_id);
    }
  };

  return (
    <Layout user={user}>
      <div className="grid grid-cols-12 gap-6 h-[calc(100vh-8rem)]" data-testid="review-papers-page">
        {/* Left Panel - Submissions List */}
        <div className="col-span-4 flex flex-col">
          <Card className="flex-1 flex flex-col">
            <CardHeader className="pb-3">
              <CardTitle className="text-lg">Papers to Review</CardTitle>
              
              {/* Filters */}
              <div className="space-y-2 mt-3">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input 
                    placeholder="Search by student name..."
                    value={filters.search}
                    onChange={(e) => setFilters(prev => ({ ...prev, search: e.target.value }))}
                    className="pl-9"
                    data-testid="search-input"
                  />
                </div>
                <Select 
                  value={filters.exam_id} 
                  onValueChange={(v) => setFilters(prev => ({ ...prev, exam_id: v }))}
                >
                  <SelectTrigger data-testid="exam-filter">
                    <SelectValue placeholder="Filter by exam" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="">All Exams</SelectItem>
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
              <ScrollArea className="h-full px-4 pb-4">
                {loading ? (
                  <div className="space-y-2">
                    {[1, 2, 3, 4, 5].map(i => (
                      <div key={i} className="h-20 bg-muted animate-pulse rounded-lg" />
                    ))}
                  </div>
                ) : filteredSubmissions.length === 0 ? (
                  <div className="text-center py-8">
                    <FileText className="w-12 h-12 mx-auto text-muted-foreground/50 mb-3" />
                    <p className="text-muted-foreground">No submissions found</p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    {filteredSubmissions.map((submission) => (
                      <div 
                        key={submission.submission_id}
                        onClick={() => fetchSubmissionDetails(submission.submission_id)}
                        className={`p-4 rounded-lg border cursor-pointer transition-all ${
                          selectedSubmission?.submission_id === submission.submission_id
                            ? "border-primary bg-primary/5"
                            : "border-border hover:border-primary/50 hover:bg-muted/50"
                        }`}
                        data-testid={`submission-${submission.submission_id}`}
                      >
                        <div className="flex items-center justify-between">
                          <div>
                            <p className="font-medium">{submission.student_name}</p>
                            <p className="text-sm text-muted-foreground">
                              {submission.exam_name || "Unknown Exam"}
                            </p>
                          </div>
                          <div className="text-right">
                            <p className="font-bold text-lg">
                              {submission.total_score}
                              <span className="text-sm font-normal text-muted-foreground">
                                /{exams.find(e => e.exam_id === submission.exam_id)?.total_marks || 100}
                              </span>
                            </p>
                            <Badge 
                              variant="secondary"
                              className={
                                submission.status === "teacher_reviewed" 
                                  ? "bg-green-100 text-green-700" 
                                  : "bg-yellow-100 text-yellow-700"
                              }
                            >
                              {submission.status === "teacher_reviewed" ? "Reviewed" : "AI Graded"}
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

        {/* Right Panel - Paper Detail View */}
        <div className="col-span-8 flex flex-col">
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
              {/* Header */}
              <CardHeader className="pb-3 border-b">
                <div className="flex items-center justify-between">
                  <div>
                    <CardTitle className="text-xl">{selectedSubmission.student_name}</CardTitle>
                    <p className="text-sm text-muted-foreground">
                      {selectedSubmission.subject_name} • Score: {selectedSubmission.total_score} ({selectedSubmission.percentage}%)
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
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
              </CardHeader>

              {/* Content - Split View */}
              <div className="flex-1 overflow-hidden grid grid-cols-2">
                {/* Left: PDF Viewer */}
                <div className="border-r overflow-auto bg-muted/30 p-4">
                  {selectedSubmission.file_images?.length > 0 ? (
                    <div className="space-y-4">
                      {selectedSubmission.file_images.map((img, idx) => (
                        <img 
                          key={idx}
                          src={`data:image/jpeg;base64,${img}`}
                          alt={`Page ${idx + 1}`}
                          className="w-full rounded-lg shadow-md"
                        />
                      ))}
                    </div>
                  ) : (
                    <div className="h-full flex items-center justify-center">
                      <p className="text-muted-foreground">No preview available</p>
                    </div>
                  )}
                </div>

                {/* Right: Question Breakdown */}
                <ScrollArea className="p-4">
                  <div className="space-y-4">
                    {selectedSubmission.question_scores?.map((qs, index) => (
                      <div 
                        key={index}
                        className={`p-4 rounded-lg border question-card ${
                          qs.is_reviewed ? "reviewed" : ""
                        }`}
                      >
                        <div className="flex items-center justify-between mb-3">
                          <h4 className="font-semibold">Question {qs.question_number}</h4>
                          <div className="flex items-center gap-2">
                            <Input 
                              type="number"
                              value={qs.obtained_marks}
                              onChange={(e) => updateQuestionScore(index, "obtained_marks", parseFloat(e.target.value) || 0)}
                              className="w-20 text-center"
                              data-testid={`score-q${qs.question_number}`}
                            />
                            <span className="text-muted-foreground">/ {qs.max_marks}</span>
                          </div>
                        </div>

                        <div className="space-y-3">
                          <div>
                            <Label className="text-sm text-muted-foreground">AI Feedback</Label>
                            <Textarea 
                              value={qs.ai_feedback}
                              onChange={(e) => updateQuestionScore(index, "ai_feedback", e.target.value)}
                              className="mt-1 text-sm"
                              rows={3}
                            />
                          </div>

                          <div>
                            <Label className="text-sm text-muted-foreground">Teacher Comment</Label>
                            <Textarea 
                              value={qs.teacher_comment || ""}
                              onChange={(e) => updateQuestionScore(index, "teacher_comment", e.target.value)}
                              placeholder="Add your comments..."
                              className="mt-1 text-sm"
                              rows={2}
                            />
                          </div>

                          <div className="flex items-center gap-2">
                            <Checkbox 
                              id={`reviewed-${index}`}
                              checked={qs.is_reviewed}
                              onCheckedChange={(checked) => updateQuestionScore(index, "is_reviewed", checked)}
                            />
                            <Label htmlFor={`reviewed-${index}`} className="text-sm cursor-pointer">
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
              <div className="border-t p-4 flex items-center justify-between bg-muted/30">
                <div className="flex items-center gap-2">
                  <Button 
                    variant="outline" 
                    size="sm"
                    onClick={() => navigatePaper(-1)}
                    disabled={currentIndex <= 0}
                  >
                    <ChevronLeft className="w-4 h-4 mr-1" />
                    Previous
                  </Button>
                  <Button 
                    variant="outline" 
                    size="sm"
                    onClick={() => navigatePaper(1)}
                    disabled={currentIndex >= filteredSubmissions.length - 1}
                  >
                    Next
                    <ChevronRight className="w-4 h-4 ml-1" />
                  </Button>
                  <span className="text-sm text-muted-foreground ml-2">
                    {currentIndex + 1} of {filteredSubmissions.length}
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <Button 
                    variant="outline"
                    onClick={handleSaveChanges}
                    disabled={saving}
                    data-testid="save-changes-btn"
                  >
                    {saving ? <RefreshCw className="w-4 h-4 mr-2 animate-spin" /> : <Save className="w-4 h-4 mr-2" />}
                    Save Changes
                  </Button>
                  <Button 
                    onClick={() => {
                      handleSaveChanges();
                      if (currentIndex < filteredSubmissions.length - 1) {
                        navigatePaper(1);
                      }
                    }}
                    disabled={saving}
                    data-testid="approve-finalize-btn"
                  >
                    <CheckCircle className="w-4 h-4 mr-2" />
                    Approve & Next
                  </Button>
                </div>
              </div>
            </Card>
          )}
        </div>
      </div>
    </Layout>
  );
}
