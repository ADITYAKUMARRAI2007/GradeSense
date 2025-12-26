import { useState, useEffect } from "react";
import axios from "axios";
import { API } from "../../App";
import Layout from "../../components/Layout";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/ui/card";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
import { 
  FileText, 
  Users, 
  CheckCircle, 
  Clock, 
  TrendingUp,
  AlertCircle,
  ArrowRight,
  BookOpen
} from "lucide-react";
import { useNavigate } from "react-router-dom";

export default function TeacherDashboard({ user }) {
  const [analytics, setAnalytics] = useState(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    fetchDashboard();
  }, []);

  const fetchDashboard = async () => {
    try {
      const response = await axios.get(`${API}/analytics/dashboard`);
      setAnalytics(response.data);
    } catch (error) {
      console.error("Error fetching dashboard:", error);
    } finally {
      setLoading(false);
    }
  };

  const stats = analytics?.stats || {};
  const recentSubmissions = analytics?.recent_submissions || [];

  const statCards = [
    { 
      label: "Papers Graded", 
      value: stats.total_submissions || 0, 
      icon: FileText, 
      color: "text-primary",
      bgColor: "bg-orange-50"
    },
    { 
      label: "Active Batches", 
      value: stats.total_batches || 0, 
      icon: BookOpen, 
      color: "text-blue-600",
      bgColor: "bg-blue-50"
    },
    { 
      label: "Pending Reviews", 
      value: stats.pending_reviews || 0, 
      icon: Clock, 
      color: "text-yellow-600",
      bgColor: "bg-yellow-50"
    },
    { 
      label: "Class Average", 
      value: `${stats.avg_score || 0}%`, 
      icon: TrendingUp, 
      color: "text-green-600",
      bgColor: "bg-green-50"
    },
  ];

  return (
    <Layout user={user}>
      <div className="space-y-6" data-testid="teacher-dashboard">
        {/* Welcome Header */}
        <div className="flex flex-col md:flex-row md:items-center md:justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-foreground">
              Welcome back, {user?.name?.split(" ")[0]}!
            </h1>
            <p className="text-muted-foreground mt-1">
              {new Date().toLocaleDateString('en-US', { 
                weekday: 'long', 
                year: 'numeric', 
                month: 'long', 
                day: 'numeric' 
              })}
            </p>
          </div>
          <Button 
            onClick={() => navigate("/teacher/upload")}
            className="rounded-full shadow-md hover:shadow-lg transition-all"
            data-testid="upload-papers-btn"
          >
            Upload & Grade Papers
            <ArrowRight className="w-4 h-4 ml-2" />
          </Button>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {statCards.map((stat, index) => {
            const Icon = stat.icon;
            return (
              <Card 
                key={stat.label} 
                className="card-hover animate-fade-in"
                style={{ animationDelay: `${index * 0.1}s` }}
                data-testid={`stat-${stat.label.toLowerCase().replace(/\s+/g, '-')}`}
              >
                <CardContent className="p-6">
                  <div className="flex items-center justify-between">
                    <div>
                      <p className="text-sm text-muted-foreground">{stat.label}</p>
                      <p className="text-3xl font-bold mt-1">{stat.value}</p>
                    </div>
                    <div className={`p-3 rounded-xl ${stat.bgColor}`}>
                      <Icon className={`w-6 h-6 ${stat.color}`} />
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>

        {/* Main Content Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Recent Activity */}
          <Card className="lg:col-span-2 animate-fade-in stagger-2" data-testid="recent-activity">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Recent Submissions</CardTitle>
              <Button 
                variant="ghost" 
                size="sm"
                onClick={() => navigate("/teacher/review")}
              >
                View All
                <ArrowRight className="w-4 h-4 ml-1" />
              </Button>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="space-y-3">
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="h-16 bg-muted animate-pulse rounded-lg" />
                  ))}
                </div>
              ) : recentSubmissions.length === 0 ? (
                <div className="text-center py-8">
                  <FileText className="w-12 h-12 mx-auto text-muted-foreground/50 mb-3" />
                  <p className="text-muted-foreground">No submissions yet</p>
                  <Button 
                    variant="outline" 
                    size="sm" 
                    className="mt-3"
                    onClick={() => navigate("/teacher/upload")}
                  >
                    Upload your first paper
                  </Button>
                </div>
              ) : (
                <div className="space-y-3">
                  {recentSubmissions.map((submission, index) => (
                    <div 
                      key={submission.submission_id}
                      className="flex items-center justify-between p-4 rounded-lg bg-muted/50 hover:bg-muted transition-colors"
                    >
                      <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-full bg-primary/10 flex items-center justify-center">
                          <span className="text-sm font-medium text-primary">
                            {submission.student_name?.charAt(0) || "?"}
                          </span>
                        </div>
                        <div>
                          <p className="font-medium">{submission.student_name}</p>
                          <p className="text-sm text-muted-foreground">
                            Score: {submission.total_score}
                          </p>
                        </div>
                      </div>
                      <Badge 
                        variant={submission.status === "teacher_reviewed" ? "default" : "secondary"}
                        className={submission.status === "ai_graded" ? "bg-yellow-100 text-yellow-700" : ""}
                      >
                        {submission.status === "ai_graded" ? "Needs Review" : "Reviewed"}
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Quick Actions & Alerts */}
          <div className="space-y-6">
            {/* Pending Re-evaluations */}
            {stats.pending_reeval > 0 && (
              <Card className="border-yellow-200 bg-yellow-50/50 animate-fade-in stagger-3">
                <CardContent className="p-4">
                  <div className="flex items-start gap-3">
                    <AlertCircle className="w-5 h-5 text-yellow-600 mt-0.5" />
                    <div>
                      <p className="font-medium text-yellow-900">
                        {stats.pending_reeval} Re-evaluation Request{stats.pending_reeval > 1 ? "s" : ""}
                      </p>
                      <p className="text-sm text-yellow-700 mt-1">
                        Students have requested grade reviews
                      </p>
                      <Button 
                        variant="outline" 
                        size="sm" 
                        className="mt-2 border-yellow-300 text-yellow-700 hover:bg-yellow-100"
                        onClick={() => navigate("/teacher/re-evaluations")}
                      >
                        Review Requests
                      </Button>
                    </div>
                  </div>
                </CardContent>
              </Card>
            )}

            {/* Quick Actions */}
            <Card className="animate-fade-in stagger-4" data-testid="quick-actions">
              <CardHeader>
                <CardTitle className="text-lg">Quick Actions</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2">
                <Button 
                  variant="outline" 
                  className="w-full justify-start"
                  onClick={() => navigate("/teacher/upload")}
                >
                  <FileText className="w-4 h-4 mr-2" />
                  Upload New Papers
                </Button>
                <Button 
                  variant="outline" 
                  className="w-full justify-start"
                  onClick={() => navigate("/teacher/students")}
                >
                  <Users className="w-4 h-4 mr-2" />
                  Manage Students
                </Button>
                <Button 
                  variant="outline" 
                  className="w-full justify-start"
                  onClick={() => navigate("/teacher/reports")}
                >
                  <TrendingUp className="w-4 h-4 mr-2" />
                  View Reports
                </Button>
              </CardContent>
            </Card>

            {/* Summary */}
            <Card className="animate-fade-in stagger-5">
              <CardContent className="p-4">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg bg-green-50">
                    <CheckCircle className="w-5 h-5 text-green-600" />
                  </div>
                  <div>
                    <p className="font-medium">{stats.total_students || 0} Students</p>
                    <p className="text-sm text-muted-foreground">
                      Across {stats.total_batches || 0} batches
                    </p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      </div>
    </Layout>
  );
}
