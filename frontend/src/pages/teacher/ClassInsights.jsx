import { useState, useEffect } from "react";
import axios from "axios";
import { API } from "../../App";
import Layout from "../../components/Layout";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "../../components/ui/card";
import { Button } from "../../components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "../../components/ui/select";
import { Badge } from "../../components/ui/badge";
import { Progress } from "../../components/ui/progress";
import { 
  Lightbulb, 
  TrendingUp, 
  TrendingDown, 
  CheckCircle,
  AlertTriangle,
  BookOpen,
  Target,
  RefreshCw
} from "lucide-react";

export default function ClassInsights({ user }) {
  const [insights, setInsights] = useState(null);
  const [loading, setLoading] = useState(true);
  const [exams, setExams] = useState([]);
  const [selectedExam, setSelectedExam] = useState("");

  useEffect(() => {
    fetchExams();
  }, []);

  useEffect(() => {
    fetchInsights();
  }, [selectedExam]);

  const fetchExams = async () => {
    try {
      const response = await axios.get(`${API}/exams`);
      setExams(response.data);
    } catch (error) {
      console.error("Error fetching exams:", error);
    }
  };

  const fetchInsights = async () => {
    setLoading(true);
    try {
      const params = selectedExam ? `?exam_id=${selectedExam}` : "";
      const response = await axios.get(`${API}/analytics/insights${params}`);
      setInsights(response.data);
    } catch (error) {
      console.error("Error fetching insights:", error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <Layout user={user}>
      <div className="space-y-6" data-testid="class-insights-page">
        {/* Header */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>
            <h1 className="text-2xl font-bold text-foreground">Class Feedback & Insights</h1>
            <p className="text-muted-foreground">AI-generated analysis and recommendations</p>
          </div>
          
          <div className="flex items-center gap-3">
            <Select value={selectedExam} onValueChange={setSelectedExam}>
              <SelectTrigger className="w-64" data-testid="exam-select">
                <SelectValue placeholder="All Exams" />
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
            
            <Button variant="outline" onClick={fetchInsights} disabled={loading}>
              <RefreshCw className={`w-4 h-4 mr-2 ${loading ? "animate-spin" : ""}`} />
              Refresh
            </Button>
          </div>
        </div>

        {loading ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {[1, 2, 3, 4].map(i => (
              <Card key={i} className="animate-pulse">
                <CardContent className="p-6">
                  <div className="h-32 bg-muted rounded" />
                </CardContent>
              </Card>
            ))}
          </div>
        ) : (
          <>
            {/* Summary Card */}
            <Card className="bg-gradient-to-r from-orange-50 to-white border-orange-100 animate-fade-in">
              <CardContent className="p-6">
                <div className="flex items-start gap-4">
                  <div className="p-3 rounded-xl bg-primary/10">
                    <Lightbulb className="w-6 h-6 text-primary" />
                  </div>
                  <div>
                    <h3 className="text-lg font-semibold mb-2">Overall Assessment</h3>
                    <p className="text-muted-foreground leading-relaxed">
                      {insights?.summary || "No analysis available yet. Upload and grade some papers to see insights."}
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>

            {/* Strengths & Weaknesses */}
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* Strengths */}
              <Card className="animate-fade-in stagger-1">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-green-700">
                    <TrendingUp className="w-5 h-5" />
                    Strengths
                  </CardTitle>
                  <CardDescription>Areas where the class is performing well</CardDescription>
                </CardHeader>
                <CardContent>
                  {insights?.strengths?.length === 0 ? (
                    <p className="text-muted-foreground text-center py-4">No strengths identified yet</p>
                  ) : (
                    <div className="space-y-3">
                      {insights?.strengths?.map((strength, index) => (
                        <div key={index} className="flex items-center gap-3 p-3 bg-green-50 rounded-lg">
                          <CheckCircle className="w-5 h-5 text-green-600 flex-shrink-0" />
                          <span className="text-sm">{strength}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* Weaknesses */}
              <Card className="animate-fade-in stagger-2">
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-red-700">
                    <TrendingDown className="w-5 h-5" />
                    Areas for Improvement
                  </CardTitle>
                  <CardDescription>Topics that need more attention</CardDescription>
                </CardHeader>
                <CardContent>
                  {insights?.weaknesses?.length === 0 ? (
                    <p className="text-muted-foreground text-center py-4">No weaknesses identified</p>
                  ) : (
                    <div className="space-y-3">
                      {insights?.weaknesses?.map((weakness, index) => (
                        <div key={index} className="flex items-center gap-3 p-3 bg-red-50 rounded-lg">
                          <AlertTriangle className="w-5 h-5 text-red-600 flex-shrink-0" />
                          <span className="text-sm">{weakness}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </div>

            {/* Recommendations */}
            <Card className="animate-fade-in stagger-3">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Target className="w-5 h-5 text-primary" />
                  Teaching Recommendations
                </CardTitle>
                <CardDescription>AI-suggested actions to improve class performance</CardDescription>
              </CardHeader>
              <CardContent>
                {insights?.recommendations?.length === 0 ? (
                  <p className="text-muted-foreground text-center py-4">No recommendations available</p>
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                    {insights?.recommendations?.map((rec, index) => (
                      <div 
                        key={index} 
                        className="p-4 border rounded-xl hover:border-primary/50 hover:bg-primary/5 transition-all cursor-pointer"
                      >
                        <div className="flex items-start gap-3">
                          <div className="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center flex-shrink-0">
                            <span className="text-sm font-bold text-primary">{index + 1}</span>
                          </div>
                          <p className="text-sm">{rec}</p>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* Action Items Checklist */}
            <Card className="animate-fade-in stagger-4">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <BookOpen className="w-5 h-5 text-blue-600" />
                  Action Items
                </CardTitle>
                <CardDescription>Track your follow-up activities</CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {[
                    "Review papers with scores below 40%",
                    "Schedule remedial session for weak topics",
                    "Prepare practice material for struggling questions",
                    "Update parents on student progress"
                  ].map((item, index) => (
                    <div 
                      key={index}
                      className="flex items-center gap-3 p-3 bg-muted/50 rounded-lg hover:bg-muted transition-colors cursor-pointer"
                    >
                      <input 
                        type="checkbox" 
                        className="w-4 h-4 rounded border-gray-300 text-primary focus:ring-primary"
                      />
                      <span className="text-sm">{item}</span>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </>
        )}
      </div>
    </Layout>
  );
}
