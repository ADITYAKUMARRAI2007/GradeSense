import { useState, useEffect } from "react";
import axios from "axios";
import { API } from "../../App";
import Layout from "../../components/Layout";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Badge } from "../../components/ui/badge";
import { ScrollArea } from "../../components/ui/scroll-area";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "../../components/ui/dialog";
import { toast } from "sonner";
import { useDropzone } from "react-dropzone";
import { 
  FileText,
  Search,
  Trash2,
  Users,
  Calendar,
  BookOpen,
  ChevronRight,
  AlertTriangle,
  CheckCircle,
  Upload,
  Lock,
  LockOpen,
  CheckCircle2
} from "lucide-react";

export default function ManageExams({ user }) {
  const [exams, setExams] = useState([]);
  const [batches, setBatches] = useState([]);
  const [subjects, setSubjects] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedExam, setSelectedExam] = useState(null);
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);
  const [uploadingPapers, setUploadingPapers] = useState(false);
  const [paperFiles, setPaperFiles] = useState([]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    accept: { 'application/pdf': ['.pdf'] },
    multiple: true,
    onDrop: (acceptedFiles) => {
      setPaperFiles(acceptedFiles);
    }
  });

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      const [examsRes, batchesRes, subjectsRes] = await Promise.all([
        axios.get(`${API}/exams`),
        axios.get(`${API}/batches`),
        axios.get(`${API}/subjects`)
      ]);
      setExams(examsRes.data);
      setBatches(batchesRes.data);
      setSubjects(subjectsRes.data);
    } catch (error) {
      console.error("Error fetching data:", error);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (exam) => {
    if (!confirm(`Are you sure you want to delete "${exam.exam_name}"? This will also delete all submissions and re-evaluation requests associated with this exam.`)) {
      return;
    }
    
    try {
      await axios.delete(`${API}/exams/${exam.exam_id}`);
      toast.success("Exam deleted successfully");
      fetchData();
      if (selectedExam?.exam_id === exam.exam_id) {
        setSelectedExam(null);
      }
    } catch (error) {
      toast.error(error.response?.data?.detail || "Failed to delete exam");
    }
  };

  const getBatchName = (batchId) => {
    const batch = batches.find(b => b.batch_id === batchId);
    return batch?.name || "Unknown";
  };

  const getSubjectName = (subjectId) => {
    const subject = subjects.find(s => s.subject_id === subjectId);
    return subject?.name || "Unknown";
  };

  const getStatusBadge = (status) => {
    const styles = {
      draft: "bg-gray-100 text-gray-700",
      processing: "bg-blue-100 text-blue-700",
      completed: "bg-green-100 text-green-700"
    };
    return (
      <Badge className={styles[status] || styles.draft}>
        {status.charAt(0).toUpperCase() + status.slice(1)}
      </Badge>
    );
  };

  const filteredExams = exams.filter(e => 
    e.exam_name.toLowerCase().includes(searchQuery.toLowerCase()) ||
    getBatchName(e.batch_id).toLowerCase().includes(searchQuery.toLowerCase()) ||
    getSubjectName(e.subject_id).toLowerCase().includes(searchQuery.toLowerCase())
  );

  return (
    <Layout user={user}>
      <div className="space-y-4 lg:space-y-6" data-testid="manage-exams-page">
        {/* Header */}
        <div>
          <h1 className="text-xl lg:text-2xl font-bold text-foreground">Manage Exams</h1>
          <p className="text-sm text-muted-foreground">View and manage all your exams</p>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 lg:gap-6">
          {/* Exams List */}
          <div className="lg:col-span-1">
            <Card>
              <CardHeader className="p-4">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input 
                    placeholder="Search exams..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="pl-9"
                    data-testid="search-input"
                  />
                </div>
              </CardHeader>
              <CardContent className="p-0">
                <ScrollArea className="h-[400px] lg:h-[500px]">
                  {loading ? (
                    <div className="p-4 space-y-2">
                      {[1, 2, 3, 4].map(i => (
                        <div key={i} className="h-20 bg-muted animate-pulse rounded-lg" />
                      ))}
                    </div>
                  ) : filteredExams.length === 0 ? (
                    <div className="text-center py-8 px-4">
                      <FileText className="w-12 h-12 mx-auto text-muted-foreground/50 mb-3" />
                      <p className="text-muted-foreground">No exams found</p>
                    </div>
                  ) : (
                    <div className="p-2 space-y-1">
                      {filteredExams.map((exam) => (
                        <div 
                          key={exam.exam_id}
                          onClick={() => setSelectedExam(exam)}
                          className={`p-3 rounded-lg cursor-pointer transition-all flex items-center justify-between ${
                            selectedExam?.exam_id === exam.exam_id
                              ? "bg-primary/10 border border-primary"
                              : "hover:bg-muted"
                          }`}
                          data-testid={`exam-${exam.exam_id}`}
                        >
                          <div className="flex items-center gap-3 min-w-0 flex-1">
                            <div className="w-10 h-10 rounded-lg bg-orange-50 flex items-center justify-center flex-shrink-0">
                              <FileText className="w-5 h-5 text-orange-600" />
                            </div>
                            <div className="min-w-0">
                              <p className="font-medium truncate">{exam.exam_name}</p>
                              <p className="text-xs text-muted-foreground truncate">
                                {getBatchName(exam.batch_id)} • {getSubjectName(exam.subject_id)}
                              </p>
                            </div>
                          </div>
                          <ChevronRight className="w-4 h-4 text-muted-foreground flex-shrink-0" />
                        </div>
                      ))}
                    </div>
                  )}
                </ScrollArea>
              </CardContent>
            </Card>
          </div>

          {/* Exam Details */}
          <div className="lg:col-span-2">
            {!selectedExam ? (
              <Card className="h-full flex items-center justify-center min-h-[400px]">
                <div className="text-center">
                  <FileText className="w-16 h-16 mx-auto text-muted-foreground/30 mb-4" />
                  <p className="text-lg font-medium text-muted-foreground">Select an exam</p>
                  <p className="text-sm text-muted-foreground">Click on an exam to view details</p>
                </div>
              </Card>
            ) : (
              <Card>
                <CardHeader className="flex flex-row items-start justify-between">
                  <div>
                    <CardTitle className="text-xl">{selectedExam.exam_name}</CardTitle>
                    <CardDescription>
                      Created {new Date(selectedExam.created_at).toLocaleDateString()}
                    </CardDescription>
                  </div>
                  <div className="flex gap-2">
                    {getStatusBadge(selectedExam.status)}
                    <Button 
                      variant="outline" 
                      size="sm"
                      onClick={() => handleDelete(selectedExam)}
                      className="text-destructive hover:text-destructive"
                      data-testid="delete-exam-btn"
                    >
                      <Trash2 className="w-4 h-4 mr-1" />
                      Delete
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-6">
                  {/* Basic Info */}
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                    <div className="p-4 bg-blue-50 rounded-lg">
                      <div className="flex items-center gap-2 mb-1">
                        <BookOpen className="w-4 h-4 text-blue-600" />
                        <span className="text-xs text-blue-600">Batch</span>
                      </div>
                      <p className="font-medium text-sm">{getBatchName(selectedExam.batch_id)}</p>
                    </div>
                    
                    <div className="p-4 bg-orange-50 rounded-lg">
                      <div className="flex items-center gap-2 mb-1">
                        <FileText className="w-4 h-4 text-orange-600" />
                        <span className="text-xs text-orange-600">Subject</span>
                      </div>
                      <p className="font-medium text-sm">{getSubjectName(selectedExam.subject_id)}</p>
                    </div>

                    <div className="p-4 bg-green-50 rounded-lg">
                      <div className="flex items-center gap-2 mb-1">
                        <CheckCircle className="w-4 h-4 text-green-600" />
                        <span className="text-xs text-green-600">Total Marks</span>
                      </div>
                      <p className="font-medium text-sm">{selectedExam.total_marks}</p>
                    </div>

                    <div className="p-4 bg-purple-50 rounded-lg">
                      <div className="flex items-center gap-2 mb-1">
                        <Calendar className="w-4 h-4 text-purple-600" />
                        <span className="text-xs text-purple-600">Exam Date</span>
                      </div>
                      <p className="font-medium text-sm">{selectedExam.exam_date}</p>
                    </div>
                  </div>

                  {/* Additional Details */}
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div className="p-4 border rounded-lg">
                      <p className="text-sm text-muted-foreground mb-1">Exam Type</p>
                      <p className="font-medium">{selectedExam.exam_type}</p>
                    </div>
                    <div className="p-4 border rounded-lg">
                      <p className="text-sm text-muted-foreground mb-1">Grading Mode</p>
                      <p className="font-medium capitalize">{selectedExam.grading_mode}</p>
                    </div>
                  </div>

                  {/* Questions */}
                  <div>
                    <h3 className="font-semibold mb-3 flex items-center gap-2">
                      <FileText className="w-4 h-4" />
                      Questions ({selectedExam.questions?.length || 0})
                    </h3>
                    <div className="space-y-2 max-h-64 overflow-y-auto">
                      {selectedExam.questions?.map((question, idx) => (
                        <div key={idx} className="p-3 bg-muted/50 rounded-lg">
                          <div className="flex items-center justify-between">
                            <span className="font-medium text-sm">Question {question.question_number}</span>
                            <Badge variant="outline">{question.max_marks} marks</Badge>
                          </div>
                          {question.sub_questions?.length > 0 && (
                            <div className="mt-2 ml-4 space-y-1">
                              {question.sub_questions.map((sq, sqIdx) => (
                                <div key={sqIdx} className="flex items-center justify-between text-xs">
                                  <span className="text-muted-foreground">Part {sq.sub_id}</span>
                                  <span className="text-muted-foreground">{sq.max_marks} marks</span>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Stats */}
                  <div className="p-4 bg-yellow-50 rounded-lg">
                    <div className="flex items-center gap-2 mb-2">
                      <Users className="w-4 h-4 text-yellow-600" />
                      <span className="font-medium text-yellow-800">Submissions</span>
                    </div>
                    <p className="text-sm text-yellow-700">
                      {selectedExam.submission_count || 0} student papers graded
                    </p>
                  </div>

                  {/* Warning */}
                  <div className="p-4 bg-red-50 border border-red-200 rounded-lg flex items-start gap-3">
                    <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
                    <div>
                      <p className="font-medium text-red-800 text-sm">Delete Warning</p>
                      <p className="text-sm text-red-700 mt-1">
                        Deleting this exam will permanently remove all submissions, grades, and re-evaluation requests. This action cannot be undone.
                      </p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      </div>
    </Layout>
  );
}
