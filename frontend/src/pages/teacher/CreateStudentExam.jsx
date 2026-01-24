import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import axios from 'axios';
import { ArrowLeft, Upload, Users, FileText, CheckCircle, AlertCircle } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Input } from '../../components/ui/input';
import { Label } from '../../components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select';
import { Switch } from '../../components/ui/switch';
import { Checkbox } from '../../components/ui/checkbox';
import { toast } from 'sonner';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const CreateStudentExam = () => {
  const { batchId } = useParams();
  const navigate = useNavigate();
  
  const [step, setStep] = useState(1);
  const [batch, setBatch] = useState(null);
  const [students, setStudents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  
  // Form data
  const [examName, setExamName] = useState('');
  const [totalMarks, setTotalMarks] = useState('');
  const [gradingMode, setGradingMode] = useState('balanced');
  const [showQuestionPaper, setShowQuestionPaper] = useState(false);
  const [selectedStudents, setSelectedStudents] = useState([]);
  const [questionPaper, setQuestionPaper] = useState(null);
  const [modelAnswer, setModelAnswer] = useState(null);
  const [questions, setQuestions] = useState([{ question_number: 1, max_marks: 10 }]);

  useEffect(() => {
    fetchBatchAndStudents();
  }, [batchId]);

  const fetchBatchAndStudents = async () => {
    try {
      const batchRes = await axios.get(`${API}/batches/${batchId}`, { withCredentials: true });
      setBatch(batchRes.data);
      
      const studentsRes = await axios.get(`${API}/batches/${batchId}/students`, { withCredentials: true });
      setStudents(studentsRes.data);
      
      // Auto-select all students
      setSelectedStudents(studentsRes.data.map(s => s.student_id));
    } catch (error) {
      console.error('Error fetching data:', error);
      toast.error('Failed to load batch data');
    } finally {
      setLoading(false);
    }
  };

  const toggleStudent = (studentId) => {
    setSelectedStudents(prev => 
      prev.includes(studentId)
        ? prev.filter(id => id !== studentId)
        : [...prev, studentId]
    );
  };

  const addQuestion = () => {
    setQuestions([...questions, { question_number: questions.length + 1, max_marks: 10 }]);
  };

  const updateQuestion = (index, field, value) => {
    const updated = [...questions];
    updated[index][field] = field === 'max_marks' ? parseFloat(value) : value;
    setQuestions(updated);
  };

  const removeQuestion = (index) => {
    setQuestions(questions.filter((_, i) => i !== index));
  };

  const handleCreate = async () => {
    if (!examName || !totalMarks || selectedStudents.length === 0 || !questionPaper || !modelAnswer) {
      toast.error('Please fill all required fields');
      return;
    }

    setCreating(true);
    try {
      const formData = new FormData();
      formData.append('question_paper', questionPaper);
      formData.append('model_answer', modelAnswer);
      
      const examData = {
        batch_id: batchId,
        exam_name: examName,
        total_marks: parseFloat(totalMarks),
        grading_mode: gradingMode,
        student_ids: selectedStudents,
        show_question_paper: showQuestionPaper,
        questions: questions.map(q => ({
          question_number: q.question_number,
          max_marks: q.max_marks,
          sub_questions: []
        }))
      };
      
      // Send as multipart with JSON in form field
      formData.append('exam_data', JSON.stringify(examData));
      
      const response = await axios.post(`${API}/exams/student-mode`, formData, {
        withCredentials: true,
        headers: { 'Content-Type': 'multipart/form-data' }
      });
      
      toast.success('Exam created! Students can now submit their answers.');
      navigate(`/teacher/batch/${batchId}`);
    } catch (error) {
      console.error('Error creating exam:', error);
      toast.error(error.response?.data?.detail || 'Failed to create exam');
    } finally {
      setCreating(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-4 border-primary border-t-transparent"></div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-4xl mx-auto">
        <button
          onClick={() => navigate(`/teacher/batch/${batchId}`)}
          className="flex items-center gap-2 text-gray-600 hover:text-gray-900 mb-6"
        >
          <ArrowLeft className="w-4 h-4" />
          Back to Batch
        </button>

        <h1 className="text-3xl font-bold text-gray-900 mb-2">Create Student Upload Exam</h1>
        <p className="text-gray-500 mb-8">Students will upload their answer papers for this exam</p>

        {/* Progress Steps */}
        <div className="flex items-center justify-between mb-8">
          {[1, 2, 3, 4].map((num) => (
            <div key={num} className="flex items-center flex-1">
              <div className={`w-10 h-10 rounded-full flex items-center justify-center font-semibold ${
                step >= num ? 'bg-primary text-white' : 'bg-gray-200 text-gray-600'
              }`}>
                {step > num ? <CheckCircle className="w-6 h-6" /> : num}
              </div>
              {num < 4 && (
                <div className={`flex-1 h-1 mx-2 ${
                  step > num ? 'bg-primary' : 'bg-gray-200'
                }`} />
              )}
            </div>
          ))}
        </div>

        {/* Step 1: Exam Details */}
        {step === 1 && (
          <Card>
            <CardHeader>
              <CardTitle>Step 1: Exam Details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <Label>Exam Name *</Label>
                <Input
                  value={examName}
                  onChange={(e) => setExamName(e.target.value)}
                  placeholder="e.g., Mid-term Math Exam"
                  className="mt-1"
                />
              </div>
              <div>
                <Label>Total Marks *</Label>
                <Input
                  type="number"
                  value={totalMarks}
                  onChange={(e) => setTotalMarks(e.target.value)}
                  placeholder="e.g., 100"
                  className="mt-1"
                />
              </div>
              <div>
                <Label>Grading Mode</Label>
                <Select value={gradingMode} onValueChange={setGradingMode}>
                  <SelectTrigger className="mt-1">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="strict">Strict</SelectItem>
                    <SelectItem value="balanced">Balanced</SelectItem>
                    <SelectItem value="lenient">Lenient</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="flex items-center justify-between p-4 bg-blue-50 rounded-lg">
                <div>
                  <Label>Show Question Paper to Students</Label>
                  <p className="text-sm text-gray-600">Allow students to download the question paper</p>
                </div>
                <Switch
                  checked={showQuestionPaper}
                  onCheckedChange={setShowQuestionPaper}
                />
              </div>
              
              {/* Question Configuration */}
              <div className="border-t pt-4">
                <Label className="text-base font-semibold">Question Structure</Label>
                <p className="text-sm text-gray-600 mb-3">Define the questions for grading</p>
                {questions.map((q, idx) => (
                  <div key={idx} className="flex gap-3 mb-2">
                    <Input
                      type="number"
                      value={q.question_number}
                      onChange={(e) => updateQuestion(idx, 'question_number', e.target.value)}
                      placeholder="Q No."
                      className="w-24"
                    />
                    <Input
                      type="number"
                      value={q.max_marks}
                      onChange={(e) => updateQuestion(idx, 'max_marks', e.target.value)}
                      placeholder="Max marks"
                      className="flex-1"
                    />
                    {questions.length > 1 && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => removeQuestion(idx)}
                      >
                        Remove
                      </Button>
                    )}
                  </div>
                ))}
                <Button variant="outline" size="sm" onClick={addQuestion} className="mt-2">
                  + Add Question
                </Button>
              </div>
              
              <Button onClick={() => setStep(2)} className="w-full" disabled={!examName || !totalMarks}>
                Next: Select Students
              </Button>
            </CardContent>
          </Card>
        )}

        {/* Step 2: Select Students */}
        {step === 2 && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Users className="w-5 h-5" />
                Step 2: Select Students ({selectedStudents.length}/{students.length})
              </CardTitle>
            </CardHeader>
            <CardContent>
              <div className="space-y-2 mb-4">
                {students.map((student) => (
                  <div
                    key={student.student_id}
                    className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg hover:bg-gray-100"
                  >
                    <Checkbox
                      checked={selectedStudents.includes(student.student_id)}
                      onCheckedChange={() => toggleStudent(student.student_id)}
                    />
                    <div className="flex-1">
                      <p className="font-semibold text-gray-900">{student.name}</p>
                      <p className="text-sm text-gray-500">{student.email}</p>
                    </div>
                  </div>
                ))}
              </div>
              {students.length === 0 && (
                <div className="text-center py-8">
                  <AlertCircle className="w-12 h-12 text-gray-300 mx-auto mb-2" />
                  <p className="text-gray-500">No students in this batch</p>
                </div>
              )}
              <div className="flex gap-3">
                <Button variant="outline" onClick={() => setStep(1)} className="flex-1">
                  Back
                </Button>
                <Button
                  onClick={() => setStep(3)}
                  className="flex-1"
                  disabled={selectedStudents.length === 0}
                >
                  Next: Upload Files
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Step 3: Upload Files */}
        {step === 3 && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Upload className="w-5 h-5" />
                Step 3: Upload Question Paper & Model Answer
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div>
                <Label>Question Paper (PDF) *</Label>
                <Input
                  type="file"
                  accept=".pdf"
                  onChange={(e) => setQuestionPaper(e.target.files[0])}
                  className="mt-1"
                />
                {questionPaper && (
                  <p className="text-sm text-green-600 mt-1">✓ {questionPaper.name}</p>
                )}
              </div>
              <div>
                <Label>Model Answer (PDF) *</Label>
                <Input
                  type="file"
                  accept=".pdf"
                  onChange={(e) => setModelAnswer(e.target.files[0])}
                  className="mt-1"
                />
                {modelAnswer && (
                  <p className="text-sm text-green-600 mt-1">✓ {modelAnswer.name}</p>
                )}
              </div>
              <div className="flex gap-3">
                <Button variant="outline" onClick={() => setStep(2)} className="flex-1">
                  Back
                </Button>
                <Button
                  onClick={() => setStep(4)}
                  className="flex-1"
                  disabled={!questionPaper || !modelAnswer}
                >
                  Next: Review & Create
                </Button>
              </div>
            </CardContent>
          </Card>
        )}

        {/* Step 4: Review & Create */}
        {step === 4 && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <CheckCircle className="w-5 h-5" />
                Step 4: Review & Create
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="bg-gray-50 p-4 rounded-lg space-y-2">
                <p><strong>Exam Name:</strong> {examName}</p>
                <p><strong>Total Marks:</strong> {totalMarks}</p>
                <p><strong>Grading Mode:</strong> {gradingMode}</p>
                <p><strong>Students:</strong> {selectedStudents.length} selected</p>
                <p><strong>Question Paper:</strong> {showQuestionPaper ? 'Visible to students' : 'Hidden from students'}</p>
                <p><strong>Questions:</strong> {questions.length} questions configured</p>
              </div>
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                <p className="text-sm text-blue-800">
                  ℹ️ After creating this exam, students will be able to upload their answer papers. 
                  You'll be notified when all students have submitted.
                </p>
              </div>
              <div className="flex gap-3">
                <Button variant="outline" onClick={() => setStep(3)} className="flex-1">
                  Back
                </Button>
                <Button
                  onClick={handleCreate}
                  disabled={creating}
                  className="flex-1"
                >
                  {creating ? 'Creating...' : 'Create Exam'}
                </Button>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
};

export default CreateStudentExam;
