import { useState, useEffect, useCallback } from "react";
import axios from "axios";
import { API } from "../../App";
import Layout from "../../components/Layout";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Textarea } from "../../components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Progress } from "../../components/ui/progress";
import { Badge } from "../../components/ui/badge";
import { toast } from "sonner";
import { useDropzone } from "react-dropzone";
import { 
  Upload, 
  FileText, 
  Plus, 
  Trash2, 
  CheckCircle, 
  ArrowRight, 
  ArrowLeft,
  Loader2,
  AlertCircle,
  X
} from "lucide-react";

const GRADING_MODES = [
  { 
    id: "strict", 
    name: "Strict Mode", 
    description: "Exact match with model answer required. Minimal tolerance for deviations.",
    color: "bg-red-50 border-red-200"
  },
  { 
    id: "balanced", 
    name: "Balanced Mode", 
    description: "Fair evaluation considering both accuracy and conceptual understanding.",
    color: "bg-blue-50 border-blue-200",
    recommended: true
  },
  { 
    id: "conceptual", 
    name: "Conceptual Mode", 
    description: "Focus on understanding of concepts over exact wording or format.",
    color: "bg-purple-50 border-purple-200"
  },
  { 
    id: "lenient", 
    name: "Lenient Mode", 
    description: "Generous partial credit. Rewards attempt and partial understanding.",
    color: "bg-green-50 border-green-200"
  },
];

const EXAM_TYPES = [
  "Mock Test",
  "Unit Test", 
  "Mid-Term",
  "End-Term",
  "Competitive Prep",
  "Custom"
];

export default function UploadGrade({ user }) {
  const [step, setStep] = useState(1);
  const [batches, setBatches] = useState([]);
  const [subjects, setSubjects] = useState([]);
  const [loading, setLoading] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [processingProgress, setProcessingProgress] = useState(0);

  // Form state
  const [formData, setFormData] = useState({
    batch_id: "",
    subject_id: "",
    exam_type: "",
    exam_name: "",
    total_marks: 100,
    exam_date: new Date().toISOString().split("T")[0],
    grading_mode: "balanced",
    questions: [{ question_number: 1, max_marks: 10, rubric: "" }]
  });

  const [modelAnswerFile, setModelAnswerFile] = useState(null);
  const [studentFiles, setStudentFiles] = useState([]);
  const [examId, setExamId] = useState(null);
  const [results, setResults] = useState(null);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      const [batchesRes, subjectsRes] = await Promise.all([
        axios.get(`${API}/batches`),
        axios.get(`${API}/subjects`)
      ]);
      setBatches(batchesRes.data);
      setSubjects(subjectsRes.data);
    } catch (error) {
      console.error("Error fetching data:", error);
    }
  };

  const handleInputChange = (field, value) => {
    setFormData(prev => ({ ...prev, [field]: value }));
  };

  const addQuestion = () => {
    const nextNum = formData.questions.length + 1;
    setFormData(prev => ({
      ...prev,
      questions: [...prev.questions, { question_number: nextNum, max_marks: 10, rubric: "" }]
    }));
  };

  const updateQuestion = (index, field, value) => {
    setFormData(prev => {
      const newQuestions = [...prev.questions];
      newQuestions[index] = { ...newQuestions[index], [field]: value };
      return { ...prev, questions: newQuestions };
    });
  };

  const removeQuestion = (index) => {
    if (formData.questions.length > 1) {
      setFormData(prev => ({
        ...prev,
        questions: prev.questions.filter((_, i) => i !== index).map((q, i) => ({
          ...q,
          question_number: i + 1
        }))
      }));
    }
  };

  // Model answer dropzone
  const onModelAnswerDrop = useCallback((acceptedFiles) => {
    if (acceptedFiles.length > 0) {
      setModelAnswerFile(acceptedFiles[0]);
    }
  }, []);

  const { getRootProps: getModelRootProps, getInputProps: getModelInputProps, isDragActive: isModelDragActive } = useDropzone({
    onDrop: onModelAnswerDrop,
    accept: { 'application/pdf': ['.pdf'] },
    maxFiles: 1
  });

  // Student papers dropzone
  const onStudentPapersDrop = useCallback((acceptedFiles) => {
    setStudentFiles(prev => [...prev, ...acceptedFiles]);
  }, []);

  const { getRootProps: getStudentRootProps, getInputProps: getStudentInputProps, isDragActive: isStudentDragActive } = useDropzone({
    onDrop: onStudentPapersDrop,
    accept: { 'application/pdf': ['.pdf'] },
    multiple: true
  });

  const removeStudentFile = (index) => {
    setStudentFiles(prev => prev.filter((_, i) => i !== index));
  };

  const createSubject = async (name) => {
    try {
      const response = await axios.post(`${API}/subjects`, { name });
      setSubjects(prev => [...prev, response.data]);
      handleInputChange("subject_id", response.data.subject_id);
      toast.success("Subject created");
    } catch (error) {
      toast.error("Failed to create subject");
    }
  };

  const createBatch = async (name) => {
    try {
      const response = await axios.post(`${API}/batches`, { name });
      setBatches(prev => [...prev, response.data]);
      handleInputChange("batch_id", response.data.batch_id);
      toast.success("Batch created");
    } catch (error) {
      toast.error("Failed to create batch");
    }
  };

  const handleCreateExam = async () => {
    setLoading(true);
    try {
      const response = await axios.post(`${API}/exams`, formData);
      setExamId(response.data.exam_id);
      toast.success("Exam configuration saved");
      setStep(4);
    } catch (error) {
      toast.error("Failed to create exam");
    } finally {
      setLoading(false);
    }
  };

  const handleUploadModelAnswer = async () => {
    if (!modelAnswerFile || !examId) return;
    
    setLoading(true);
    try {
      const formData = new FormData();
      formData.append("file", modelAnswerFile);
      
      await axios.post(`${API}/exams/${examId}/upload-model-answer`, formData, {
        headers: { "Content-Type": "multipart/form-data" }
      });
      
      toast.success("Model answer uploaded");
      setStep(5);
    } catch (error) {
      toast.error("Failed to upload model answer");
    } finally {
      setLoading(false);
    }
  };

  const handleStartGrading = async () => {
    if (studentFiles.length === 0 || !examId) return;
    
    setProcessing(true);
    setProcessingProgress(0);
    
    try {
      const formData = new FormData();
      studentFiles.forEach(file => {
        formData.append("files", file);
      });
      
      // Simulate progress
      const progressInterval = setInterval(() => {
        setProcessingProgress(prev => Math.min(prev + 5, 90));
      }, 500);
      
      const response = await axios.post(`${API}/exams/${examId}/upload-papers`, formData, {
        headers: { "Content-Type": "multipart/form-data" }
      });
      
      clearInterval(progressInterval);
      setProcessingProgress(100);
      setResults(response.data);
      toast.success(`Graded ${response.data.processed} papers`);
      setStep(6);
    } catch (error) {
      toast.error("Grading failed: " + (error.response?.data?.detail || error.message));
    } finally {
      setProcessing(false);
    }
  };

  const renderStepIndicator = () => (
    <div className="flex items-center justify-center gap-2 mb-8">
      {[1, 2, 3, 4, 5, 6].map((s) => (
        <div key={s} className="flex items-center">
          <div 
            className={`w-8 h-8 rounded-full flex items-center justify-center text-sm font-medium transition-all ${
              s < step ? "bg-green-500 text-white" :
              s === step ? "bg-primary text-white" :
              "bg-muted text-muted-foreground"
            }`}
          >
            {s < step ? <CheckCircle className="w-5 h-5" /> : s}
          </div>
          {s < 6 && (
            <div className={`w-8 h-1 mx-1 ${s < step ? "bg-green-500" : "bg-muted"}`} />
          )}
        </div>
      ))}
    </div>
  );

  return (
    <Layout user={user}>
      <div className="max-w-4xl mx-auto" data-testid="upload-grade-page">
        {renderStepIndicator()}

        {/* Step 1: Exam Configuration */}
        {step === 1 && (
          <Card className="animate-fade-in">
            <CardHeader>
              <CardTitle>Step 1: Exam Configuration</CardTitle>
              <CardDescription>Set up the basic details for this exam</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Batch/Class *</Label>
                  <Select 
                    value={formData.batch_id} 
                    onValueChange={(v) => handleInputChange("batch_id", v)}
                  >
                    <SelectTrigger data-testid="batch-select">
                      <SelectValue placeholder="Select batch" />
                    </SelectTrigger>
                    <SelectContent>
                      {batches.map(batch => (
                        <SelectItem key={batch.batch_id} value={batch.batch_id}>
                          {batch.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button 
                    variant="link" 
                    size="sm" 
                    className="p-0 h-auto text-primary"
                    onClick={() => {
                      const name = prompt("Enter batch name:");
                      if (name) createBatch(name);
                    }}
                  >
                    <Plus className="w-3 h-3 mr-1" /> Add new batch
                  </Button>
                </div>

                <div className="space-y-2">
                  <Label>Subject *</Label>
                  <Select 
                    value={formData.subject_id} 
                    onValueChange={(v) => handleInputChange("subject_id", v)}
                  >
                    <SelectTrigger data-testid="subject-select">
                      <SelectValue placeholder="Select subject" />
                    </SelectTrigger>
                    <SelectContent>
                      {subjects.map(subject => (
                        <SelectItem key={subject.subject_id} value={subject.subject_id}>
                          {subject.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <Button 
                    variant="link" 
                    size="sm" 
                    className="p-0 h-auto text-primary"
                    onClick={() => {
                      const name = prompt("Enter subject name:");
                      if (name) createSubject(name);
                    }}
                  >
                    <Plus className="w-3 h-3 mr-1" /> Add new subject
                  </Button>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Exam Type *</Label>
                  <Select 
                    value={formData.exam_type} 
                    onValueChange={(v) => handleInputChange("exam_type", v)}
                  >
                    <SelectTrigger data-testid="exam-type-select">
                      <SelectValue placeholder="Select type" />
                    </SelectTrigger>
                    <SelectContent>
                      {EXAM_TYPES.map(type => (
                        <SelectItem key={type} value={type}>{type}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label>Exam Name *</Label>
                  <Input 
                    placeholder="e.g., Physics Mid-Term October 2024"
                    value={formData.exam_name}
                    onChange={(e) => handleInputChange("exam_name", e.target.value)}
                    data-testid="exam-name-input"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label>Total Marks *</Label>
                  <Input 
                    type="number"
                    value={formData.total_marks}
                    onChange={(e) => handleInputChange("total_marks", parseFloat(e.target.value))}
                    data-testid="total-marks-input"
                  />
                </div>

                <div className="space-y-2">
                  <Label>Date of Exam</Label>
                  <Input 
                    type="date"
                    value={formData.exam_date}
                    onChange={(e) => handleInputChange("exam_date", e.target.value)}
                    data-testid="exam-date-input"
                  />
                </div>
              </div>

              <div className="flex justify-end pt-4">
                <Button 
                  onClick={() => setStep(2)}
                  disabled={!formData.batch_id || !formData.subject_id || !formData.exam_name}
                  data-testid="next-step-btn"
                >
                  Next: Configure Questions
                  <ArrowRight className="w-4 h-4 ml-2" />
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Step 2: Question Configuration */}
        {step === 2 && (
          <Card className="animate-fade-in">
            <CardHeader>
              <CardTitle>Step 2: Question Configuration</CardTitle>
              <CardDescription>Define the questions and marks distribution</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {formData.questions.map((question, index) => (
                <div key={index} className="flex items-start gap-4 p-4 bg-muted/50 rounded-lg">
                  <div className="flex-1 grid grid-cols-3 gap-4">
                    <div className="space-y-2">
                      <Label>Question #{question.question_number}</Label>
                      <Input value={`Q${question.question_number}`} disabled />
                    </div>
                    <div className="space-y-2">
                      <Label>Max Marks</Label>
                      <Input 
                        type="number"
                        value={question.max_marks}
                        onChange={(e) => updateQuestion(index, "max_marks", parseFloat(e.target.value))}
                        data-testid={`question-${index}-marks`}
                      />
                    </div>
                    <div className="space-y-2">
                      <Label>Rubric (Optional)</Label>
                      <Input 
                        placeholder="Grading criteria..."
                        value={question.rubric}
                        onChange={(e) => updateQuestion(index, "rubric", e.target.value)}
                      />
                    </div>
                  </div>
                  {formData.questions.length > 1 && (
                    <Button 
                      variant="ghost" 
                      size="icon"
                      onClick={() => removeQuestion(index)}
                      className="text-destructive hover:text-destructive"
                    >
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  )}
                </div>
              ))}

              <Button 
                variant="outline" 
                onClick={addQuestion}
                className="w-full"
                data-testid="add-question-btn"
              >
                <Plus className="w-4 h-4 mr-2" />
                Add Question
              </Button>

              <div className="flex justify-between pt-4">
                <Button variant="outline" onClick={() => setStep(1)}>
                  <ArrowLeft className="w-4 h-4 mr-2" />
                  Back
                </Button>
                <Button onClick={() => setStep(3)} data-testid="next-grading-mode-btn">
                  Next: Grading Mode
                  <ArrowRight className="w-4 h-4 ml-2" />
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Step 3: Grading Mode Selection */}
        {step === 3 && (
          <Card className="animate-fade-in">
            <CardHeader>
              <CardTitle>Step 3: Select Grading Mode</CardTitle>
              <CardDescription>Choose how strictly the AI should grade the papers</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-2 gap-4">
                {GRADING_MODES.map((mode) => (
                  <div 
                    key={mode.id}
                    onClick={() => handleInputChange("grading_mode", mode.id)}
                    className={`grading-mode-card p-4 border-2 rounded-xl cursor-pointer transition-all ${
                      formData.grading_mode === mode.id ? "selected" : ""
                    } ${mode.color}`}
                    data-testid={`grading-mode-${mode.id}`}
                  >
                    <div className="flex items-start justify-between">
                      <h3 className="font-semibold">{mode.name}</h3>
                      {mode.recommended && (
                        <Badge variant="secondary" className="text-xs">Recommended</Badge>
                      )}
                    </div>
                    <p className="text-sm text-muted-foreground mt-2">{mode.description}</p>
                  </div>
                ))}
              </div>

              <div className="flex justify-between pt-4">
                <Button variant="outline" onClick={() => setStep(2)}>
                  <ArrowLeft className="w-4 h-4 mr-2" />
                  Back
                </Button>
                <Button onClick={handleCreateExam} disabled={loading} data-testid="create-exam-btn">
                  {loading ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
                  Save & Continue
                  <ArrowRight className="w-4 h-4 ml-2" />
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Step 4: Upload Model Answer */}
        {step === 4 && (
          <Card className="animate-fade-in">
            <CardHeader>
              <CardTitle>Step 4: Upload Model Answer</CardTitle>
              <CardDescription>Upload the reference answer sheet for AI grading</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div 
                {...getModelRootProps()} 
                className={`dropzone upload-zone p-8 text-center ${isModelDragActive ? "active" : ""}`}
                data-testid="model-answer-dropzone"
              >
                <input {...getModelInputProps()} />
                {modelAnswerFile ? (
                  <div className="flex items-center justify-center gap-3">
                    <FileText className="w-8 h-8 text-primary" />
                    <div className="text-left">
                      <p className="font-medium">{modelAnswerFile.name}</p>
                      <p className="text-sm text-muted-foreground">
                        {(modelAnswerFile.size / 1024 / 1024).toFixed(2)} MB
                      </p>
                    </div>
                    <Button 
                      variant="ghost" 
                      size="icon"
                      onClick={(e) => { e.stopPropagation(); setModelAnswerFile(null); }}
                    >
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                ) : (
                  <>
                    <Upload className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
                    <p className="font-medium">Drop your model answer PDF here</p>
                    <p className="text-sm text-muted-foreground mt-1">or click to browse</p>
                  </>
                )}
              </div>

              <div className="flex justify-between pt-4">
                <Button variant="outline" onClick={() => setStep(3)}>
                  <ArrowLeft className="w-4 h-4 mr-2" />
                  Back
                </Button>
                <Button 
                  onClick={handleUploadModelAnswer} 
                  disabled={!modelAnswerFile || loading}
                  data-testid="upload-model-btn"
                >
                  {loading ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : null}
                  Upload & Continue
                  <ArrowRight className="w-4 h-4 ml-2" />
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Step 5: Upload Student Papers */}
        {step === 5 && (
          <Card className="animate-fade-in">
            <CardHeader>
              <CardTitle>Step 5: Upload Student Papers</CardTitle>
              <CardDescription>Upload student answer sheets for grading</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div 
                {...getStudentRootProps()} 
                className={`dropzone upload-zone p-8 text-center ${isStudentDragActive ? "active" : ""}`}
                data-testid="student-papers-dropzone"
              >
                <input {...getStudentInputProps()} />
                <Upload className="w-12 h-12 mx-auto text-muted-foreground mb-4" />
                <p className="font-medium">Drop student answer PDFs here</p>
                <p className="text-sm text-muted-foreground mt-1">
                  Multiple files allowed. Each file should be named with student identifier.
                </p>
              </div>

              {studentFiles.length > 0 && (
                <div className="space-y-2 max-h-60 overflow-y-auto">
                  {studentFiles.map((file, index) => (
                    <div key={index} className="flex items-center justify-between p-3 bg-muted/50 rounded-lg">
                      <div className="flex items-center gap-3">
                        <FileText className="w-5 h-5 text-primary" />
                        <span className="text-sm font-medium">{file.name}</span>
                      </div>
                      <Button 
                        variant="ghost" 
                        size="icon"
                        onClick={() => removeStudentFile(index)}
                      >
                        <X className="w-4 h-4" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}

              {processing && (
                <div className="space-y-2 p-4 bg-primary/5 rounded-lg">
                  <div className="flex items-center gap-2">
                    <Loader2 className="w-5 h-5 animate-spin text-primary" />
                    <span className="font-medium">Processing papers...</span>
                  </div>
                  <Progress value={processingProgress} className="h-2" />
                  <p className="text-sm text-muted-foreground">
                    {processingProgress < 100 ? "AI is analyzing and grading..." : "Almost done!"}
                  </p>
                </div>
              )}

              <div className="flex justify-between pt-4">
                <Button variant="outline" onClick={() => setStep(4)} disabled={processing}>
                  <ArrowLeft className="w-4 h-4 mr-2" />
                  Back
                </Button>
                <Button 
                  onClick={handleStartGrading} 
                  disabled={studentFiles.length === 0 || processing}
                  data-testid="start-grading-btn"
                >
                  {processing ? (
                    <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  ) : null}
                  Start Grading ({studentFiles.length} papers)
                  <ArrowRight className="w-4 h-4 ml-2" />
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Step 6: Results */}
        {step === 6 && results && (
          <Card className="animate-fade-in">
            <CardHeader>
              <div className="flex items-center gap-3">
                <div className="p-3 rounded-full bg-green-100">
                  <CheckCircle className="w-6 h-6 text-green-600" />
                </div>
                <div>
                  <CardTitle>Grading Complete!</CardTitle>
                  <CardDescription>
                    Successfully processed {results.processed} paper{results.processed !== 1 ? "s" : ""}
                  </CardDescription>
                </div>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2 max-h-80 overflow-y-auto">
                {results.submissions.map((sub, index) => (
                  <div key={index} className="flex items-center justify-between p-4 bg-muted/50 rounded-lg">
                    <div className="flex items-center gap-3">
                      <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
                        <span className="font-medium text-primary">
                          {sub.student_name?.charAt(0) || "?"}
                        </span>
                      </div>
                      <div>
                        <p className="font-medium">{sub.student_name}</p>
                        {sub.error ? (
                          <p className="text-sm text-destructive">{sub.error}</p>
                        ) : (
                          <p className="text-sm text-muted-foreground">
                            Score: {sub.total_score} ({sub.percentage}%)
                          </p>
                        )}
                      </div>
                    </div>
                    {!sub.error && (
                      <Badge 
                        className={
                          sub.percentage >= 80 ? "bg-green-100 text-green-700" :
                          sub.percentage >= 60 ? "bg-blue-100 text-blue-700" :
                          sub.percentage >= 40 ? "bg-yellow-100 text-yellow-700" :
                          "bg-red-100 text-red-700"
                        }
                      >
                        {sub.percentage}%
                      </Badge>
                    )}
                  </div>
                ))}
              </div>

              <div className="flex justify-center gap-4 pt-4">
                <Button 
                  variant="outline"
                  onClick={() => {
                    setStep(1);
                    setFormData({
                      batch_id: "",
                      subject_id: "",
                      exam_type: "",
                      exam_name: "",
                      total_marks: 100,
                      exam_date: new Date().toISOString().split("T")[0],
                      grading_mode: "balanced",
                      questions: [{ question_number: 1, max_marks: 10, rubric: "" }]
                    });
                    setModelAnswerFile(null);
                    setStudentFiles([]);
                    setExamId(null);
                    setResults(null);
                  }}
                >
                  Grade More Papers
                </Button>
                <Button onClick={() => window.location.href = "/teacher/review"} data-testid="review-papers-btn">
                  Review & Edit Grades
                  <ArrowRight className="w-4 h-4 ml-2" />
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </Layout>
  );
}
