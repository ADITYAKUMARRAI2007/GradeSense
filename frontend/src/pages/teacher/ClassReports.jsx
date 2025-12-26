import { useState, useEffect } from "react";
import axios from "axios";
import { API } from "../../App";
import Layout from "../../components/Layout";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Badge } from "../../components/ui/badge";
import { 
  BarChart, 
  Bar, 
  XAxis, 
  YAxis, 
  CartesianGrid, 
  Tooltip, 
  ResponsiveContainer,
  PieChart,
  Pie,
  Cell,
  LineChart,
  Line,
  Legend
} from "recharts";
import { 
  Download, 
  Users, 
  TrendingUp, 
  TrendingDown, 
  Award,
  AlertTriangle,
  FileSpreadsheet
} from "lucide-react";

const COLORS = ['#F97316', '#3B82F6', '#22C55E', '#EAB308', '#EF4444'];

export default function ClassReports({ user }) {
  const [report, setReport] = useState(null);
  const [loading, setLoading] = useState(true);
  const [batches, setBatches] = useState([]);
  const [subjects, setSubjects] = useState([]);
  const [exams, setExams] = useState([]);
  const [filters, setFilters] = useState({
    batch_id: "",
    subject_id: "",
    exam_id: ""
  });

  useEffect(() => {
    fetchFiltersData();
  }, []);

  useEffect(() => {
    fetchReport();
  }, [filters]);

  const fetchFiltersData = async () => {
    try {
      const [batchesRes, subjectsRes, examsRes] = await Promise.all([
        axios.get(`${API}/batches`),
        axios.get(`${API}/subjects`),
        axios.get(`${API}/exams`)
      ]);
      setBatches(batchesRes.data);
      setSubjects(subjectsRes.data);
      setExams(examsRes.data);
    } catch (error) {
      console.error("Error fetching filter data:", error);
    }
  };

  const fetchReport = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (filters.batch_id) params.append("batch_id", filters.batch_id);
      if (filters.subject_id) params.append("subject_id", filters.subject_id);
      if (filters.exam_id) params.append("exam_id", filters.exam_id);
      
      const response = await axios.get(`${API}/analytics/class-report?${params}`);
      setReport(response.data);
    } catch (error) {
      console.error("Error fetching report:", error);
    } finally {
      setLoading(false);
    }
  };

  const overview = report?.overview || {};
  const scoreDistribution = report?.score_distribution || [];
  const topPerformers = report?.top_performers || [];
  const needsAttention = report?.needs_attention || [];
  const questionAnalysis = report?.question_analysis || [];

  const exportReport = () => {
    // Create CSV content
    const csvContent = [
      ["Class Report"],
      [""],
      ["Overview"],
      ["Total Students", overview.total_students],
      ["Average Score", `${overview.avg_score}%`],
      ["Highest Score", `${overview.highest_score}%`],
      ["Lowest Score", `${overview.lowest_score}%`],
      ["Pass Percentage", `${overview.pass_percentage}%`],
      [""],
      ["Top Performers"],
      ["Name", "Score", "Percentage"],
      ...topPerformers.map(p => [p.name, p.score, `${p.percentage}%`]),
      [""],
      ["Needs Attention"],
      ["Name", "Score", "Percentage"],
      ...needsAttention.map(p => [p.name, p.score, `${p.percentage}%`])
    ].map(row => row.join(",")).join("\n");

    const blob = new Blob([csvContent], { type: "text/csv" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "class_report.csv";
    a.click();
  };

  return (
    <Layout user={user}>
      <div className="space-y-4 lg:space-y-6" data-testid="class-reports-page">
        {/* Filters */}
        <Card>
          <CardContent className="p-3 lg:p-4">
            <div className="flex flex-col sm:flex-row sm:flex-wrap items-stretch sm:items-center gap-3 lg:gap-4">
              <Select 
                value={filters.batch_id || "all"} 
                onValueChange={(v) => setFilters(prev => ({ ...prev, batch_id: v === "all" ? "" : v }))}
              >
                <SelectTrigger className="w-full sm:w-40 lg:w-48 text-sm" data-testid="batch-filter">
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

              <Select 
                value={filters.subject_id || "all"} 
                onValueChange={(v) => setFilters(prev => ({ ...prev, subject_id: v === "all" ? "" : v }))}
              >
                <SelectTrigger className="w-full sm:w-40 lg:w-48 text-sm" data-testid="subject-filter">
                  <SelectValue placeholder="All Subjects" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Subjects</SelectItem>
                  {subjects.map(subject => (
                    <SelectItem key={subject.subject_id} value={subject.subject_id}>
                      {subject.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>

              <Select 
                value={filters.exam_id || "all"} 
                onValueChange={(v) => setFilters(prev => ({ ...prev, exam_id: v === "all" ? "" : v }))}
              >
                <SelectTrigger className="w-full sm:w-40 lg:w-48 text-sm" data-testid="exam-filter">
                  <SelectValue placeholder="All Exams" />
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

              <div className="sm:ml-auto">
                <Button variant="outline" onClick={exportReport} data-testid="export-btn" className="w-full sm:w-auto text-sm">
                  <Download className="w-4 h-4 mr-2" />
                  Export
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Overview Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3 lg:gap-4">
          <Card className="animate-fade-in">
            <CardContent className="p-3 lg:p-4">
              <div className="flex items-center gap-2 lg:gap-3">
                <div className="p-1.5 lg:p-2 rounded-lg bg-blue-50 flex-shrink-0">
                  <Users className="w-4 h-4 lg:w-5 lg:h-5 text-blue-600" />
                </div>
                <div className="min-w-0">
                  <p className="text-lg lg:text-2xl font-bold">{overview.total_students || 0}</p>
                  <p className="text-xs lg:text-sm text-muted-foreground truncate">Total Students</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="animate-fade-in stagger-1">
            <CardContent className="p-3 lg:p-4">
              <div className="flex items-center gap-2 lg:gap-3">
                <div className="p-1.5 lg:p-2 rounded-lg bg-orange-50 flex-shrink-0">
                  <TrendingUp className="w-4 h-4 lg:w-5 lg:h-5 text-orange-600" />
                </div>
                <div className="min-w-0">
                  <p className="text-lg lg:text-2xl font-bold">{overview.avg_score || 0}%</p>
                  <p className="text-xs lg:text-sm text-muted-foreground truncate">Class Average</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="animate-fade-in stagger-2">
            <CardContent className="p-3 lg:p-4">
              <div className="flex items-center gap-2 lg:gap-3">
                <div className="p-1.5 lg:p-2 rounded-lg bg-green-50 flex-shrink-0">
                  <Award className="w-4 h-4 lg:w-5 lg:h-5 text-green-600" />
                </div>
                <div className="min-w-0">
                  <p className="text-lg lg:text-2xl font-bold">{overview.highest_score || 0}%</p>
                  <p className="text-xs lg:text-sm text-muted-foreground truncate">Highest Score</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="animate-fade-in stagger-3">
            <CardContent className="p-3 lg:p-4">
              <div className="flex items-center gap-2 lg:gap-3">
                <div className="p-1.5 lg:p-2 rounded-lg bg-red-50 flex-shrink-0">
                  <TrendingDown className="w-4 h-4 lg:w-5 lg:h-5 text-red-600" />
                </div>
                <div className="min-w-0">
                  <p className="text-lg lg:text-2xl font-bold">{overview.lowest_score || 0}%</p>
                  <p className="text-xs lg:text-sm text-muted-foreground truncate">Lowest Score</p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="animate-fade-in stagger-4 col-span-2 lg:col-span-1">
            <CardContent className="p-3 lg:p-4">
              <div className="flex items-center gap-2 lg:gap-3">
                <div className="p-1.5 lg:p-2 rounded-lg bg-purple-50 flex-shrink-0">
                  <FileSpreadsheet className="w-4 h-4 lg:w-5 lg:h-5 text-purple-600" />
                </div>
                <div className="min-w-0">
                  <p className="text-lg lg:text-2xl font-bold">{overview.pass_percentage || 0}%</p>
                  <p className="text-xs lg:text-sm text-muted-foreground truncate">Pass Rate</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Charts */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 lg:gap-6">
          {/* Score Distribution */}
          <Card className="animate-fade-in stagger-2">
            <CardHeader className="p-4 lg:p-6">
              <CardTitle className="text-base lg:text-lg">Score Distribution</CardTitle>
            </CardHeader>
            <CardContent className="p-4 lg:p-6 pt-0">
              <div className="h-60 lg:h-80">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={scoreDistribution}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                    <XAxis dataKey="range" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} />
                    <Tooltip 
                      contentStyle={{ 
                        backgroundColor: 'white', 
                        border: '1px solid #E2E8F0',
                        borderRadius: '8px',
                        fontSize: '12px'
                      }} 
                    />
                    <Bar dataKey="count" fill="#F97316" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>

          {/* Question Analysis */}
          <Card className="animate-fade-in stagger-3">
            <CardHeader className="p-4 lg:p-6">
              <CardTitle className="text-base lg:text-lg">Question-wise Performance</CardTitle>
            </CardHeader>
            <CardContent className="p-4 lg:p-6 pt-0">
              <div className="h-60 lg:h-80">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={questionAnalysis} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                    <XAxis type="number" domain={[0, 100]} tick={{ fontSize: 10 }} />
                    <YAxis dataKey="question" type="category" tick={{ fontSize: 10 }} width={40} tickFormatter={(v) => `Q${v}`} />
                    <Tooltip 
                      contentStyle={{ 
                        backgroundColor: 'white', 
                        border: '1px solid #E2E8F0',
                        borderRadius: '8px',
                        fontSize: '12px'
                      }}
                      formatter={(value) => [`${value}%`, 'Average']}
                    />
                    <Bar 
                      dataKey="percentage" 
                      radius={[0, 4, 4, 0]}
                    >
                      {questionAnalysis.map((entry, index) => (
                        <Cell 
                          key={`cell-${index}`} 
                          fill={entry.percentage >= 70 ? '#22C55E' : entry.percentage >= 50 ? '#F97316' : '#EF4444'} 
                        />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Tables */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Top Performers */}
          <Card className="animate-fade-in stagger-4">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Award className="w-5 h-5 text-green-600" />
                Top Performers
              </CardTitle>
            </CardHeader>
            <CardContent>
              {topPerformers.length === 0 ? (
                <p className="text-center text-muted-foreground py-4">No data available</p>
              ) : (
                <div className="space-y-3">
                  {topPerformers.map((student, index) => (
                    <div key={index} className="flex items-center justify-between p-3 bg-green-50/50 rounded-lg">
                      <div className="flex items-center gap-3">
                        <span className="w-6 h-6 rounded-full bg-green-100 flex items-center justify-center text-sm font-bold text-green-700">
                          {index + 1}
                        </span>
                        <span className="font-medium">{student.name}</span>
                      </div>
                      <Badge className="bg-green-100 text-green-700">
                        {student.percentage}%
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Needs Attention */}
          <Card className="animate-fade-in stagger-5">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <AlertTriangle className="w-5 h-5 text-yellow-600" />
                Needs Attention
              </CardTitle>
            </CardHeader>
            <CardContent>
              {needsAttention.length === 0 ? (
                <p className="text-center text-muted-foreground py-4">All students performing well!</p>
              ) : (
                <div className="space-y-3">
                  {needsAttention.map((student, index) => (
                    <div key={index} className="flex items-center justify-between p-3 bg-yellow-50/50 rounded-lg">
                      <div className="flex items-center gap-3">
                        <span className="w-6 h-6 rounded-full bg-yellow-100 flex items-center justify-center text-sm font-bold text-yellow-700">
                          {index + 1}
                        </span>
                        <span className="font-medium">{student.name}</span>
                      </div>
                      <Badge className="bg-red-100 text-red-700">
                        {student.percentage}%
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </Layout>
  );
}
