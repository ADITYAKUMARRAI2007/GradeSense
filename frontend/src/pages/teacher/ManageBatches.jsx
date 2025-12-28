import { useState, useEffect } from "react";
import axios from "axios";
import { API } from "../../App";
import Layout from "../../components/Layout";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Input } from "../../components/ui/input";
import { Label } from "../../components/ui/label";
import { Badge } from "../../components/ui/badge";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger, DialogFooter } from "../../components/ui/dialog";
import { ScrollArea } from "../../components/ui/scroll-area";
import { toast } from "sonner";
import { 
  Plus, 
  Search, 
  Edit2, 
  Trash2, 
  Users,
  BookOpen,
  FileText,
  ChevronRight,
  AlertTriangle,
  Lock,
  LockOpen,
  Archive
} from "lucide-react";
import { useNavigate } from "react-router-dom";

export default function ManageBatches({ user }) {
  const [batches, setBatches] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingBatch, setEditingBatch] = useState(null);
  const [batchName, setBatchName] = useState("");
  const [selectedBatch, setSelectedBatch] = useState(null);
  const [batchDetails, setBatchDetails] = useState(null);
  const navigate = useNavigate();

  const [showClosed, setShowClosed] = useState(false);

  useEffect(() => {
    fetchBatches();
  }, []);

  const fetchBatches = async () => {
    try {
      const response = await axios.get(`${API}/batches`);
      setBatches(response.data);
    } catch (error) {
      console.error("Error fetching batches:", error);
    } finally {
      setLoading(false);
    }
  };

  const fetchBatchDetails = async (batchId) => {
    try {
      const response = await axios.get(`${API}/batches/${batchId}`);
      setBatchDetails(response.data);
      setSelectedBatch(batchId);
    } catch (error) {
      toast.error("Failed to load batch details");
    }
  };

  const handleSubmit = async () => {
    if (!batchName.trim()) {
      toast.error("Batch name is required");
      return;
    }

    try {
      if (editingBatch) {
        await axios.put(`${API}/batches/${editingBatch.batch_id}`, { name: batchName });
        toast.success("Batch updated");
      } else {
        await axios.post(`${API}/batches`, { name: batchName });
        toast.success("Batch created");
      }
      setDialogOpen(false);
      setEditingBatch(null);
      setBatchName("");
      fetchBatches();
    } catch (error) {
      toast.error(error.response?.data?.detail || "Failed to save batch");
    }
  };

  const handleDelete = async (batch) => {
    if (batch.student_count > 0) {
      toast.error(`Cannot delete batch with ${batch.student_count} students. Remove students first.`);
      return;
    }
    
    if (!confirm(`Are you sure you want to delete "${batch.name}"?`)) return;
    
    try {
      await axios.delete(`${API}/batches/${batch.batch_id}`);
      toast.success("Batch deleted");
      fetchBatches();
      if (selectedBatch === batch.batch_id) {
        setSelectedBatch(null);
        setBatchDetails(null);
      }
    } catch (error) {
      toast.error(error.response?.data?.detail || "Failed to delete batch");
    }
  };

  const openEditDialog = (batch) => {
    setEditingBatch(batch);
    setBatchName(batch.name);
    setDialogOpen(true);
  };

  const openNewDialog = () => {
    setEditingBatch(null);
    setBatchName("");
    setDialogOpen(true);
  };

  const filteredBatches = batches.filter(b => {
    // Filter by search query
    if (!b.name.toLowerCase().includes(searchQuery.toLowerCase())) return false;
    // Filter by closed status
    if (!showClosed && b.status === "closed") return false;
    return true;
  });

  return (
    <Layout user={user}>
      <div className="space-y-4 lg:space-y-6" data-testid="manage-batches-page">
        {/* Header */}
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h1 className="text-xl lg:text-2xl font-bold text-foreground">Manage Batches</h1>


  const handleCloseBatch = async (batch) => {
    if (!confirm(`Close/archive "${batch.name}"?\n\nThis will:\n- Prevent adding new exams\n- Prevent adding/removing students\n- Keep all data accessible\n- You can reopen it later if needed`)) {
      return;
    }

    try {
      await axios.put(`${API}/batches/${batch.batch_id}/close`);
      toast.success("Batch closed successfully");
      fetchBatches();
    } catch (error) {
      toast.error(error.response?.data?.detail || "Failed to close batch");
    }
  };

  const handleReopenBatch = async (batch) => {
    if (!confirm(`Reopen "${batch.name}"?`)) {
      return;
    }

    try {
      await axios.put(`${API}/batches/${batch.batch_id}/reopen`);
      toast.success("Batch reopened successfully");
      fetchBatches();
    } catch (error) {
      toast.error(error.response?.data?.detail || "Failed to reopen batch");
    }
  };

            <p className="text-sm text-muted-foreground">Create and manage class batches</p>
          </div>
          
          <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
            <DialogTrigger asChild>
              <Button onClick={openNewDialog} data-testid="add-batch-btn">
                <Plus className="w-4 h-4 mr-2" />
                New Batch
              </Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>{editingBatch ? "Edit Batch" : "Create New Batch"}</DialogTitle>
              </DialogHeader>
              <form onSubmit={(e) => {
                e.preventDefault();
                handleSubmit();
              }}>
                <div className="py-4">
                  <Label htmlFor="batch-name-field">Batch Name *</Label>
                  <Input 
                    id="batch-name-field"
                    name="batchName"
                    value={batchName}
                    onChange={(e) => setBatchName(e.target.value)}
                    placeholder="e.g., Class 10-A, Grade 5 Science"
                    className="mt-2"
                    data-testid="batch-name-input"
                    required
                  />
                </div>
                <DialogFooter>
                  <Button type="button" variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
                  <Button type="submit" disabled={!batchName.trim()} data-testid="save-batch-btn">
                    {editingBatch ? "Save Changes" : "Create Batch"}
                  </Button>
                </DialogFooter>
              </form>
            </DialogContent>
          </Dialog>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 lg:gap-6">
          {/* Batches List */}
          <div className="lg:col-span-1">
            <Card>
              <CardHeader className="p-4">
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <Input 
                    placeholder="Search batches..."
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="pl-9"
                  />
                </div>
              </CardHeader>
              <CardContent className="p-0">
                <ScrollArea className="h-[400px] lg:h-[500px]">
                  {loading ? (
                    <div className="p-4 space-y-2">
                      {[1, 2, 3, 4].map(i => (
                        <div key={i} className="h-16 bg-muted animate-pulse rounded-lg" />
                      ))}
                    </div>
                  ) : filteredBatches.length === 0 ? (
                    <div className="text-center py-8 px-4">
                      <BookOpen className="w-12 h-12 mx-auto text-muted-foreground/50 mb-3" />
                      <p className="text-muted-foreground">No batches found</p>
                      <Button variant="outline" className="mt-3" onClick={openNewDialog}>
                        <Plus className="w-4 h-4 mr-2" />
                        Create first batch
                      </Button>
                    </div>
                  ) : (
                    <div className="p-2 space-y-1">
                      {filteredBatches.map((batch) => (
                        <div 
                          key={batch.batch_id}
                          onClick={() => fetchBatchDetails(batch.batch_id)}
                          className={`p-3 rounded-lg cursor-pointer transition-all flex items-center justify-between ${
                            selectedBatch === batch.batch_id
                              ? "bg-primary/10 border border-primary"
                              : "hover:bg-muted"
                          }`}
                          data-testid={`batch-${batch.batch_id}`}
                        >
                          <div className="flex items-center gap-3">
                            <div className="w-10 h-10 rounded-lg bg-primary/10 flex items-center justify-center">
                              <BookOpen className="w-5 h-5 text-primary" />
                            </div>
                            <div>
                              <p className="font-medium">{batch.name}</p>
                              <p className="text-xs text-muted-foreground">
                                {batch.student_count || 0} students
                              </p>
                            </div>
                          </div>
                          <ChevronRight className="w-4 h-4 text-muted-foreground" />
                        </div>
                      ))}
                    </div>
                  )}
                </ScrollArea>
              </CardContent>
            </Card>
          </div>

          {/* Batch Details */}
          <div className="lg:col-span-2">
            {!batchDetails ? (
              <Card className="h-full flex items-center justify-center min-h-[400px]">
                <div className="text-center">
                  <BookOpen className="w-16 h-16 mx-auto text-muted-foreground/30 mb-4" />
                  <p className="text-lg font-medium text-muted-foreground">Select a batch</p>
                  <p className="text-sm text-muted-foreground">Click on a batch to view details</p>
                </div>
              </Card>
            ) : (
              <Card>
                <CardHeader className="flex flex-row items-start justify-between">
                  <div>
                    <CardTitle className="text-xl">{batchDetails.name}</CardTitle>
                    <CardDescription>
                      Created {new Date(batchDetails.created_at).toLocaleDateString()}
                    </CardDescription>
                  </div>
                  <div className="flex gap-2">
                    <Button 
                      variant="outline" 
                      size="sm"
                      onClick={() => openEditDialog(batchDetails)}
                    >
                      <Edit2 className="w-4 h-4 mr-1" />
                      Edit
                    </Button>
                    <Button 
                      variant="outline" 
                      size="sm"
                      onClick={() => handleDelete(batchDetails)}
                      className="text-destructive hover:text-destructive"
                      disabled={batchDetails.student_count > 0}
                    >
                      <Trash2 className="w-4 h-4 mr-1" />
                      Delete
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-6">
                  {/* Stats */}
                  <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
                    <div className="p-4 bg-blue-50 rounded-lg">
                      <div className="flex items-center gap-2">
                        <Users className="w-5 h-5 text-blue-600" />
                        <span className="text-2xl font-bold text-blue-700">
                          {batchDetails.student_count || 0}
                        </span>
                      </div>
                      <p className="text-sm text-blue-600 mt-1">Students</p>
                    </div>
                    <div className="p-4 bg-orange-50 rounded-lg">
                      <div className="flex items-center gap-2">
                        <FileText className="w-5 h-5 text-orange-600" />
                        <span className="text-2xl font-bold text-orange-700">
                          {batchDetails.exams?.length || 0}
                        </span>
                      </div>
                      <p className="text-sm text-orange-600 mt-1">Exams</p>
                    </div>
                  </div>

                  {/* Warning if empty */}
                  {batchDetails.student_count === 0 && (
                    <div className="p-4 bg-yellow-50 border border-yellow-200 rounded-lg flex items-start gap-3">
                      <AlertTriangle className="w-5 h-5 text-yellow-600 flex-shrink-0" />
                      <div>
                        <p className="font-medium text-yellow-800">Empty Batch</p>
                        <p className="text-sm text-yellow-700">This batch has no students. Add students or delete this batch.</p>
                        <Button 
                          variant="outline" 
                          size="sm" 
                          className="mt-2"
                          onClick={() => navigate("/teacher/students")}
                        >
                          Add Students
                        </Button>
                      </div>
                    </div>
                  )}

                  {/* Students List */}
                  {batchDetails.students_list?.length > 0 && (
                    <div>
                      <h3 className="font-semibold mb-3 flex items-center gap-2">
                        <Users className="w-4 h-4" />
                        Students ({batchDetails.students_list.length})
                      </h3>
                      <div className="space-y-2 max-h-48 overflow-y-auto">
                        {batchDetails.students_list.map((student) => (
                          <div 
                            key={student.user_id}
                            className="flex items-center justify-between p-3 bg-muted/50 rounded-lg"
                          >
                            <div className="flex items-center gap-3">
                              <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center">
                                <span className="text-sm font-medium text-primary">
                                  {student.name?.charAt(0)}
                                </span>
                              </div>
                              <div>
                                <p className="font-medium text-sm">{student.name}</p>
                                <p className="text-xs text-muted-foreground">{student.email}</p>
                              </div>
                            </div>
                            {student.student_id && (
                              <Badge variant="outline" className="text-xs">
                                {student.student_id}
                              </Badge>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Exams List */}
                  {batchDetails.exams?.length > 0 && (
                    <div>
                      <h3 className="font-semibold mb-3 flex items-center gap-2">
                        <FileText className="w-4 h-4" />
                        Exams ({batchDetails.exams.length})
                      </h3>
                      <div className="space-y-2">
                        {batchDetails.exams.map((exam) => (
                          <div 
                            key={exam.exam_id}
                            className="flex items-center justify-between p-3 bg-muted/50 rounded-lg"
                          >
                            <span className="font-medium text-sm">{exam.exam_name}</span>
                            <Badge 
                              className={
                                exam.status === "completed" 
                                  ? "bg-green-100 text-green-700" 
                                  : "bg-yellow-100 text-yellow-700"
                              }
                            >
                              {exam.status}
                            </Badge>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      </div>
    </Layout>
  );
}
