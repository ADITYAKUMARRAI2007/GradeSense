import { useState, useEffect } from "react";
import axios from "axios";
import { API } from "../../App";
import Layout from "../../components/Layout";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Badge } from "../../components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "../../components/ui/dialog";
import { toast } from "sonner";
import { 
  Plus, 
  Search, 
  Edit2, 
  Trash2, 
  Users,
  Upload,
  Mail,
  BookOpen
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
            <p className="text-sm text-muted-foreground">Add, edit, and organize your students</p>
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
                      placeholder="STU001"
                    />
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
                <DialogFooter>
                  <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
                  <Button onClick={handleSubmit} disabled={!formData.name || !formData.email} data-testid="save-student-btn">
                    {editingStudent ? "Save Changes" : "Add Student"}
                  </Button>
                </DialogFooter>
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
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Users className="w-5 h-5" />
              Students ({filteredStudents.length})
            </CardTitle>
          </CardHeader>
          <CardContent>
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
                    className="flex items-center justify-between p-4 bg-muted/30 rounded-lg hover:bg-muted/50 transition-colors"
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
                        onClick={() => openEditDialog(student)}
                      >
                        <Edit2 className="w-4 h-4" />
                      </Button>
                      <Button 
                        variant="ghost" 
                        size="icon"
                        onClick={() => handleDelete(student.user_id)}
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
      </div>
    </Layout>
  );
}
