import { useState, useEffect } from "react";
import axios from "axios";
import { API } from "../../App";
import Layout from "../../components/Layout";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Badge } from "../../components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "../../components/ui/dialog";
import { ScrollArea } from "../../components/ui/scroll-area";
import { 
  FileText, 
  Download, 
  Eye,
  ChevronDown,
  ChevronUp
} from "lucide-react";

export default function StudentResults({ user }) {
  const [submissions, setSubmissions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [selectedSubmission, setSelectedSubmission] = useState(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [expandedIds, setExpandedIds] = useState([]);

  useEffect(() => {
    fetchSubmissions();
  }, []);

  const fetchSubmissions = async () => {
    try {
      const response = await axios.get(`${API}/submissions`);
      setSubmissions(response.data);
    } catch (error) {
      console.error("Error fetching submissions:", error);
    } finally {
      setLoading(false);
    }
  };

  const viewDetails = async (submissionId) => {
    try {
      const response = await axios.get(`${API}/submissions/${submissionId}`);
      setSelectedSubmission(response.data);
      setDialogOpen(true);
    } catch (error) {
      console.error("Error fetching details:", error);
    }
  };

  const toggleExpand = (id) => {
    setExpandedIds(prev => 
      prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]
    );
  };

  const getGradeBadgeColor = (percentage) => {
    if (percentage >= 80) return "bg-green-100 text-green-700";
    if (percentage >= 60) return "bg-blue-100 text-blue-700";
    if (percentage >= 40) return "bg-yellow-100 text-yellow-700";
    return "bg-red-100 text-red-700";
  };

  return (
    <Layout user={user}>
      <div className="space-y-6" data-testid="student-results-page">
        <div>
          <h1 className="text-2xl font-bold text-foreground">My Results</h1>
          <p className="text-muted-foreground">View your exam results and feedback</p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="w-5 h-5 text-primary" />
              Exam Results ({submissions.length})
            </CardTitle>
          </CardHeader>
          <CardContent>
            {loading ? (
              <div className="space-y-3">
                {[1, 2, 3, 4].map(i => (
                  <div key={i} className="h-20 bg-muted animate-pulse rounded-lg" />
                ))}
              </div>
            ) : submissions.length === 0 ? (
              <div className="text-center py-12">
                <FileText className="w-12 h-12 mx-auto text-muted-foreground/50 mb-3" />
                <p className="text-muted-foreground">No results available yet</p>
              </div>
            ) : (
              <div className="space-y-4">
                {submissions.map((submission) => (
                  <div 
                    key={submission.submission_id}
                    className="border rounded-lg overflow-hidden"
                    data-testid={`result-${submission.submission_id}`}
                  >
                    <div 
                      className="flex items-center justify-between p-4 cursor-pointer hover:bg-muted/50 transition-colors"
                      onClick={() => toggleExpand(submission.submission_id)}
                    >
                      <div className="flex items-center gap-4">
                        <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center">
                          <span className="text-lg font-bold text-primary">
                            {submission.percentage}%
                          </span>
                        </div>
                        <div>
                          <p className="font-medium">{submission.exam_name || "Exam"}</p>
                          <p className="text-sm text-muted-foreground">
                            {submission.subject_name || "Subject"} • Score: {submission.total_score}
                          </p>
                        </div>
                      </div>
                      <div className="flex items-center gap-3">
                        <Badge className={getGradeBadgeColor(submission.percentage)}>
                          {submission.percentage >= 80 ? "Excellent" :
                           submission.percentage >= 60 ? "Good" :
                           submission.percentage >= 40 ? "Average" : "Needs Improvement"}
                        </Badge>
                        {expandedIds.includes(submission.submission_id) ? (
                          <ChevronUp className="w-5 h-5 text-muted-foreground" />
                        ) : (
                          <ChevronDown className="w-5 h-5 text-muted-foreground" />
                        )}
                      </div>
                    </div>

                    {expandedIds.includes(submission.submission_id) && (
                      <div className="border-t p-4 bg-muted/30">
                        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                          {submission.question_scores?.slice(0, 6).map((qs, idx) => (
                            <div key={idx} className="p-3 bg-white rounded-lg border">
                              <div className="flex items-center justify-between mb-1">
                                <span className="text-sm font-medium">Q{qs.question_number}</span>
                                <span className="text-sm">
                                  {qs.obtained_marks}/{qs.max_marks}
                                </span>
                              </div>
                              <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
                                <div 
                                  className="h-full bg-primary rounded-full transition-all"
                                  style={{ width: `${(qs.obtained_marks / qs.max_marks) * 100}%` }}
                                />
                              </div>
                            </div>
                          ))}
                        </div>
                        
                        <div className="flex justify-end mt-4">
                          <Button 
                            variant="outline" 
                            size="sm"
                            onClick={() => viewDetails(submission.submission_id)}
                          >
                            <Eye className="w-4 h-4 mr-2" />
                            View Full Details
                          </Button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Detail Dialog */}
        <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
          <DialogContent className="max-w-3xl max-h-[80vh] overflow-hidden flex flex-col">
            <DialogHeader>
              <DialogTitle>
                Result Details - {selectedSubmission?.exam_name || "Exam"}
              </DialogTitle>
            </DialogHeader>
            
            {selectedSubmission && (
              <ScrollArea className="flex-1 pr-4">
                <div className="space-y-4 py-4">
                  {/* Summary */}
                  <div className="flex items-center gap-4 p-4 bg-primary/5 rounded-lg">
                    <div className="text-center">
                      <p className="text-4xl font-bold text-primary">
                        {selectedSubmission.percentage}%
                      </p>
                      <p className="text-sm text-muted-foreground">Overall Score</p>
                    </div>
                    <div className="flex-1 grid grid-cols-2 gap-4">
                      <div>
                        <p className="text-sm text-muted-foreground">Total Marks</p>
                        <p className="font-medium">{selectedSubmission.total_score}</p>
                      </div>
                      <div>
                        <p className="text-sm text-muted-foreground">Status</p>
                        <Badge className={
                          selectedSubmission.status === "teacher_reviewed" 
                            ? "bg-green-100 text-green-700" 
                            : "bg-yellow-100 text-yellow-700"
                        }>
                          {selectedSubmission.status === "teacher_reviewed" ? "Reviewed" : "AI Graded"}
                        </Badge>
                      </div>
                    </div>
                  </div>

                  {/* Question-wise Breakdown */}
                  <div className="space-y-3">
                    <h3 className="font-semibold">Question-wise Breakdown</h3>
                    {selectedSubmission.question_scores?.map((qs, idx) => (
                      <div key={idx} className="p-4 border rounded-lg">
                        <div className="flex items-center justify-between mb-2">
                          <span className="font-medium">Question {qs.question_number}</span>
                          <Badge variant="outline">
                            {qs.obtained_marks} / {qs.max_marks}
                          </Badge>
                        </div>
                        <div className="h-2 bg-gray-100 rounded-full overflow-hidden mb-3">
                          <div 
                            className={`h-full rounded-full transition-all ${
                              (qs.obtained_marks / qs.max_marks) >= 0.8 ? "bg-green-500" :
                              (qs.obtained_marks / qs.max_marks) >= 0.5 ? "bg-yellow-500" : "bg-red-500"
                            }`}
                            style={{ width: `${(qs.obtained_marks / qs.max_marks) * 100}%` }}
                          />
                        </div>
                        <div className="bg-muted/50 p-3 rounded-lg">
                          <p className="text-sm text-muted-foreground mb-1">Feedback:</p>
                          <p className="text-sm">{qs.ai_feedback}</p>
                        </div>
                        {qs.teacher_comment && (
                          <div className="bg-blue-50 p-3 rounded-lg mt-2">
                            <p className="text-sm text-blue-700">
                              <strong>Teacher Note:</strong> {qs.teacher_comment}
                            </p>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              </ScrollArea>
            )}
          </DialogContent>
        </Dialog>
      </div>
    </Layout>
  );
}
