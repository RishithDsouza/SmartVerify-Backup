import React, { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { ShieldCheck, Eye, EyeOff, AlertCircle } from "lucide-react";
import toast from "react-hot-toast";
import { login } from "../services/api";
import { useAuth } from "../context/AuthContext";

export default function LoginPage() {
  const [form, setForm]         = useState({ email: "", password: "" });
  const [showPw, setShowPw]     = useState(false);
  const [loading, setLoading]   = useState(false);
  const [errorMsg, setErrorMsg] = useState("");
  const { user, loginUser }     = useAuth();
  const navigate                = useNavigate();

  // If already logged in, navigate directly to dashboard
  useEffect(() => {
    if (user) {
      navigate("/dashboard", { replace: true });
    }
  }, [user, navigate]);

  const parseErrorMessage = (err) => {
    const detail = err.response?.data?.detail;
    if (!detail) return err.message || "Login failed. Please check your credentials.";
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .map((d) => d.msg || (typeof d === "string" ? d : JSON.stringify(d)))
        .join(", ");
    }
    if (typeof detail === "object") {
      return detail.msg || JSON.stringify(detail);
    }
    return String(detail);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setErrorMsg("");
    setLoading(true);
    const cleanEmail = form.email.trim();
    const cleanPassword = form.password;

    try {
      const { data } = await login(cleanEmail, cleanPassword);
      loginUser(data.access_token, data.user);
      toast.success(`Welcome back, ${data.user.name}!`);
      navigate("/dashboard");
    } catch (err) {
      const msg = parseErrorMessage(err);
      setErrorMsg(msg);
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  };

  const handleQuickFill = (email, password) => {
    setForm({ email, password });
    setErrorMsg("");
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-primary-900 via-primary-700 to-blue-600 flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Logo */}
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-16 h-16 bg-white/20 rounded-2xl mb-4 backdrop-blur-sm shadow-inner">
            <ShieldCheck className="w-9 h-9 text-white" />
          </div>
          <h1 className="text-3xl font-bold text-white tracking-tight">SmartVerify</h1>
          <p className="text-blue-200 mt-1 text-sm">AI-Powered Loan Verification System</p>
        </div>

        {/* Card */}
        <div className="bg-white rounded-2xl shadow-2xl p-8 border border-slate-100">
          <h2 className="text-xl font-semibold text-slate-800 mb-2">Sign in to your account</h2>
          <p className="text-xs text-slate-500 mb-6">Enter your credentials to access the verification portal.</p>

          {errorMsg && (
            <div className="mb-4 p-3.5 bg-red-50 border border-red-200 rounded-xl flex items-start gap-2.5 text-red-700 text-sm animate-fadeIn">
              <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5 text-red-600" />
              <div className="flex-1">{errorMsg}</div>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Email address</label>
              <input
                type="email"
                required
                value={form.email}
                onChange={(e) => {
                  setForm({ ...form, email: e.target.value });
                  if (errorMsg) setErrorMsg("");
                }}
                placeholder="officer@smartverify.com"
                className="input w-full"
                autoComplete="email"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-slate-700 mb-1">Password</label>
              <div className="relative">
                <input
                  type={showPw ? "text" : "password"}
                  required
                  value={form.password}
                  onChange={(e) => {
                    setForm({ ...form, password: e.target.value });
                    if (errorMsg) setErrorMsg("");
                  }}
                  placeholder="••••••••"
                  className="input w-full pr-10"
                  autoComplete="current-password"
                />
                <button
                  type="button"
                  onClick={() => setShowPw(!showPw)}
                  className="absolute right-3 top-2.5 text-slate-400 hover:text-slate-600"
                  aria-label={showPw ? "Hide password" : "Show password"}
                >
                  {showPw ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>
            <button
              type="submit"
              disabled={loading}
              className="btn-primary w-full py-2.5 mt-2 flex items-center justify-center font-medium shadow-md transition-all disabled:opacity-50"
            >
              {loading ? (
                <span className="inline-flex items-center gap-2">
                  <svg className="animate-spin h-4 w-4 text-white" viewBox="0 0 24 24">
                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                  </svg>
                  Signing in…
                </span>
              ) : (
                "Sign in"
              )}
            </button>
          </form>

          {/* Demo credentials hint & quick-fill buttons */}
          <div className="mt-6 p-3.5 bg-slate-50 border border-slate-200 rounded-xl text-xs text-slate-600">
            <div className="font-semibold text-slate-700 mb-2 flex items-center justify-between">
              <span>Quick Demo Accounts:</span>
              <span className="text-[10px] text-blue-600 uppercase tracking-wide">1-Click Fill</span>
            </div>
            <div className="grid grid-cols-2 gap-2 mt-2">
              <button
                type="button"
                onClick={() => handleQuickFill("admin@smartverify.com", "admin123")}
                className="px-2.5 py-1.5 bg-white border border-slate-200 rounded-lg text-left hover:border-blue-500 hover:bg-blue-50 transition-colors shadow-sm"
              >
                <div className="font-medium text-slate-800">Admin</div>
                <div className="text-[10px] text-slate-400">admin123</div>
              </button>
              <button
                type="button"
                onClick={() => handleQuickFill("officer@smartverify.com", "officer123")}
                className="px-2.5 py-1.5 bg-white border border-slate-200 rounded-lg text-left hover:border-blue-500 hover:bg-blue-50 transition-colors shadow-sm"
              >
                <div className="font-medium text-slate-800">Loan Officer</div>
                <div className="text-[10px] text-slate-400">officer123</div>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
