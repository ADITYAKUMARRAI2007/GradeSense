import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";
import axios from "axios";
import { API } from "../App";

export default function AuthCallback() {
  const navigate = useNavigate();
  const hasProcessed = useRef(false);

  useEffect(() => {
    // Use ref to prevent double processing in StrictMode
    if (hasProcessed.current) return;
    hasProcessed.current = true;

    const processAuth = async () => {
      try {
        console.log("=== AUTH CALLBACK STARTED ===");
        console.log("Full URL:", window.location.href);
        console.log("Hash:", window.location.hash);
        
        // Extract session_id from URL fragment
        const hash = window.location.hash;
        const params = new URLSearchParams(hash.substring(1));
        const sessionId = params.get("session_id");

        console.log("Extracted session_id:", sessionId);

        if (!sessionId) {
          console.error("No session_id found in URL");
          alert("Authentication failed: No session ID received");
          navigate("/login", { replace: true });
          return;
        }

        // Get preferred role from localStorage
        const preferredRole = localStorage.getItem("preferredRole") || "teacher";
        console.log("Preferred role:", preferredRole);

        console.log("Calling API:", `${API}/auth/session`);
        
        // Exchange session_id for session_token (include preferred_role)
        // Use withCredentials to ensure cookies are sent
        const response = await axios.post(`${API}/auth/session`, {
          session_id: sessionId,
          preferred_role: preferredRole,
        }, {
          withCredentials: true,
          timeout: 15000  // 15 second timeout
        });

        console.log("API Response:", response.data);
        const user = response.data;

        // Clear URL fragment and localStorage
        window.history.replaceState(null, "", window.location.pathname);
        localStorage.removeItem("preferredRole");

        // Check profile completion status for new users
        try {
          const profileResponse = await axios.get(`${API}/profile/check`);
          console.log("Profile check:", profileResponse.data);
          
          // If this is a NEW user (profile_completed === false), redirect to profile setup
          if (profileResponse.data.profile_completed === false) {
            console.log("New user detected, redirecting to profile setup");
            navigate('/profile/setup', { replace: true });
            return;
          }
        } catch (profileError) {
          console.error("Profile check error:", profileError);
          // If profile check fails, proceed to dashboard
        }

        // Existing user - redirect to dashboard based on role
        const redirectPath = user.role === "student" 
          ? "/student/dashboard" 
          : "/teacher/dashboard";
        
        console.log("Redirecting to:", redirectPath);
        navigate(redirectPath, { replace: true, state: { user } });
      } catch (error) {
        console.error("=== AUTH ERROR ===");
        console.error("Error details:", error);
        console.error("Error response:", error.response?.data);
        console.error("Error status:", error.response?.status);
        console.error("Error code:", error.code);
        console.error("Error message:", error.message);
        
        // Better error message handling with more detail
        let errorMessage = "Authentication failed";
        
        if (error.code === 'ECONNABORTED' || error.message?.includes('timeout')) {
          errorMessage = "Connection timeout. The server took too long to respond. Please check your internet connection and try again.";
        } else if (error.code === 'ERR_NETWORK' || error.message === 'Network Error') {
          errorMessage = "Network Error. Unable to reach the authentication server. Please check:\n\n1. Your internet connection\n2. If you're behind a firewall or VPN\n3. Browser extensions that might block requests\n\nThen try again.";
        } else if (error.response?.data?.detail) {
          errorMessage = error.response.data.detail;
        } else if (error.response?.status === 401) {
          errorMessage = "Authentication session expired. Please try logging in again.";
        } else if (error.response?.status === 504) {
          errorMessage = "Gateway timeout. The authentication service is slow. Please wait a moment and try again.";
        } else if (error.message) {
          errorMessage = `${error.message}\n\nIf this persists, please try:\n1. Clearing your browser cache\n2. Using a different browser\n3. Checking your internet connection`;
        }
        
        alert(`Authentication failed:\n\n${errorMessage}`);
        navigate("/login", { replace: true });
      }
    };

    processAuth();
  }, [navigate]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <div className="text-center">
        <div className="animate-spin rounded-full h-12 w-12 border-4 border-primary border-t-transparent mx-auto mb-4"></div>
        <p className="text-muted-foreground">Signing you in...</p>
      </div>
    </div>
  );
}
