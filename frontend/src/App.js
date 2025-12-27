import { useEffect, useState, useRef } from "react";
import { BrowserRouter, Routes, Route, Navigate, useLocation, useNavigate } from "react-router-dom";
import axios from "axios";
import { Toaster } from "./components/ui/sonner";

// Pages
import LoginPage from "./pages/LoginPage";
import AuthCallback from "./pages/AuthCallback";
import TeacherDashboard from "./pages/teacher/Dashboard";
import UploadGrade from "./pages/teacher/UploadGrade";
import ReviewPapers from "./pages/teacher/ReviewPapers";
import ClassReports from "./pages/teacher/ClassReports";
import ClassInsights from "./pages/teacher/ClassInsights";
import ManageStudents from "./pages/teacher/ManageStudents";
import ManageBatches from "./pages/teacher/ManageBatches";
import ManageExams from "./pages/teacher/ManageExams";
import ReEvaluations from "./pages/teacher/ReEvaluations";
import StudentDashboard from "./pages/student/Dashboard";
import StudentResults from "./pages/student/Results";
import StudentReEvaluation from "./pages/student/RequestReEvaluation";
import Settings from "./pages/Settings";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

// Configure axios
axios.defaults.withCredentials = true;

// Auth context
export const useAuth = () => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const checkAuth = async () => {
    try {
      const response = await axios.get(`${API}/auth/me`);
      setUser(response.data);
      return response.data;
    } catch (error) {
      setUser(null);
      return null;
    } finally {
      setLoading(false);
    }
  };

  const logout = async () => {
    try {
      await axios.post(`${API}/auth/logout`);
    } catch (error) {
      console.error("Logout error:", error);
    }
    setUser(null);
    window.location.href = "/login";
  };

  return { user, setUser, loading, checkAuth, logout };
};

// Protected Route wrapper
const ProtectedRoute = ({ children, allowedRoles }) => {
  const [isAuthenticated, setIsAuthenticated] = useState(null);
  const [user, setUser] = useState(null);
  const navigate = useNavigate();
  const location = useLocation();
  const checkedRef = useRef(false);

  useEffect(() => {
    // If user was passed from AuthCallback, use it
    if (location.state?.user) {
      setUser(location.state.user);
      setIsAuthenticated(true);
      return;
    }

    if (checkedRef.current) return;
    checkedRef.current = true;

    const checkAuth = async () => {
      try {
        const response = await axios.get(`${API}/auth/me`);
        setUser(response.data);
        setIsAuthenticated(true);

        // Check role
        if (allowedRoles && !allowedRoles.includes(response.data.role)) {
          const redirectPath = response.data.role === "teacher" ? "/teacher/dashboard" : "/student/dashboard";
          navigate(redirectPath, { replace: true });
        }
      } catch (error) {
        setIsAuthenticated(false);
        navigate("/login", { replace: true });
      }
    };

    checkAuth();
  }, [navigate, allowedRoles, location.state]);

  if (isAuthenticated === null) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <div className="animate-spin rounded-full h-12 w-12 border-4 border-primary border-t-transparent"></div>
      </div>
    );
  }

  if (!isAuthenticated) {
    return null;
  }

  return children({ user, setUser });
};

// App Router with session_id detection
function AppRouter() {
  const location = useLocation();

  // CRITICAL: Detect session_id synchronously during render
  // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
  if (location.hash?.includes("session_id=")) {
    return <AuthCallback />;
  }

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      
      {/* Teacher Routes */}
      <Route
        path="/teacher/dashboard"
        element={
          <ProtectedRoute allowedRoles={["teacher"]}>
            {(props) => <TeacherDashboard {...props} />}
          </ProtectedRoute>
        }
      />
      <Route
        path="/teacher/upload"
        element={
          <ProtectedRoute allowedRoles={["teacher"]}>
            {(props) => <UploadGrade {...props} />}
          </ProtectedRoute>
        }
      />
      <Route
        path="/teacher/review"
        element={
          <ProtectedRoute allowedRoles={["teacher"]}>
            {(props) => <ReviewPapers {...props} />}
          </ProtectedRoute>
        }
      />
      <Route
        path="/teacher/reports"
        element={
          <ProtectedRoute allowedRoles={["teacher"]}>
            {(props) => <ClassReports {...props} />}
          </ProtectedRoute>
        }
      />
      <Route
        path="/teacher/insights"
        element={
          <ProtectedRoute allowedRoles={["teacher"]}>
            {(props) => <ClassInsights {...props} />}
          </ProtectedRoute>
        }
      />
      <Route
        path="/teacher/students"
        element={
          <ProtectedRoute allowedRoles={["teacher"]}>
            {(props) => <ManageStudents {...props} />}
          </ProtectedRoute>
        }
      />
      <Route
        path="/teacher/batches"
        element={
          <ProtectedRoute allowedRoles={["teacher"]}>
            {(props) => <ManageBatches {...props} />}
          </ProtectedRoute>
        }
      />
      <Route
        path="/teacher/exams"
        element={
          <ProtectedRoute allowedRoles={["teacher"]}>
            {(props) => <ManageExams {...props} />}
          </ProtectedRoute>
        }
      />
      <Route
        path="/teacher/re-evaluations"
        element={
          <ProtectedRoute allowedRoles={["teacher"]}>
            {(props) => <ReEvaluations {...props} />}
          </ProtectedRoute>
        }
      />

      {/* Student Routes */}
      <Route
        path="/student/dashboard"
        element={
          <ProtectedRoute allowedRoles={["student"]}>
            {(props) => <StudentDashboard {...props} />}
          </ProtectedRoute>
        }
      />
      <Route
        path="/student/results"
        element={
          <ProtectedRoute allowedRoles={["student"]}>
            {(props) => <StudentResults {...props} />}
          </ProtectedRoute>
        }
      />
      <Route
        path="/student/re-evaluation"
        element={
          <ProtectedRoute allowedRoles={["student"]}>
            {(props) => <StudentReEvaluation {...props} />}
          </ProtectedRoute>
        }
      />

      {/* Shared Routes */}
      <Route
        path="/settings"
        element={
          <ProtectedRoute>
            {(props) => <Settings {...props} />}
          </ProtectedRoute>
        }
      />

      {/* Default redirect */}
      <Route path="/" element={<Navigate to="/login" replace />} />
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}

function App() {
  return (
    <div className="App">
      <BrowserRouter>
        <AppRouter />
      </BrowserRouter>
      <Toaster position="top-right" richColors />
    </div>
  );
}

export default App;
