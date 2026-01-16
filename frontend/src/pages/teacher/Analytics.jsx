import { useState, useEffect } from 'react';
import axios from 'axios';
import { API } from '../../App';
import Layout from '../../components/Layout';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../components/ui/card';
import { Button } from '../../components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../../components/ui/select';
import { Badge } from '../../components/ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '../../components/ui/dialog';
import { toast } from 'sonner';
import { 
  TrendingUp, 
  TrendingDown, 
  AlertTriangle, 
  ChevronRight,
  ArrowLeft,
  Users,
  Target,
  Layers,
  FileText,
  Zap,
  Brain,
  Award,
  Eye,
  RefreshCw
} from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar } from 'recharts';

export default function Analytics({ user }) {
  const [exams, setExams] = useState([]);
  const [batches, setBatches] = useState([]);
  const [selectedExam, setSelectedExam] = useState('');
  const [selectedBatch, setSelectedBatch] = useState('');
  
  // Drill-down state
  const [drillLevel, setDrillLevel] = useState('overview'); // overview, topic, question
  const [selectedTopic, setSelectedTopic] = useState(null);
  const [selectedQuestion, setSelectedQuestion] = useState(null);
  
  // Data states
  const [topicMastery, setTopicMastery] = useState(null);
  const [topicDrillDown, setTopicDrillDown] = useState(null);
  const [questionDrillDown, setQuestionDrillDown] = useState(null);
  const [loading, setLoading] = useState(false);
  
  // Modal states
  const [studentJourneyModal, setStudentJourneyModal] = useState(false);
  const [selectedStudentJourney, setSelectedStudentJourney] = useState(null);
  
  // Phase 2: Advanced Metrics
  const [showAdvancedMetrics, setShowAdvancedMetrics] = useState(false);
  const [bluffIndex, setBluffIndex] = useState(null);
  const [syllabusCoverage, setSyllabusCoverage] = useState(null);
  const [peerGroups, setPeerGroups] = useState(null);
  const [loadingAdvanced, setLoadingAdvanced] = useState(false);
  
  // Ask Your Data (NL Query)
  const [nlQuery, setNlQuery] = useState('');
  const [nlResult, setNlResult] = useState(null);
  const [loadingNlQuery, setLoadingNlQuery] = useState(false);

  useEffect(() => {
    fetchFilters();
  }, []);

  useEffect(() => {
    if (selectedExam || selectedBatch) {
      fetchTopicMastery();
    }
  }, [selectedExam, selectedBatch]);

  const fetchFilters = async () => {
    try {
      const [examsRes, batchesRes] = await Promise.all([
        axios.get(`${API}/exams`),
        axios.get(`${API}/batches`)
      ]);
      setExams(examsRes.data);
      setBatches(batchesRes.data);
    } catch (error) {
      console.error('Error fetching filters:', error);
    }
  };

  const fetchTopicMastery = async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (selectedExam) params.append('exam_id', selectedExam);
      if (selectedBatch) params.append('batch_id', selectedBatch);
      
      const response = await axios.get(`${API}/analytics/topic-mastery?${params}`);
      setTopicMastery(response.data);
    } catch (error) {
      console.error('Error fetching topic mastery:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleTopicClick = async (topic) => {
    setLoading(true);
    try {
      const params = new URLSearchParams();
      if (selectedExam) params.append('exam_id', selectedExam);
      if (selectedBatch) params.append('batch_id', selectedBatch);
      
      const response = await axios.get(
        `${API}/analytics/drill-down/topic/${encodeURIComponent(topic.topic)}?${params}`
      );
      setTopicDrillDown(response.data);
      setSelectedTopic(topic);
      setDrillLevel('topic');
    } catch (error) {
      console.error('Error fetching topic drill-down:', error);
      toast.error('Failed to load topic details');
    } finally {
      setLoading(false);
    }
  };

  const handleQuestionClick = async (question) => {
    if (!selectedExam) {
      toast.error('Please select a specific exam to view question details');
      return;
    }
    
    setLoading(true);
    try {
      const response = await axios.get(
        `${API}/analytics/drill-down/question?exam_id=${selectedExam}&question_number=${question.question_number}`
      );
      setQuestionDrillDown(response.data);
      setSelectedQuestion(question);
      setDrillLevel('question');
    } catch (error) {
      console.error('Error fetching question drill-down:', error);
      toast.error('Failed to load question details');
    } finally {
      setLoading(false);
    }
  };

  const handleStudentClick = async (studentId) => {
    setLoading(true);
    try {
      const response = await axios.get(`${API}/analytics/student-journey/${studentId}`);
      setSelectedStudentJourney(response.data);
      setStudentJourneyModal(true);
    } catch (error) {
      console.error('Error fetching student journey:', error);
      toast.error('Failed to load student details');
    } finally {
      setLoading(false);
    }
  };

  const goBack = () => {
    if (drillLevel === 'question') {
      setDrillLevel('topic');
      setQuestionDrillDown(null);
    } else if (drillLevel === 'topic') {
      setDrillLevel('overview');
      setTopicDrillDown(null);
    }
  };
  
  // Phase 2: Advanced Metrics Functions
  const fetchBluffIndex = async () => {
    if (!selectedExam) {
      toast.error('Please select an exam to analyze bluff patterns');
      return;
    }
    
    setLoadingAdvanced(true);
    try {
      const response = await axios.get(`${API}/analytics/bluff-index?exam_id=${selectedExam}`);
      setBluffIndex(response.data);
    } catch (error) {
      console.error('Error fetching bluff index:', error);
      toast.error('Failed to load bluff analysis');
    } finally {
      setLoadingAdvanced(false);
    }
  };
  
  const fetchSyllabusCoverage = async () => {
    setLoadingAdvanced(true);
    try {
      const params = new URLSearchParams();
      if (selectedBatch) params.append('batch_id', selectedBatch);
      
      const response = await axios.get(`${API}/analytics/syllabus-coverage?${params}`);
      setSyllabusCoverage(response.data);
    } catch (error) {
      console.error('Error fetching syllabus coverage:', error);
      toast.error('Failed to load syllabus coverage');
    } finally {
      setLoadingAdvanced(false);
    }
  };
  
  const fetchPeerGroups = async () => {
    if (!selectedBatch) {
      toast.error('Please select a batch to find peer groups');
      return;
    }
    
    setLoadingAdvanced(true);
    try {
      const response = await axios.get(`${API}/analytics/peer-groups?batch_id=${selectedBatch}`);
      setPeerGroups(response.data);
    } catch (error) {
      console.error('Error fetching peer groups:', error);
      toast.error('Failed to load peer group suggestions');
    } finally {
      setLoadingAdvanced(false);
    }
  };
  
  const sendPeerGroupNotification = async (student1Id, student2Id, message) => {
    try {
      await axios.post(`${API}/analytics/send-peer-group-email`, {
        student1_id: student1Id,
        student2_id: student2Id,
        message: message
      });
      toast.success('Notifications sent to both students!');
    } catch (error) {
      console.error('Error sending notifications:', error);
      toast.error('Failed to send notifications');
    }
  };
  
  // Natural Language Query
  const handleAskData = async () => {
    if (!nlQuery.trim()) {
      toast.error('Please enter a question');
      return;
    }
    
    setLoadingNlQuery(true);
    try {
      const response = await axios.post(`${API}/analytics/ask`, {
        query: nlQuery,
        batch_id: selectedBatch,
        exam_id: selectedExam
      });
      setNlResult(response.data);
    } catch (error) {
      console.error('Error processing natural language query:', error);
      toast.error('Failed to process your question');
      setNlResult({
        type: 'error',
        message: error.response?.data?.detail || 'Failed to process query'
      });
    } finally {
      setLoadingNlQuery(false);
    }
  };

  const getScoreColor = (percentage) => {
    if (percentage >= 70) return 'text-green-600';
    if (percentage >= 50) return 'text-amber-600';
    return 'text-red-600';
  };

  const getScoreBg = (percentage) => {
    if (percentage >= 70) return 'bg-green-50 border-green-200';
    if (percentage >= 50) return 'bg-amber-50 border-amber-200';
    return 'bg-red-50 border-red-200';
  };

  // Prepare radar chart data for overview
  const radarData = topicMastery?.topics?.slice(0, 6).map(t => ({
    topic: t.topic.substring(0, 15),
    score: t.avg_percentage,
    fullMark: 100
  })) || [];

  return (
    <Layout user={user}>
      <div className="space-y-6" data-testid="analytics-page">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-4">
            {drillLevel !== 'overview' && (
              <Button variant="outline" onClick={goBack}>
                <ArrowLeft className="w-4 h-4 mr-2" />
                Back
              </Button>
            )}
            <div>
              <h1 className="text-2xl font-bold">
                {drillLevel === 'overview' && 'Data Studio - Deep Analytics'}
                {drillLevel === 'topic' && `Topic DNA: ${selectedTopic?.topic}`}
                {drillLevel === 'question' && `Question Analysis: Q${selectedQuestion?.question_number}`}
              </h1>
              <p className="text-muted-foreground">
                {drillLevel === 'overview' && 'Click any metric to drill down into details'}
                {drillLevel === 'topic' && 'Sub-skill breakdown and student performance'}
                {drillLevel === 'question' && 'Error patterns and student grouping'}
              </p>
            </div>
          </div>

          {/* Filters */}
          <div className="flex gap-3">
            <Select value={selectedBatch || 'all'} onValueChange={(v) => setSelectedBatch(v === 'all' ? '' : v)}>
              <SelectTrigger className="w-40">
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

            <Select value={selectedExam || 'all'} onValueChange={(v) => setSelectedExam(v === 'all' ? '' : v)}>
              <SelectTrigger className="w-48">
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
          </div>
        </div>

        {/* LEVEL 1: OVERVIEW */}
        {drillLevel === 'overview' && (
          <>
            {/* Ask Your Data - Natural Language Query */}
            <Card className="border-2 border-primary">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Brain className="w-5 h-5 text-primary" />
                  Ask Your Data (AI-Powered)
                </CardTitle>
                <CardDescription>
                  Ask questions in plain English - e.g., "Show me top 5 students in Math" or "Who failed Question 3?"
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {/* Search Bar */}
                  <div className="flex gap-2">
                    <input
                      type="text"
                      placeholder='Try: "Show me top 5 students" or "Compare Section A vs Section B"'
                      className="flex-1 border p-3 rounded-lg bg-gray-50 focus:outline-none focus:ring-2 focus:ring-primary"
                      value={nlQuery}
                      onChange={(e) => setNlQuery(e.target.value)}
                      onKeyPress={(e) => e.key === 'Enter' && handleAskData()}
                      disabled={loadingNlQuery}
                    />
                    <Button
                      onClick={handleAskData}
                      disabled={loadingNlQuery || !nlQuery.trim()}
                      className="px-8"
                    >
                      {loadingNlQuery ? (
                        <>
                          <RefreshCw className="w-4 h-4 mr-2 animate-spin" />
                          Analyzing...
                        </>
                      ) : (
                        <>
                          <Zap className="w-4 h-4 mr-2" />
                          Ask AI
                        </>
                      )}
                    </Button>
                  </div>

                  {/* Suggested Questions */}
                  <div className="flex flex-wrap gap-2">
                    <span className="text-xs text-muted-foreground">Quick questions:</span>
                    {[
                      "Show me top 5 students",
                      "Who failed in Math?",
                      "Show students below 40%",
                      "Top performers in last exam"
                    ].map((suggestion, idx) => (
                      <button
                        key={idx}
                        onClick={() => {
                          setNlQuery(suggestion);
                          setNlResult(null);
                        }}
                        className="text-xs px-3 py-1 bg-primary/10 text-primary rounded-full hover:bg-primary/20 transition-colors"
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>

                  {/* Results */}
                  {nlResult && (
                    <div className="mt-4 border rounded-lg p-4 bg-white">
                      {nlResult.type === 'error' ? (
                        <div className="text-center py-8">
                          <AlertTriangle className="w-12 h-12 mx-auto mb-3 text-red-500" />
                          <p className="font-semibold text-red-700">Could not process query</p>
                          <p className="text-sm text-muted-foreground mt-2">{nlResult.message}</p>
                        </div>
                      ) : (
                        <>
                          <div className="mb-4">
                            <h4 className="font-semibold text-lg">{nlResult.title}</h4>
                            {nlResult.description && (
                              <p className="text-sm text-muted-foreground mt-1">{nlResult.description}</p>
                            )}
                          </div>

                          {/* Bar Chart */}
                          {nlResult.type === 'bar' && nlResult.data?.length > 0 && (
                            <div className="h-80">
                              <ResponsiveContainer width="100%" height="100%">
                                <BarChart data={nlResult.data}>
                                  <CartesianGrid strokeDasharray="3 3" />
                                  <XAxis
                                    dataKey={nlResult.xAxis || 'name'}
                                    tick={{ fontSize: 12 }}
                                    angle={-45}
                                    textAnchor="end"
                                    height={100}
                                  />
                                  <YAxis />
                                  <Tooltip />
                                  <Bar
                                    dataKey={nlResult.yAxis || 'value'}
                                    fill="#F97316"
                                    radius={[4, 4, 0, 0]}
                                  />
                                </BarChart>
                              </ResponsiveContainer>
                            </div>
                          )}

                          {/* Table */}
                          {nlResult.type === 'table' && nlResult.data?.length > 0 && (
                            <div className="overflow-x-auto">
                              <table className="w-full border-collapse">
                                <thead>
                                  <tr className="bg-gray-50">
                                    {Object.keys(nlResult.data[0]).map((key, idx) => (
                                      <th key={idx} className="border p-2 text-left text-sm font-semibold">
                                        {key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase())}
                                      </th>
                                    ))}
                                  </tr>
                                </thead>
                                <tbody>
                                  {nlResult.data.map((row, idx) => (
                                    <tr key={idx} className="hover:bg-gray-50">
                                      {Object.values(row).map((val, vidx) => (
                                        <td key={vidx} className="border p-2 text-sm">
                                          {typeof val === 'number' ? val.toFixed(1) : val}
                                        </td>
                                      ))}
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          )}

                          {/* No Data */}
                          {nlResult.data?.length === 0 && (
                            <div className="text-center py-8 text-muted-foreground">
                              <FileText className="w-12 h-12 mx-auto mb-3 opacity-30" />
                              <p>No results found for your query.</p>
                              <p className="text-sm">Try rephrasing or selecting different filters.</p>
                            </div>
                          )}
                        </>
                      )}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            {/* Topic Mastery Radar */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Layers className="w-5 h-5 text-primary" />
                  Topic Mastery Overview
                </CardTitle>
                <CardDescription>Click any topic to see detailed breakdown</CardDescription>
              </CardHeader>
              <CardContent>
                {loading ? (
                  <div className="h-80 flex items-center justify-center">
                    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-primary"></div>
                  </div>
                ) : !topicMastery?.topics?.length ? (
                  <div className="text-center py-12 text-muted-foreground">
                    <Layers className="w-12 h-12 mx-auto mb-3 opacity-30" />
                    <p>No data available. Select an exam or batch with graded submissions.</p>
                  </div>
                ) : (
                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    {/* Radar Chart */}
                    <div className="h-80">
                      <ResponsiveContainer width="100%" height="100%">
                        <RadarChart data={radarData}>
                          <PolarGrid />
                          <PolarAngleAxis dataKey="topic" />
                          <PolarRadiusAxis angle={90} domain={[0, 100]} />
                          <Radar
                            name="Score"
                            dataKey="score"
                            stroke="#F97316"
                            fill="#F97316"
                            fillOpacity={0.6}
                          />
                          <Tooltip />
                        </RadarChart>
                      </ResponsiveContainer>
                    </div>

                    {/* Topic Cards */}
                    <div className="grid grid-cols-2 gap-3">
                      {topicMastery.topics.slice(0, 6).map((topic, idx) => (
                        <div
                          key={idx}
                          className={`p-4 rounded-lg border-2 cursor-pointer transition-all hover:scale-105 hover:shadow-lg ${
                            topic.color === 'green' ? 'bg-green-50 border-green-300 hover:border-green-500' :
                            topic.color === 'amber' ? 'bg-amber-50 border-amber-300 hover:border-amber-500' :
                            'bg-red-50 border-red-300 hover:border-red-500'
                          }`}
                          onClick={() => handleTopicClick(topic)}
                        >
                          <p className="font-medium text-sm truncate mb-1" title={topic.topic}>
                            {topic.topic}
                          </p>
                          <p className={`text-2xl font-bold ${
                            topic.color === 'green' ? 'text-green-700' :
                            topic.color === 'amber' ? 'text-amber-700' :
                            'text-red-700'
                          }`}>
                            {topic.avg_percentage}%
                          </p>
                          <div className="flex items-center justify-between mt-2">
                            <Badge variant="outline" className="text-xs">
                              {topic.question_count} Q
                            </Badge>
                            <ChevronRight className="w-4 h-4 text-muted-foreground" />
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            {/* All Topics Grid */}
            {topicMastery?.topics?.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle>All Topics Performance</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
                    {topicMastery.topics.map((topic, idx) => (
                      <div
                        key={idx}
                        className={`p-3 rounded-lg border-2 cursor-pointer transition-all hover:scale-105 ${
                          topic.color === 'green' ? 'bg-green-50 border-green-200' :
                          topic.color === 'amber' ? 'bg-amber-50 border-amber-200' :
                          'bg-red-50 border-red-200'
                        }`}
                        onClick={() => handleTopicClick(topic)}
                      >
                        <p className="font-medium text-xs truncate" title={topic.topic}>
                          {topic.topic}
                        </p>
                        <p className={`text-xl font-bold ${
                          topic.color === 'green' ? 'text-green-700' :
                          topic.color === 'amber' ? 'text-amber-700' :
                          'text-red-700'
                        }`}>
                          {topic.avg_percentage}%
                        </p>
                        {topic.struggling_count > 0 && (
                          <Badge variant="outline" className="text-xs text-red-600 border-red-300 mt-1">
                            {topic.struggling_count} need help
                          </Badge>
                        )}
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </>
        )}
        
        {/* PHASE 2: ADVANCED METRICS (Toggle) */}
        {drillLevel === 'overview' && (
          <>
            <Card className="border-purple-200 bg-gradient-to-r from-purple-50 to-white">
              <CardContent className="p-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="font-semibold flex items-center gap-2">
                      <Brain className="w-5 h-5 text-purple-600" />
                      Advanced AI Metrics (Beta)
                    </h3>
                    <p className="text-sm text-muted-foreground">
                      Bluff detection, syllabus gaps, and peer group suggestions
                    </p>
                  </div>
                  <Button
                    variant={showAdvancedMetrics ? "default" : "outline"}
                    onClick={() => setShowAdvancedMetrics(!showAdvancedMetrics)}
                  >
                    {showAdvancedMetrics ? 'Hide' : 'Show'} Advanced Metrics
                  </Button>
                </div>
              </CardContent>
            </Card>

            {showAdvancedMetrics && (
              <div className="space-y-6">
                {/* Action Buttons */}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <Button
                    variant="outline"
                    className="h-auto p-4 flex flex-col items-start gap-2 hover:border-amber-500 hover:bg-amber-50"
                    onClick={fetchBluffIndex}
                    disabled={!selectedExam || loadingAdvanced}
                  >
                    <div className="flex items-center gap-2 w-full">
                      <AlertTriangle className="w-5 h-5 text-amber-600" />
                      <span className="font-semibold">Bluff Index</span>
                    </div>
                    <span className="text-xs text-muted-foreground text-left">
                      Detect students writing long but irrelevant answers
                    </span>
                  </Button>

                  <Button
                    variant="outline"
                    className="h-auto p-4 flex flex-col items-start gap-2 hover:border-blue-500 hover:bg-blue-50"
                    onClick={fetchSyllabusCoverage}
                    disabled={loadingAdvanced}
                  >
                    <div className="flex items-center gap-2 w-full">
                      <Layers className="w-5 h-5 text-blue-600" />
                      <span className="font-semibold">Syllabus Coverage</span>
                    </div>
                    <span className="text-xs text-muted-foreground text-left">
                      See which topics you've tested and assessment gaps
                    </span>
                  </Button>

                  <Button
                    variant="outline"
                    className="h-auto p-4 flex flex-col items-start gap-2 hover:border-green-500 hover:bg-green-50"
                    onClick={fetchPeerGroups}
                    disabled={!selectedBatch || loadingAdvanced}
                  >
                    <div className="flex items-center gap-2 w-full">
                      <Users className="w-5 h-5 text-green-600" />
                      <span className="font-semibold">Peer Groups</span>
                    </div>
                    <span className="text-xs text-muted-foreground text-left">
                      Find study partners with complementary skills
                    </span>
                  </Button>
                </div>

                {/* Bluff Index Results */}
                {bluffIndex && (
                  <Card>
                    <CardHeader>
                      <CardTitle className="flex items-center gap-2 text-amber-700">
                        <AlertTriangle className="w-5 h-5" />
                        Bluff Index - {bluffIndex.exam_name}
                      </CardTitle>
                      <CardDescription>{bluffIndex.summary}</CardDescription>
                    </CardHeader>
                    <CardContent>
                      {bluffIndex.bluff_candidates.length === 0 ? (
                        <div className="text-center py-8 text-muted-foreground">
                          <Award className="w-12 h-12 mx-auto mb-3 opacity-30 text-green-600" />
                          <p className="font-medium text-green-700">Great! No bluffing patterns detected.</p>
                          <p className="text-sm">All students appear to be writing meaningful answers.</p>
                        </div>
                      ) : (
                        <div className="space-y-4">
                          {bluffIndex.bluff_candidates.map((candidate, idx) => (
                            <div key={idx} className="p-4 bg-amber-50 border border-amber-200 rounded-lg">
                              <div className="flex items-center justify-between mb-3">
                                <div className="flex items-center gap-3">
                                  <AlertTriangle className="w-5 h-5 text-amber-600" />
                                  <span className="font-semibold">{candidate.student_name}</span>
                                </div>
                                <Badge variant="outline" className="bg-amber-100 text-amber-700">
                                  {candidate.bluff_score} suspicious answers
                                </Badge>
                              </div>
                              
                              <div className="space-y-2">
                                {candidate.suspicious_answers.map((answer, aidx) => (
                                  <div key={aidx} className="p-3 bg-white border border-amber-100 rounded text-sm">
                                    <div className="flex items-center justify-between mb-1">
                                      <span className="font-medium">Question {answer.question_number}</span>
                                      <div className="flex items-center gap-2">
                                        <Badge variant="outline" className="text-xs">
                                          {answer.answer_length} chars
                                        </Badge>
                                        <Badge variant="destructive" className="text-xs">
                                          {answer.score_percentage}%
                                        </Badge>
                                      </div>
                                    </div>
                                    <p className="text-xs text-muted-foreground">
                                      AI Feedback: {answer.feedback_snippet}
                                    </p>
                                  </div>
                                ))}
                              </div>

                              <Button
                                variant="outline"
                                size="sm"
                                className="w-full mt-3"
                                onClick={() => handleStudentClick(candidate.student_id)}
                              >
                                <Eye className="w-4 h-4 mr-2" />
                                View Full Student Profile
                              </Button>
                            </div>
                          ))}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )}

                {/* Syllabus Coverage Results */}
                {syllabusCoverage && (
                  <Card>
                    <CardHeader>
                      <CardTitle className="flex items-center gap-2">
                        <Layers className="w-5 h-5 text-blue-600" />
                        Syllabus Coverage - {syllabusCoverage.subject}
                      </CardTitle>
                      <CardDescription>{syllabusCoverage.summary}</CardDescription>
                    </CardHeader>
                    <CardContent>
                      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
                        {syllabusCoverage.tested_topics.map((topic, idx) => (
                          <div
                            key={idx}
                            className={`p-3 rounded-lg border-2 ${
                              topic.color === 'green' ? 'bg-green-50 border-green-200' :
                              topic.color === 'amber' ? 'bg-amber-50 border-amber-200' :
                              topic.color === 'red' ? 'bg-red-50 border-red-200' :
                              'bg-gray-50 border-gray-200'
                            }`}
                          >
                            <p className="font-medium text-xs truncate mb-1" title={topic.topic}>
                              {topic.topic}
                            </p>
                            <p className={`text-xl font-bold ${
                              topic.color === 'green' ? 'text-green-700' :
                              topic.color === 'amber' ? 'text-amber-700' :
                              topic.color === 'red' ? 'text-red-700' :
                              'text-gray-700'
                            }`}>
                              {topic.avg_score}%
                            </p>
                            <p className="text-xs text-muted-foreground mt-1">
                              {topic.exam_count} exam{topic.exam_count !== 1 ? 's' : ''}
                            </p>
                          </div>
                        ))}
                      </div>
                    </CardContent>
                  </Card>
                )}

                {/* Peer Groups Results */}
                {peerGroups && (
                  <Card>
                    <CardHeader>
                      <CardTitle className="flex items-center gap-2 text-green-700">
                        <Users className="w-5 h-5" />
                        Peer Group Suggestions - {peerGroups.batch_name}
                      </CardTitle>
                      <CardDescription>{peerGroups.summary}</CardDescription>
                    </CardHeader>
                    <CardContent>
                      {peerGroups.suggestions.length === 0 ? (
                        <div className="text-center py-8 text-muted-foreground">
                          <Users className="w-12 h-12 mx-auto mb-3 opacity-30" />
                          <p>No complementary peer groups found.</p>
                          <p className="text-sm">Students may have similar skill profiles.</p>
                        </div>
                      ) : (
                        <div className="space-y-4">
                          {peerGroups.suggestions.map((pair, idx) => (
                            <div key={idx} className="p-4 bg-green-50 border border-green-200 rounded-lg">
                              <div className="flex items-center justify-between mb-3">
                                <h4 className="font-semibold flex items-center gap-2">
                                  <Users className="w-4 h-4 text-green-600" />
                                  Suggested Pair #{idx + 1}
                                </h4>
                                <Badge className="bg-green-100 text-green-700">
                                  {pair.synergy_score} complementary topics
                                </Badge>
                              </div>

                              <div className="grid grid-cols-2 gap-4 mb-3">
                                <div className="p-3 bg-white rounded border">
                                  <p className="font-medium mb-2">{pair.student1.name}</p>
                                  <div className="space-y-1">
                                    <div>
                                      <p className="text-xs font-semibold text-green-600">Strengths:</p>
                                      <div className="flex flex-wrap gap-1 mt-1">
                                        {pair.student1.strengths.slice(0, 3).map((s, i) => (
                                          <Badge key={i} variant="outline" className="text-xs bg-green-50">
                                            {s}
                                          </Badge>
                                        ))}
                                      </div>
                                    </div>
                                    <div>
                                      <p className="text-xs font-semibold text-red-600">Needs help:</p>
                                      <div className="flex flex-wrap gap-1 mt-1">
                                        {pair.student1.weaknesses.slice(0, 3).map((w, i) => (
                                          <Badge key={i} variant="outline" className="text-xs bg-red-50">
                                            {w}
                                          </Badge>
                                        ))}
                                      </div>
                                    </div>
                                  </div>
                                </div>

                                <div className="p-3 bg-white rounded border">
                                  <p className="font-medium mb-2">{pair.student2.name}</p>
                                  <div className="space-y-1">
                                    <div>
                                      <p className="text-xs font-semibold text-green-600">Strengths:</p>
                                      <div className="flex flex-wrap gap-1 mt-1">
                                        {pair.student2.strengths.slice(0, 3).map((s, i) => (
                                          <Badge key={i} variant="outline" className="text-xs bg-green-50">
                                            {s}
                                          </Badge>
                                        ))}
                                      </div>
                                    </div>
                                    <div>
                                      <p className="text-xs font-semibold text-red-600">Needs help:</p>
                                      <div className="flex flex-wrap gap-1 mt-1">
                                        {pair.student2.weaknesses.slice(0, 3).map((w, i) => (
                                          <Badge key={i} variant="outline" className="text-xs bg-red-50">
                                            {w}
                                          </Badge>
                                        ))}
                                      </div>
                                    </div>
                                  </div>
                                </div>
                              </div>

                              <div className="p-3 bg-blue-50 border border-blue-200 rounded mb-3">
                                <p className="text-xs font-semibold mb-2">Why this pairing works:</p>
                                <div className="space-y-1">
                                  {pair.complementary_topics.map((topic, tidx) => (
                                    <p key={tidx} className="text-xs">
                                      • <strong>{topic.helper}</strong> can help <strong>{topic.learner}</strong> with <em>{topic.topic}</em>
                                    </p>
                                  ))}
                                </div>
                              </div>

                              <Button
                                className="w-full bg-green-600 hover:bg-green-700"
                                onClick={() => sendPeerGroupNotification(
                                  pair.student1.id,
                                  pair.student2.id,
                                  `You both have complementary skills. Consider studying together!`
                                )}
                              >
                                <Users className="w-4 h-4 mr-2" />
                                Send Notification to Both Students
                              </Button>
                            </div>
                          ))}
                        </div>
                      )}
                    </CardContent>
                  </Card>
                )}
              </div>
            )}
          </>
        )}

        {/* LEVEL 2: TOPIC DNA */}
        {drillLevel === 'topic' && topicDrillDown && (
          <>
            {/* AI Insight */}
            <Card className="border-l-4 border-l-primary">
              <CardContent className="p-4">
                <div className="flex items-start gap-3">
                  <Brain className="w-5 h-5 text-primary mt-1" />
                  <p className="text-sm">{topicDrillDown.insight}</p>
                </div>
              </CardContent>
            </Card>

            {/* Sub-Skills Breakdown */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Target className="w-5 h-5 text-primary" />
                  Sub-Skill Analysis
                </CardTitle>
                <CardDescription>
                  Understanding which specific skills students struggle with
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
                  {topicDrillDown.sub_skills.map((skill, idx) => (
                    <div
                      key={idx}
                      className={`p-4 rounded-lg border-2 ${
                        skill.color === 'green' ? 'bg-green-50 border-green-200' :
                        skill.color === 'amber' ? 'bg-amber-50 border-amber-200' :
                        'bg-red-50 border-red-200'
                      }`}
                    >
                      <p className="font-semibold text-sm mb-2">{skill.name}</p>
                      <p className={`text-3xl font-bold ${
                        skill.color === 'green' ? 'text-green-700' :
                        skill.color === 'amber' ? 'text-amber-700' :
                        'text-red-700'
                      }`}>
                        {skill.avg_percentage}%
                      </p>
                      <p className="text-xs text-muted-foreground mt-2">
                        {skill.question_count} question{skill.question_count !== 1 ? 's' : ''}
                      </p>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Questions in this Topic */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <FileText className="w-5 h-5 text-primary" />
                  Questions - Click to See Error Patterns
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="space-y-3">
                  {topicDrillDown.questions.map((q, idx) => (
                    <div
                      key={idx}
                      className={`p-4 rounded-lg border cursor-pointer transition-all hover:shadow-md ${
                        getScoreBg(q.avg_percentage)
                      }`}
                      onClick={() => handleQuestionClick(q)}
                    >
                      <div className="flex items-center justify-between">
                        <div className="flex items-center gap-3">
                          <span className="font-semibold">
                            {q.exam_name} - Q{q.question_number}
                          </span>
                          <Badge variant="outline">{q.max_marks} marks</Badge>
                        </div>
                        <div className="flex items-center gap-3">
                          <span className={`font-bold ${getScoreColor(q.avg_percentage)}`}>
                            {q.avg_percentage}% avg
                          </span>
                          <ChevronRight className="w-4 h-4 text-muted-foreground" />
                        </div>
                      </div>
                      {q.rubric && (
                        <p className="text-sm text-muted-foreground mt-2 line-clamp-1">
                          {q.rubric}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Struggling Students */}
            {topicDrillDown.struggling_students?.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-red-700">
                    <AlertTriangle className="w-5 h-5" />
                    Students Needing Attention ({topicDrillDown.struggling_students.length})
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
                    {topicDrillDown.struggling_students.map((student, idx) => (
                      <div
                        key={idx}
                        className="p-3 bg-red-50 border border-red-200 rounded-lg cursor-pointer hover:bg-red-100 transition-colors"
                        onClick={() => handleStudentClick(student.student_id)}
                      >
                        <div className="flex items-center justify-between">
                          <span className="font-medium">{student.student_name}</span>
                          <div className="flex items-center gap-2">
                            <Badge variant="destructive">{student.avg_percentage}%</Badge>
                            <Eye className="w-4 h-4 text-muted-foreground" />
                          </div>
                        </div>
                        <p className="text-xs text-muted-foreground mt-1">
                          {student.attempts} attempt{student.attempts !== 1 ? 's' : ''}
                        </p>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </>
        )}

        {/* LEVEL 3: QUESTION ERROR PATTERNS */}
        {drillLevel === 'question' && questionDrillDown && (
          <>
            {/* Question Stats */}
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <Card>
                <CardContent className="p-4">
                  <div className="flex items-center gap-3">
                    <Users className="w-8 h-8 text-blue-500" />
                    <div>
                      <p className="text-2xl font-bold">{questionDrillDown.statistics.total_students}</p>
                      <p className="text-xs text-muted-foreground">Total Students</p>
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card className={getScoreBg(questionDrillDown.statistics.avg_percentage)}>
                <CardContent className="p-4">
                  <div className="flex items-center gap-3">
                    <Target className="w-8 h-8 text-amber-500" />
                    <div>
                      <p className={`text-2xl font-bold ${getScoreColor(questionDrillDown.statistics.avg_percentage)}`}>
                        {questionDrillDown.statistics.avg_percentage}%
                      </p>
                      <p className="text-xs text-muted-foreground">Average Score</p>
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card className="bg-green-50 border-green-200">
                <CardContent className="p-4">
                  <div className="flex items-center gap-3">
                    <TrendingUp className="w-8 h-8 text-green-500" />
                    <div>
                      <p className="text-2xl font-bold text-green-600">{questionDrillDown.statistics.pass_count}</p>
                      <p className="text-xs text-muted-foreground">Passed (≥50%)</p>
                    </div>
                  </div>
                </CardContent>
              </Card>

              <Card className="bg-red-50 border-red-200">
                <CardContent className="p-4">
                  <div className="flex items-center gap-3">
                    <TrendingDown className="w-8 h-8 text-red-500" />
                    <div>
                      <p className="text-2xl font-bold text-red-600">{questionDrillDown.statistics.fail_count}</p>
                      <p className="text-xs text-muted-foreground">Failed (&lt;50%)</p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </div>

            {/* Error Groups - The "Aha!" Moment */}
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Zap className="w-5 h-5 text-amber-500" />
                  Error Pattern Analysis
                </CardTitle>
                <CardDescription>
                  Students grouped by their specific mistakes - Generate targeted practice for each group
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="space-y-4">
                  {questionDrillDown.error_groups.map((group, idx) => (
                    <div key={idx} className="p-4 bg-red-50 border border-red-200 rounded-lg">
                      <div className="flex items-start justify-between mb-3">
                        <div>
                          <h4 className="font-semibold text-red-800 flex items-center gap-2">
                            <AlertTriangle className="w-4 h-4" />
                            {group.type}
                          </h4>
                          <p className="text-sm text-red-600 mt-1">{group.description}</p>
                        </div>
                        <Badge variant="destructive">{group.count} students</Badge>
                      </div>

                      {/* Students in this group */}
                      <div className="mt-3">
                        <p className="text-xs font-semibold text-muted-foreground mb-2">Students:</p>
                        <div className="flex flex-wrap gap-2">
                          {group.students.map((student, sidx) => (
                            <div
                              key={sidx}
                              className="px-3 py-1 bg-white border border-red-200 rounded-full text-sm cursor-pointer hover:bg-red-100 transition-colors"
                              onClick={() => handleStudentClick(student.student_id)}
                            >
                              {student.student_name} ({student.score})
                            </div>
                          ))}
                        </div>
                      </div>

                      {/* Action Button */}
                      <div className="mt-4">
                        <Button 
                          variant="outline" 
                          className="w-full bg-white hover:bg-red-100"
                          onClick={() => toast.info(`Generate practice worksheet for ${group.type} - Coming soon!`)}
                        >
                          <FileText className="w-4 h-4 mr-2" />
                          Generate Practice Worksheet for This Group
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Top Performers */}
            {questionDrillDown.top_performers?.length > 0 && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2">
                    <Award className="w-5 h-5 text-green-600" />
                    Top Performers
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex flex-wrap gap-3">
                    {questionDrillDown.top_performers.map((student, idx) => (
                      <div key={idx} className="px-4 py-2 bg-green-50 border border-green-200 rounded-lg">
                        <p className="font-medium">{student.student_name}</p>
                        <p className="text-sm text-green-600">
                          {student.score}/{student.max_marks}
                        </p>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            )}
          </>
        )}
      </div>

      {/* Student Journey Modal */}
      <Dialog open={studentJourneyModal} onOpenChange={setStudentJourneyModal}>
        <DialogContent className="max-w-4xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Users className="w-5 h-5 text-primary" />
              Academic Health Record: {selectedStudentJourney?.student?.name}
            </DialogTitle>
            <DialogDescription>
              Complete performance journey with class comparisons and blind spot analysis
            </DialogDescription>
          </DialogHeader>

          {selectedStudentJourney && (
            <div className="space-y-6">
              {/* Overall Stats */}
              <div className="grid grid-cols-4 gap-3">
                <div className="p-3 bg-blue-50 rounded-lg">
                  <p className="text-2xl font-bold text-blue-600">{selectedStudentJourney.overall_stats.total_exams}</p>
                  <p className="text-xs text-muted-foreground">Total Exams</p>
                </div>
                <div className={`p-3 rounded-lg ${getScoreBg(selectedStudentJourney.overall_stats.avg_percentage)}`}>
                  <p className={`text-2xl font-bold ${getScoreColor(selectedStudentJourney.overall_stats.avg_percentage)}`}>
                    {selectedStudentJourney.overall_stats.avg_percentage}%
                  </p>
                  <p className="text-xs text-muted-foreground">Average</p>
                </div>
                <div className="p-3 bg-green-50 rounded-lg">
                  <p className="text-2xl font-bold text-green-600">{selectedStudentJourney.overall_stats.highest}%</p>
                  <p className="text-xs text-muted-foreground">Highest</p>
                </div>
                <div className="p-3 bg-red-50 rounded-lg">
                  <p className="text-2xl font-bold text-red-600">{selectedStudentJourney.overall_stats.lowest}%</p>
                  <p className="text-xs text-muted-foreground">Lowest</p>
                </div>
              </div>

              {/* Performance vs Class Average */}
              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Performance vs Class Average</CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="h-64">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={selectedStudentJourney.vs_class_avg}>
                        <CartesianGrid strokeDasharray="3 3" />
                        <XAxis dataKey="exam_name" tick={{ fontSize: 10 }} />
                        <YAxis />
                        <Tooltip />
                        <Bar dataKey="student_score" fill="#F97316" name="Student" />
                        <Bar dataKey="class_avg" fill="#94A3B8" name="Class Avg" />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                </CardContent>
              </Card>

              {/* Blind Spots */}
              {selectedStudentJourney.blind_spots?.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base text-red-700 flex items-center gap-2">
                      <AlertTriangle className="w-4 h-4" />
                      Blind Spots - Topics Needing Urgent Attention
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
                      {selectedStudentJourney.blind_spots.map((spot, idx) => (
                        <div key={idx} className="p-3 bg-red-50 border border-red-200 rounded-lg">
                          <p className="font-medium text-sm">{spot.topic}</p>
                          <p className="text-xl font-bold text-red-600">{spot.avg_score}%</p>
                          <p className="text-xs text-muted-foreground">{spot.attempts} attempts</p>
                        </div>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Strengths */}
              {selectedStudentJourney.strengths?.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base text-green-700 flex items-center gap-2">
                      <Award className="w-4 h-4" />
                      Strengths
                    </CardTitle>
                  </CardHeader>
                  <CardContent>
                    <div className="flex flex-wrap gap-2">
                      {selectedStudentJourney.strengths.map((strength, idx) => (
                        <Badge key={idx} className="bg-green-100 text-green-700">
                          {strength.topic} ({strength.avg_score}%)
                        </Badge>
                      ))}
                    </div>
                  </CardContent>
                </Card>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>
    </Layout>
  );
}