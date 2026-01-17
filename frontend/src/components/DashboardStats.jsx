import React, { useState, useEffect } from 'react';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import { API } from '../App';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './ui/select';
import { TrendingDown, TrendingUp, AlertCircle, HelpCircle, Minus, ChevronRight } from 'lucide-react';
import { Badge } from './ui/badge';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { Button } from './ui/button';
import { toast } from 'sonner';

const DashboardStats = ({ batches = [] }) => {
  const navigate = useNavigate();
  const [selectedBatch, setSelectedBatch] = useState('all');
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [atRiskModal, setAtRiskModal] = useState(false);

  useEffect(() => {
    fetchStats();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedBatch]);

  const fetchStats = async () => {
    setLoading(true);
    try {
      const params = selectedBatch !== 'all' ? `?batch_id=${selectedBatch}` : '';
      const response = await axios.get(`${API}/dashboard/actionable-stats${params}`);
      setStats(response.data);
    } catch (error) {
      console.error('Error fetching actionable stats:', error);
      toast.error('Failed to load dashboard stats');
    } finally {
      setLoading(false);
    }
  };

  const getTrendIcon = (direction) => {
    if (direction === 'up') return <TrendingUp size={12} />;
    if (direction === 'down') return <TrendingDown size={12} />;
    return <Minus size={12} />;
  };

  const getTrendColor = (direction) => {
    if (direction === 'up') return 'bg-green-50 text-green-600';
    if (direction === 'down') return 'bg-red-50 text-red-600';
    return 'bg-gray-50 text-gray-600';
  };

  if (loading) {
    return (
      <div className="mb-8">
        <div className="h-8 w-48 bg-gray-200 rounded mb-4 animate-pulse"></div>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {[1, 2, 3, 4].map((i) => (
            <div key={i} className="h-32 bg-gray-100 rounded-xl animate-pulse"></div>
          ))}
        </div>
      </div>
    );
  }

  if (!stats) return null;

  const batchName = batches.find(b => b.batch_id === selectedBatch)?.name || 'All Batches';

  return (
    <div className="mb-8">
      {/* Batch Selector */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <h2 className="text-lg font-semibold text-gray-700">📚 Viewing:</h2>
          <Select value={selectedBatch} onValueChange={setSelectedBatch}>
            <SelectTrigger className="w-48 bg-white">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All Batches</SelectItem>
              {batches.map((batch) => (
                <SelectItem key={batch.batch_id} value={batch.batch_id}>
                  {batch.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <Badge variant="outline" className="text-xs">
          {batchName}
        </Badge>
      </div>

      {/* Actionable Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        
        {/* CARD 1: ACTION REQUIRED */}
        <div
          onClick={() => navigate('/teacher/review')}
          className="bg-white p-5 rounded-xl border-2 border-orange-100 shadow-sm hover:shadow-lg hover:border-orange-300 transition-all cursor-pointer group"
        >
          <div className="flex justify-between items-start">
            <div className="flex-1">
              <p className="text-xs font-semibold text-orange-600 uppercase tracking-wide flex items-center gap-1">
                <AlertCircle size={14} />
                Action Required
              </p>
              <h3 className="text-3xl font-bold text-gray-800 mt-2">
                {stats.action_required.total}
              </h3>
              <p className="text-sm text-gray-600 mt-2">
                {stats.action_required.quality_concerns > 0 && (
                  <span className="text-red-600 font-medium">
                    {stats.action_required.quality_concerns} Quality ⚠️
                  </span>
                )}
                {stats.action_required.quality_concerns > 0 && stats.action_required.pending_reviews > 0 && ' & '}
                {stats.action_required.pending_reviews > 0 && (
                  <span>
                    {stats.action_required.pending_reviews} Pending
                  </span>
                )}
              </p>
              <div className="mt-3 flex items-center text-orange-600 text-xs font-medium group-hover:translate-x-1 transition-transform">
                Review Now <ChevronRight size={14} className="ml-1" />
              </div>
            </div>
            <div className="p-2.5 bg-orange-50 rounded-lg group-hover:bg-orange-100 text-orange-600 transition-colors">
              <AlertCircle size={20} />
            </div>
          </div>
        </div>

        {/* CARD 2: CLASS PERFORMANCE */}
        <div
          onClick={() => navigate('/teacher/analytics')}
          className="bg-white p-5 rounded-xl border-2 border-gray-200 shadow-sm hover:shadow-lg hover:border-blue-300 transition-all cursor-pointer group"
        >
          <div className="flex justify-between items-start">
            <div className="flex-1">
              <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide">
                Performance
              </p>
              <div className="flex items-center gap-2 mt-2">
                <h3 className="text-3xl font-bold text-gray-800">
                  {stats.performance.current_avg}%
                </h3>
                {stats.performance.trend !== 0 && (
                  <Badge className={`text-xs px-2 py-0.5 rounded-full flex items-center gap-1 ${getTrendColor(stats.performance.trend_direction)}`}>
                    {getTrendIcon(stats.performance.trend_direction)}
                    {Math.abs(stats.performance.trend)}%
                  </Badge>
                )}
              </div>
              <p className="text-sm text-gray-600 mt-2">
                vs. previous exams
              </p>
              <div className="mt-3 flex items-center text-blue-600 text-xs font-medium group-hover:translate-x-1 transition-transform">
                View Trend <ChevronRight size={14} className="ml-1" />
              </div>
            </div>
            <div className="p-2.5 bg-gray-50 rounded-lg group-hover:bg-blue-50 text-blue-600 transition-colors">
              <TrendingUp size={20} />
            </div>
          </div>
        </div>

        {/* CARD 3: AT RISK STUDENTS */}
        <div
          onClick={() => setAtRiskModal(true)}
          className="bg-white p-5 rounded-xl border-2 border-red-100 shadow-sm hover:shadow-lg hover:border-red-300 transition-all cursor-pointer group"
        >
          <div className="flex justify-between items-start">
            <div className="flex-1">
              <p className="text-xs font-semibold text-red-600 uppercase tracking-wide flex items-center gap-1">
                <AlertCircle size={14} />
                Needs Support
              </p>
              <h3 className="text-3xl font-bold text-gray-800 mt-2">
                {stats.at_risk.count}
              </h3>
              <p className="text-sm text-gray-600 mt-2">
                Students below {stats.at_risk.threshold}%
              </p>
              <div className="mt-3 flex items-center text-red-600 text-xs font-medium group-hover:translate-x-1 transition-transform">
                View List <ChevronRight size={14} className="ml-1" />
              </div>
            </div>
            <div className="p-2.5 bg-red-50 rounded-lg group-hover:bg-red-100 text-red-600 transition-colors">
              <AlertCircle size={20} />
            </div>
          </div>
        </div>

        {/* CARD 4: HARDEST CONCEPT */}
        <div
          onClick={() => {
            if (stats.hardest_concept) {
              navigate(`/teacher/analytics?exam=${stats.hardest_concept.exam_id}`);
            } else {
              toast.info('No data available for hardest concept yet');
            }
          }}
          className="bg-white p-5 rounded-xl border-2 border-purple-100 shadow-sm hover:shadow-lg hover:border-purple-300 transition-all cursor-pointer group"
        >
          <div className="flex justify-between items-start">
            <div className="flex-1">
              <p className="text-xs font-semibold text-purple-600 uppercase tracking-wide flex items-center gap-1">
                <HelpCircle size={14} />
                Focus Area
              </p>
              {stats.hardest_concept ? (
                <>
                  <h3 className="text-sm font-bold text-gray-800 mt-2 truncate" title={stats.hardest_concept.topic}>
                    Q{stats.hardest_concept.question_number}: {stats.hardest_concept.topic}
                  </h3>
                  <p className="text-sm text-gray-600 mt-2">
                    Only <span className="font-bold text-red-600">{stats.hardest_concept.success_rate}%</span> correct
                  </p>
                  <div className="mt-3 flex items-center text-purple-600 text-xs font-medium group-hover:translate-x-1 transition-transform">
                    Deep Dive <ChevronRight size={14} className="ml-1" />
                  </div>
                </>
              ) : (
                <div className="mt-2">
                  <p className="text-sm text-gray-500">No data yet</p>
                  <p className="text-xs text-gray-400 mt-1">Grade more papers</p>
                </div>
              )}
            </div>
            <div className="p-2.5 bg-purple-50 rounded-lg group-hover:bg-purple-100 text-purple-600 transition-colors">
              <HelpCircle size={20} />
            </div>
          </div>
        </div>
      </div>

      {/* At Risk Students Modal */}
      <Dialog open={atRiskModal} onOpenChange={setAtRiskModal}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-red-700">
              <AlertCircle className="w-5 h-5" />
              Students Needing Support ({stats.at_risk.count})
            </DialogTitle>
            <DialogDescription>
              Students who scored below {stats.at_risk.threshold}% in recent exams. Consider intervention or additional support.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3 mt-4">
            {stats.at_risk.students.length > 0 ? (
              stats.at_risk.students.map((student, idx) => (
                <div key={idx} className="p-4 bg-red-50 border border-red-200 rounded-lg flex items-center justify-between">
                  <div>
                    <p className="font-semibold text-gray-800">{student.student_name}</p>
                    <p className="text-sm text-gray-600">
                      Avg Score: <span className="font-medium text-red-600">{student.avg_score}%</span>
                      {student.exams_failed > 1 && ` • Failed ${student.exams_failed} recent exams`}
                    </p>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setAtRiskModal(false);
                      navigate(`/teacher/analytics`);
                    }}
                  >
                    View Analytics
                  </Button>
                </div>
              ))
            ) : (
              <div className="text-center py-8 text-gray-500">
                <p>Great! No students currently at risk.</p>
              </div>
            )}
          </div>

          {stats.at_risk.count > 5 && (
            <p className="text-xs text-gray-500 text-center mt-4">
              Showing top 5 of {stats.at_risk.count} students. View full list in Analytics.
            </p>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default DashboardStats;
