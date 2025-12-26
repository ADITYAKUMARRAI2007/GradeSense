import { useState, useEffect } from "react";
import axios from "axios";
import { API } from "../../App";
import Layout from "../../components/Layout";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "../../components/ui/card";
import { Badge } from "../../components/ui/badge";
import { Button } from "../../components/ui/button";
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
  TrendingUp, 
  TrendingDown,
  Award,
  BookOpen,
  Target,
  ArrowRight
} from "lucide-react";
import { useNavigate } from "react-router-dom";

export default function StudentDashboard({ user }) {
  const [analytics, setAnalytics] = useState(null);
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  useEffect(() => {
    fetchDashboard();
  }, []);

  const fetchDashboard = async () => {
    try {
      const response = await axios.get(`${API}/analytics/student-dashboard`);
      setAnalytics(response.data);
    } catch (error) {
      console.error("Error fetching dashboard:", error);
    } finally {
      setLoading(false);
    }
  };

  const stats = analytics?.stats || {};
  const recentResults = analytics?.recent_results || [];
  const recommendations = analytics?.recommendations || [];

  return (
    <Layout user={user}>
      <div className="space-y-6" data-testid="student-dashboard">
        {/* Welcome */}
        <div>
          <h1 className="text-3xl font-bold text-foreground">
            Welcome, {user?.name?.split(" ")[0]}!
          </h1>
          <p className="text-muted-foreground mt-1">
            Track your performance and see personalized study recommendations
          </p>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          <Card className="animate-fade-in">
            <CardContent className="p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">Exams Taken</p>
                  <p className="text-3xl font-bold mt-1">{stats.total_exams || 0}</p>
                </div>
                <div className="p-3 rounded-xl bg-blue-50">
                  <BookOpen className="w-6 h-6 text-blue-600" />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="animate-fade-in stagger-1">
            <CardContent className="p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">Average Score</p>
                  <p className="text-3xl font-bold mt-1">{stats.avg_percentage || 0}%</p>
                </div>
                <div className="p-3 rounded-xl bg-orange-50">
                  <Target className="w-6 h-6 text-orange-600" />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="animate-fade-in stagger-2">
            <CardContent className="p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">Class Rank</p>
                  <p className="text-3xl font-bold mt-1">{stats.rank || "N/A"}</p>
                </div>
                <div className="p-3 rounded-xl bg-green-50">
                  <Award className="w-6 h-6 text-green-600" />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="animate-fade-in stagger-3">
            <CardContent className="p-6">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-muted-foreground">Improvement</p>
                  <p className="text-3xl font-bold mt-1">
                    {stats.improvement > 0 ? "+" : ""}{stats.improvement || 0}%
                  </p>
                </div>
                <div className={`p-3 rounded-xl ${stats.improvement >= 0 ? "bg-green-50" : "bg-red-50"}`}>
                  {stats.improvement >= 0 ? (
                    <TrendingUp className="w-6 h-6 text-green-600" />
                  ) : (
                    <TrendingDown className="w-6 h-6 text-red-600" />
                  )}
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Main Content */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* Recent Results */}
          <Card className="lg:col-span-2 animate-fade-in stagger-2">
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>Recent Results</CardTitle>
              <Button 
                variant="ghost" 
                size="sm"
                onClick={() => navigate("/student/results")}
              >
                View All
                <ArrowRight className="w-4 h-4 ml-1" />
              </Button>
            </CardHeader>
            <CardContent>
              {loading ? (
                <div className="space-y-3">
                  {[1, 2, 3].map(i => (
                    <div key={i} className="h-16 bg-muted animate-pulse rounded-lg" />
                  ))}
                </div>
              ) : recentResults.length === 0 ? (
                <div className="text-center py-8">
                  <BookOpen className="w-12 h-12 mx-auto text-muted-foreground/50 mb-3" />
                  <p className="text-muted-foreground">No exam results yet</p>
                </div>
              ) : (
                <div className="space-y-3">
                  {recentResults.map((result, index) => (
                    <div 
                      key={index}
                      className="flex items-center justify-between p-4 bg-muted/50 rounded-lg hover:bg-muted transition-colors"
                    >
                      <div>
                        <p className="font-medium">{result.exam_name}</p>
                        <p className="text-sm text-muted-foreground">{result.subject}</p>
                      </div>
                      <Badge 
                        className={
                          result.percentage >= 80 ? "bg-green-100 text-green-700" :
                          result.percentage >= 60 ? "bg-blue-100 text-blue-700" :
                          result.percentage >= 40 ? "bg-yellow-100 text-yellow-700" :
                          "bg-red-100 text-red-700"
                        }
                      >
                        {result.percentage}%
                      </Badge>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Study Recommendations */}
          <Card className="animate-fade-in stagger-3">
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Target className="w-5 h-5 text-primary" />
                Study Tips
              </CardTitle>
              <CardDescription>Personalized recommendations</CardDescription>
            </CardHeader>
            <CardContent>
              {recommendations.length === 0 ? (
                <p className="text-muted-foreground text-center py-4">
                  Complete some exams to get recommendations
                </p>
              ) : (
                <div className="space-y-3">
                  {recommendations.map((rec, index) => (
                    <div 
                      key={index}
                      className="p-3 bg-primary/5 border border-primary/20 rounded-lg"
                    >
                      <p className="text-sm">{rec}</p>
                    </div>
                  ))}
                </div>
              )}
            </CardContent>
          </Card>
        </div>

        {/* Performance Chart */}
        {recentResults.length > 1 && (
          <Card className="animate-fade-in stagger-4">
            <CardHeader>
              <CardTitle>Performance Trend</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="h-64">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={recentResults.slice().reverse()}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#E2E8F0" />
                    <XAxis 
                      dataKey="exam_name" 
                      tick={{ fontSize: 12 }}
                      tickFormatter={(value) => value.substring(0, 10) + "..."}
                    />
                    <YAxis domain={[0, 100]} tick={{ fontSize: 12 }} />
                    <Tooltip 
                      contentStyle={{ 
                        backgroundColor: 'white', 
                        border: '1px solid #E2E8F0',
                        borderRadius: '8px'
                      }}
                    />
                    <Line 
                      type="monotone" 
                      dataKey="percentage" 
                      stroke="#F97316" 
                      strokeWidth={2}
                      dot={{ fill: '#F97316', strokeWidth: 2 }}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </Layout>
  );
}
