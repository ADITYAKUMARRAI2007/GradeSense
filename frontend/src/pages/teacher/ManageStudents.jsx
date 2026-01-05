import { useState, useEffect } from "react";
import axios from "axios";
import { API } from "../../App";
import Layout from "../../components/Layout";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Badge } from "../../components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "../../components/ui/dialog";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "../../components/ui/sheet";
import { ScrollArea } from "../../components/ui/scroll-area";
import { Progress } from "../../components/ui/progress";
import { toast } from "sonner";
import { 
  BarChart, 
  Bar, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer,
  LineChart,
  Line
} from "recharts";
import { 
  Plus, 
  Search, 
  Edit2, 
  Trash2, 
  Users,
  Upload,
  Mail,
  BookOpen,
  TrendingUp,
  TrendingDown,
  Minus,
  Target,
  AlertTriangle,
  CheckCircle,
  X,
  Lightbulb
} from "lucide-react";

export default function ManageStudents({ user }) {
  const [students, setStudents] = useState([]);
  const [batches, setBatches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedBatch, setSelectedBatch] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingStudent, setEditingStudent] = useState(null);
  const [formData, setFormData] = useState({
    name: "",
    email: "",
    student_id: "",
    batches: []
  });
  
  // Student detail view
  const [selectedStudent, setSelectedStudent] = useState(null);
  const [studentDetails, setStudentDetails] = useState(null);
  const [detailSheetOpen, setDetailSheetOpen] = useState(false);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    try {
      const [studentsRes, batchesRes] = await Promise.all([
        axios.get(`${API}/students`),
        axios.get(`${API}/batches`)
      ]);
      setStudents(studentsRes.data);
      setBatches(batchesRes.data);
    } catch (error) {
      console.error("Error fetching data:", error);
    } finally {
      setLoading(false);
    }
  };

  const fetchStudentDetails = async (studentId) => {
    try {
      const response = await axios.get(`${API}/students/${studentId}`);
      setStudentDetails(response.data);
      setSelectedStudent(studentId);
      setDetailSheetOpen(true);
    } catch (error) {
      toast.error("Failed to load student details");
    }
  };

  const handleSubmit = async () => {
    try {
      if (editingStudent) {
        await axios.put(`${API}/students/${editingStudent.user_id}`, formData);
        toast.success("Student updated");
      } else {
        await axios.post(`${API}/students`, formData);
        toast.success("Student created");
      }
      setDialogOpen(false);
      setEditingStudent(null);
      setFormData({ name: "", email: "", student_id: "", batches: [] });
      fetchData();
    } catch (error) {
      toast.error(error.response?.data?.detail || "Failed to save student");
    }
  };

  const handleDelete = async (studentId) => {
    if (!confirm("Are you sure you want to delete this student?")) return;
    
    try {
      await axios.delete(`${API}/students/${studentId}`);
      toast.success("Student deleted");
      fetchData();
    } catch (error) {
      toast.error("Failed to delete student");
    }
  };

  const openEditDialog = (student) => {
    setEditingStudent(student);
    setFormData({
      name: student.name,
      email: student.email,
      student_id: student.student_id || "",
      batches: student.batches || []
    });
    setDialogOpen(true);
  };

  const openNewDialog = () => {
    setEditingStudent(null);
    setFormData({ name: "", email: "", student_id: "", batches: [] });
    setDialogOpen(true);
  };

  const filteredStudents = students.filter(s => {
    const matchesSearch = s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
                          s.email.toLowerCase().includes(searchQuery.toLowerCase());
    const matchesBatch = !selectedBatch || s.batches?.includes(selectedBatch);
    return matchesSearch && matchesBatch;
  });

  const getBatchName = (batchId) => {
    return batches.find(b => b.batch_id === batchId)?.name || batchId;
  };

  return (
    <Layout user={user}>
      <div className="space-y-4 lg:space-y-6" data-testid="manage-students-page">
        {/* Header */}
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between lg:gap-4">
          <div>
            <h1 className="text-xl lg:text-2xl font-bold text-foreground">Manage Students</h1>
            <p className="text-sm text-muted-foreground">Add, edit, and view student performance</p>
          </div>
          
          <div className="flex items-center gap-2 lg:gap-3">
            <Button variant="outline" disabled className="text-xs lg:text-sm flex-1 lg:flex-none">
              <Upload className="w-4 h-4 mr-1 lg:mr-2" />
              <span className="hidden sm:inline">Bulk Import</span>
              <span className="sm:hidden">Import</span>
            </Button>
            <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
              <DialogTrigger asChild>
                <Button onClick={openNewDialog} data-testid="add-student-btn" className="text-xs lg:text-sm flex-1 lg:flex-none">
                  <Plus className="w-4 h-4 mr-1 lg:mr-2" />
                  Add Student
                </Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>{editingStudent ? "Edit Student" : "Add New Student"}</DialogTitle>
                </DialogHeader>
                <div className="space-y-4 py-4">
                  <div className="space-y-2">
                    <Label>Full Name *</Label>
                    <Input 
                      value={formData.name}
                      onChange={(e) => setFormData(prev => ({ ...prev, name: e.target.value }))}
                      placeholder="John Doe"
                      data-testid="student-name-input"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>Email *</Label>
                    <Input 
                      type="email"
                      value={formData.email}
                      onChange={(e) => setFormData(prev => ({ ...prev, email: e.target.value }))}
                      placeholder="john@example.com"
                      data-testid="student-email-input"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label>Student ID (Optional)</Label>
                    <Input 
                      value={formData.student_id}
                      onChange={(e) => setFormData(prev => ({ ...prev, student_id: e.target.value }))}
                      placeholder="STU001, ROLL42, A123"
                    />
                    <p className="text-xs text-muted-foreground">
                      3-20 alphanumeric characters. Auto-generated if left empty.
                    </p>
                  </div>
                  <div className="space-y-2">
                    <Label>Assign to Batches</Label>
                    <div className="flex flex-wrap gap-2 p-3 border rounded-lg min-h-[48px]">
                      {batches.map(batch => (
                        <Badge 
                          key={batch.batch_id}
                          variant={formData.batches.includes(batch.batch_id) ? "default" : "outline"}
                          className="cursor-pointer transition-all"
                          onClick={() => {
                            setFormData(prev => ({
                              ...prev,
                              batches: prev.batches.includes(batch.batch_id)
                                ? prev.batches.filter(b => b !== batch.batch_id)
                                : [...prev.batches, batch.batch_id]
                            }));
                          }}
                        >
                          {batch.name}
                        </Badge>
                      ))}
                      {batches.length === 0 && (
                        <span className="text-sm text-muted-foreground">No batches available</span>
                      )}
                    </div>
                  </div>
                </div>
                <form onSubmit={(e) => {
                  e.preventDefault();
                  handleSubmit();
                }}>
                  <DialogFooter>
                    <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
                    <Button type="submit" disabled={!formData.name || !formData.email} data-testid="save-student-btn">
                      {editingStudent ? "Save Changes" : "Add Student"}
                    </Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>
          </div>
        </div>

        {/* Filters */}
        <Card>
          <CardContent className="p-3 lg:p-4">
            <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 lg:gap-4">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <Input 
                  placeholder="Search by name or email..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="pl-9 text-sm"
                  data-testid="search-students-input"
                />
              </div>
              <Select value={selectedBatch || "all"} onValueChange={(v) => setSelectedBatch(v === "all" ? "" : v)}>
                <SelectTrigger className="w-full sm:w-48 text-sm">
                  <SelectValue placeholder="All Batches" />
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
            </div>
          </CardContent>
        </Card>

        {/* Students List */}
        <Card>
          <CardHeader className="p-4 lg:p-6">
            <CardTitle className="flex items-center gap-2 text-base lg:text-lg">
              <Users className="w-5 h-5" />
              Students ({filteredStudents.length})
            </CardTitle>
            <CardDescription>Click on a student to view detailed performance analytics</CardDescription>
          </CardHeader>
          <CardContent className="p-4 lg:p-6 pt-0">
            {loading ? (
              <div className="space-y-3">
                {[1, 2, 3, 4, 5].map(i => (
                  <div key={i} className="h-16 bg-muted animate-pulse rounded-lg" />
                ))}
              </div>
            ) : filteredStudents.length === 0 ? (
              <div className="text-center py-12">
                <Users className="w-12 h-12 mx-auto text-muted-foreground/50 mb-3" />
                <p className="text-muted-foreground">No students found</p>
                <Button variant="outline" className="mt-4" onClick={openNewDialog}>
                  <Plus className="w-4 h-4 mr-2" />
                  Add your first student
                </Button>
              </div>
            ) : (
              <div className="space-y-3">
                {filteredStudents.map((student) => (
                  <div 
                    key={student.user_id}
                    className="flex items-center justify-between p-4 bg-muted/30 rounded-lg hover:bg-muted/50 transition-colors cursor-pointer"
                    onClick={() => fetchStudentDetails(student.user_id)}
                    data-testid={`student-${student.user_id}`}
                  >
                    <div className="flex items-center gap-4">
                      <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center">
                        <span className="text-lg font-semibold text-primary">
                          {student.name.charAt(0)}
                        </span>
                      </div>
                      <div>
                        <div className="flex items-center gap-2">
                          <p className="font-medium">{student.name}</p>
                          {student.student_id && (
                            <Badge variant="outline" className="text-xs">
                              {student.student_id}
                            </Badge>
                          )}
                        </div>
                        <div className="flex items-center gap-2 mt-1">
                          <Mail className="w-3 h-3 text-muted-foreground" />
                          <span className="text-sm text-muted-foreground">{student.email}</span>
                        </div>
                        {student.batches?.length > 0 && (
                          <div className="flex items-center gap-1 mt-2">
                            <BookOpen className="w-3 h-3 text-muted-foreground" />
                            <div className="flex flex-wrap gap-1">
                              {student.batches.map(batchId => (
                                <Badge key={batchId} variant="secondary" className="text-xs">
                                  {getBatchName(batchId)}
                                </Badge>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    </div>
                    
                    <div className="flex items-center gap-2">
                      <Button 
                        variant="ghost" 
                        size="icon"
                        onClick={(e) => { e.stopPropagation(); openEditDialog(student); }}
                      >
                        <Edit2 className="w-4 h-4" />
                      </Button>
                      <Button 
                        variant="ghost" 
                        size="icon"
                        onClick={(e) => { e.stopPropagation(); handleDelete(student.user_id); }}
                        className="text-destructive hover:text-destructive"
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Student Detail Sheet */}
        <Sheet open={detailSheetOpen} onOpenChange={setDetailSheetOpen}>
          <SheetContent className="w-full sm:max-w-2xl overflow-y-auto">
            <SheetHeader>
              <SheetTitle className="flex items-center gap-3">
                {studentDetails && (
                  <>
                    <div className="w-12 h-12 rounded-full bg-primary/10 flex items-center justify-center">
                      <span className="text-xl font-bold text-primary">
                        {studentDetails.student?.name?.charAt(0)}
                      </span>
                    </div>
                    <div>
                      <h2 className="text-xl font-bold">{studentDetails.student?.name}</h2>
                      <p className="text-sm text-muted-foreground">{studentDetails.student?.email}</p>
                    </div>
                  </>
                )}
              </SheetTitle>
            </SheetHeader>

            {studentDetails && (
              <div className="mt-6 space-y-6">
                {/* Stats Cards */}
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                  <div className="p-3 bg-blue-50 rounded-lg">
                    <p className="text-2xl font-bold text-blue-700">{studentDetails.stats?.total_exams || 0}</p>
                    <p className="text-xs text-blue-600">Exams Taken</p>
                  </div>
                  <div className="p-3 bg-orange-50 rounded-lg">
                    <p className="text-2xl font-bold text-orange-700">{studentDetails.stats?.avg_percentage || 0}%</p>
                    <p className="text-xs text-orange-600">Average</p>
                  </div>
                  <div className="p-3 bg-green-50 rounded-lg">
                    <p className="text-2xl font-bold text-green-700">{studentDetails.stats?.highest_score || 0}%</p>
                    <p className="text-xs text-green-600">Highest</p>
                  </div>
                  <div className="p-3 bg-purple-50 rounded-lg flex items-center gap-2">
                    {studentDetails.stats?.trend >= 0 ? (
                      <TrendingUp className="w-5 h-5 text-green-600" />
                    ) : (
                      <TrendingDown className="w-5 h-5 text-red-600" />
                    )}
                    <div>
                      <p className="text-xl font-bold text-purple-700">
                        {studentDetails.stats?.trend > 0 ? "+" : ""}{studentDetails.stats?.trend || 0}%
                      </p>
                      <p className="text-xs text-purple-600">Trend</p>
                    </div>
                  </div>
                </div>

                {/* Subject Performance */}
                {Object.keys(studentDetails.subject_performance || {}).length > 0 && (
                  <div>
                    <h3 className="font-semibold mb-3 flex items-center gap-2">
                      <Target className="w-4 h-4" />
                      Subject-wise Performance
                    </h3>
                    <div className="space-y-3">
                      {Object.entries(studentDetails.subject_performance).map(([subject, data]) => (
                        <div key={subject} className="p-3 bg-muted/50 rounded-lg">
                          <div className="flex items-center justify-between mb-2">
                            <span className="font-medium">{subject}</span>
                            <Badge variant="outline">{data.total_exams} exams</Badge>
                          </div>
                          <div className="flex items-center gap-2">
                            <Progress value={data.average} className="flex-1 h-2" />
                            <span className="text-sm font-medium w-12">{data.average?.toFixed(1)}%</span>
                          </div>
                          <div className="flex justify-between mt-1 text-xs text-muted-foreground">
                            <span>Lowest: {data.lowest}%</span>
                            <span>Highest: {data.highest}%</span>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Weak & Strong Areas */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* Weak Areas */}
                  <div>
                    <h3 className="font-semibold mb-3 flex items-center gap-2 text-red-700">
                      <AlertTriangle className="w-4 h-4" />
                      Needs Improvement
                    </h3>
                    {studentDetails.weak_areas?.length > 0 ? (
                      <div className="space-y-2">
                        {studentDetails.weak_areas.map((area, idx) => (
                          <div key={idx} className="p-2 bg-red-50 rounded text-sm text-red-700">
                            {typeof area === 'string' ? area : `${area.question}: ${area.score}`}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground">No weak areas identified</p>
                    )}
                  </div>

                  {/* Strong Areas */}
                  <div>
                    <h3 className="font-semibold mb-3 flex items-center gap-2 text-green-700">
                      <CheckCircle className="w-4 h-4" />
                      Strengths
                    </h3>
                    {studentDetails.strong_areas?.length > 0 ? (
                      <div className="space-y-2">
                        {studentDetails.strong_areas.map((area, idx) => (
                          <div key={idx} className="p-2 bg-green-50 rounded text-sm text-green-700">
                            {typeof area === 'string' ? area : `${area.question}: ${area.score}`}
                          </div>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground">Complete more exams to identify strengths</p>
                    )}
                  </div>
                </div>

                {/* Recent Submissions */}
                {studentDetails.recent_submissions?.length > 0 && (
                  <div>
                    <h3 className="font-semibold mb-3">Recent Results</h3>
                    <div className="space-y-2">
                      {studentDetails.recent_submissions.slice(0, 5).map((sub, idx) => (
                        <div key={idx} className="flex items-center justify-between p-3 bg-muted/50 rounded-lg">
                          <span className="text-sm font-medium">{sub.exam_name || `Exam ${idx + 1}`}</span>
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
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Recommendations */}
                {studentDetails.recommendations?.length > 0 && (
                  <div>
                    <h3 className="font-semibold mb-3">Recommendations</h3>
                    <div className="space-y-2">
                      {studentDetails.recommendations.map((rec, idx) => (
                        <div key={idx} className="p-3 bg-primary/5 border border-primary/20 rounded-lg text-sm">
                          {rec}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </SheetContent>
        </Sheet>
      </div>
    </Layout>
  );
}
