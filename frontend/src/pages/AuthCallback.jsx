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
        const response = await axios.post(`${API}/auth/session`, {
          session_id: sessionId,
          preferred_role: preferredRole,
        });

        console.log("API Response:", response.data);
        const user = response.data;

        // Clear URL fragment and localStorage
        window.history.replaceState(null, "", window.location.pathname);
        localStorage.removeItem("preferredRole");

        // Redirect based on role
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
        alert(`Authentication failed: ${error.response?.data?.detail || error.message}`);
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
