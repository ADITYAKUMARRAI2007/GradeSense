import { useNavigate } from 'react-router-dom';
import { BarChart3, Users, MessageSquare, Settings, Mail, Shield, TrendingUp, Database, FileText, Zap, Activity } from 'lucide-react';
import { Card, CardHeader, CardTitle, CardContent } from '../../components/ui/card';
import FeedbackBeacon from '../../components/FeedbackBeacon';

const AdminDashboard = () => {
  const navigate = useNavigate();

  const adminSections = [
    {
      title: 'Analytics Dashboard',
      description: 'View platform metrics, user engagement, and AI performance',
      icon: BarChart3,
      path: '/admin/analytics',
      color: 'bg-blue-500',
      stats: 'Real-time insights'
    },
    {
      title: 'User Management',
      description: 'Manage users, roles, permissions, and access control',
      icon: Users,
      path: '/admin/users',
      color: 'bg-green-500',
      stats: 'Full control'
    },
    {
      title: 'Feedback Management',
      description: 'View and respond to user feedback, bugs, and questions',
      icon: MessageSquare,
      path: '/admin/feedback',
      color: 'bg-purple-500',
      stats: 'Direct support'
    },
    {
      title: 'System Health',
      description: 'Monitor API performance, errors, and system status',
      icon: Activity,
      path: '/admin/system',
      color: 'bg-orange-500',
      stats: 'Coming soon',
      disabled: true
    },
    {
      title: 'Email Templates',
      description: 'Manage email templates and automated notifications',
      icon: Mail,
      path: '/admin/emails',
      color: 'bg-pink-500',
      stats: 'Coming soon',
      disabled: true
    },
    {
      title: 'Feature Flags',
      description: 'Control feature availability per user or globally',
      icon: Zap,
      path: '/admin/features',
      color: 'bg-yellow-500',
      stats: 'Coming soon',
      disabled: true
    },
    {
      title: 'Usage Quotas',
      description: 'Set limits on exams, papers, and API usage per user',
      icon: Database,
      path: '/admin/quotas',
      color: 'bg-indigo-500',
      stats: 'Coming soon',
      disabled: true
    },
    {
      title: 'Audit Logs',
      description: 'View detailed logs of all admin actions and changes',
      icon: FileText,
      path: '/admin/logs',
      color: 'bg-red-500',
      stats: 'Coming soon',
      disabled: true
    }
  ];

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-4">
            <div className="w-12 h-12 bg-primary rounded-lg flex items-center justify-center">
              <Shield className="w-6 h-6 text-white" />
            </div>
            <div>
              <h1 className="text-3xl font-bold text-gray-900">Admin Control Panel</h1>
              <p className="text-gray-500">Central hub for managing GradeSense platform</p>
            </div>
          </div>
        </div>

        {/* Quick Stats */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
          <Card className="border-l-4 border-l-blue-500">
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-gray-600 mb-1">Active Now</p>
                  <p className="text-2xl font-bold text-gray-900">12</p>
                </div>
                <Activity className="w-8 h-8 text-blue-500" />
              </div>
            </CardContent>
          </Card>

          <Card className="border-l-4 border-l-green-500">
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-gray-600 mb-1">Pending Feedback</p>
                  <p className="text-2xl font-bold text-gray-900">5</p>
                </div>
                <MessageSquare className="w-8 h-8 text-green-500" />
              </div>
            </CardContent>
          </Card>

          <Card className="border-l-4 border-l-purple-500">
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-gray-600 mb-1">API Health</p>
                  <p className="text-2xl font-bold text-green-600">99.8%</p>
                </div>
                <TrendingUp className="w-8 h-8 text-purple-500" />
              </div>
            </CardContent>
          </Card>

          <Card className="border-l-4 border-l-orange-500">
            <CardContent className="p-4">
              <div className="flex items-center justify-between">
                <div>
                  <p className="text-sm text-gray-600 mb-1">System Status</p>
                  <p className="text-2xl font-bold text-green-600">Healthy</p>
                </div>
                <Shield className="w-8 h-8 text-orange-500" />
              </div>
            </CardContent>
          </Card>
        </div>

        {/* Admin Sections Grid */}
        <div className="mb-8">
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Admin Tools</h2>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {adminSections.map((section) => (
              <Card
                key={section.path}
                className={`hover:shadow-lg transition-all duration-200 ${
                  section.disabled ? 'opacity-60 cursor-not-allowed' : 'cursor-pointer hover:scale-105'
                }`}
                onClick={() => !section.disabled && navigate(section.path)}
              >
                <CardContent className="p-6">
                  <div className="flex items-start justify-between mb-4">
                    <div className={`w-12 h-12 ${section.color} rounded-lg flex items-center justify-center`}>
                      <section.icon className="w-6 h-6 text-white" />
                    </div>
                    {section.disabled && (
                      <span className="text-xs bg-gray-200 text-gray-600 px-2 py-1 rounded-full">
                        Coming Soon
                      </span>
                    )}
                  </div>
                  <h3 className="text-lg font-semibold text-gray-900 mb-2">{section.title}</h3>
                  <p className="text-sm text-gray-600 mb-4">{section.description}</p>
                  <div className="flex items-center justify-between">
                    <span className="text-xs text-gray-500">{section.stats}</span>
                    {!section.disabled && (
                      <button className="text-sm text-primary font-medium hover:underline">
                        Open →
                      </button>
                    )}
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        </div>

        {/* Info Banner */}
        <Card className="bg-gradient-to-r from-blue-50 to-purple-50 border-blue-200">
          <CardContent className="p-6">
            <div className="flex items-start gap-4">
              <Shield className="w-8 h-8 text-blue-600 flex-shrink-0" />
              <div>
                <h3 className="text-lg font-semibold text-gray-900 mb-2">Admin Access</h3>
                <p className="text-sm text-gray-700 mb-3">
                  You have full administrative access to GradeSense. This panel gives you control over users, 
                  analytics, system settings, and platform configuration. All actions are logged for security.
                </p>
                <div className="flex items-center gap-4 text-sm text-gray-600">
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 bg-green-500 rounded-full animate-pulse"></div>
                    <span>Real-time monitoring</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 bg-blue-500 rounded-full"></div>
                    <span>Secure access</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 bg-purple-500 rounded-full"></div>
                    <span>Audit logged</span>
                  </div>
                </div>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Feedback Beacon */}
      <FeedbackBeacon user={{}} />
    </div>
  );
};

export default AdminDashboard;
