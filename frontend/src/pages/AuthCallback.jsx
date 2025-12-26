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
        // Extract session_id from URL fragment
        const hash = window.location.hash;
        const params = new URLSearchParams(hash.substring(1));
        const sessionId = params.get("session_id");

        if (!sessionId) {
          console.error("No session_id found");
          navigate("/login", { replace: true });
          return;
        }

        // Exchange session_id for session_token
        const response = await axios.post(`${API}/auth/session`, {
          session_id: sessionId,
        });

        const user = response.data;

        // Clear URL fragment
        window.history.replaceState(null, "", window.location.pathname);

        // Redirect based on role
        const redirectPath = user.role === "student" 
          ? "/student/dashboard" 
          : "/teacher/dashboard";
        
        navigate(redirectPath, { replace: true, state: { user } });
      } catch (error) {
        console.error("Auth error:", error);
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
