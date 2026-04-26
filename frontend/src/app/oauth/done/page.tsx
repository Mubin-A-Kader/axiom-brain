"use client";

import { useEffect } from "react";
import { Check } from "lucide-react";

export default function OAuthDonePage() {
  useEffect(() => {
    // Notify the opener (the wizard) that auth is complete, then close.
    if (window.opener) {
      window.opener.postMessage({ type: "n8n-oauth-complete" }, window.location.origin);
    }
    // Auto-close after a short delay so the user sees the message.
    const t = setTimeout(() => window.close(), 2000);
    return () => clearTimeout(t);
  }, []);

  return (
    <div className="min-h-screen bg-[#1E1E1C] flex items-center justify-center">
      <div className="flex flex-col items-center gap-4 text-center px-6">
        <div className="w-16 h-16 rounded-full bg-[#638A70]/20 flex items-center justify-center">
          <Check className="w-8 h-8 text-[#638A70]" />
        </div>
        <h1 className="text-xl font-semibold text-[#E6E1D8]">Authorization complete</h1>
        <p className="text-sm text-[#9A9589]">You can close this window.</p>
      </div>
    </div>
  );
}
