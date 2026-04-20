"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { 
  Network, ArrowRight, Loader2, Rocket, Shield, Zap
} from "lucide-react";
import { createClient } from "@/lib/supabase/client";

export default function OnboardingPage() {
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [isLoading, setIsLoading] = useState(false);
  const [isVerifying, setIsVerifying] = useState(true);
  const [error, setError] = useState<string | null>(null);
  
  const router = useRouter();

  useEffect(() => {
    const checkStatus = async () => {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      
      if (!session) {
        router.push("/login");
        return;
      }

      // Check if user already has a tenant
      try {
        const hostname = typeof window !== 'undefined' ? window.location.hostname : '127.0.0.1';
        const API_URL = process.env.NEXT_PUBLIC_API_URL || `http://${hostname}:8080`;
        const res = await fetch(`${API_URL}/api/tenant`, {
          headers: {
            "Authorization": `Bearer ${session.access_token}`
          }
        });
        if (res.ok) {
          const tenant = await res.json();
          if (tenant) {
            router.push("/");
            return;
          }
        }
      } catch (err) {
        console.error("Failed to fetch tenant status", err);
      }
      
      setIsVerifying(false);
    };

    checkStatus();
  }, [router]);

  // Auto-generate slug from name
  useEffect(() => {
    setSlug(name.toLowerCase().replace(/[^a-z0-9]/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, ""));
  }, [name]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setIsLoading(true);

    try {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      
      const hostname = typeof window !== 'undefined' ? window.location.hostname : '127.0.0.1';
      const API_URL = process.env.NEXT_PUBLIC_API_URL || `http://${hostname}:8080`;
      const res = await fetch(`${API_URL}/api/tenant`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${session?.access_token}`
        },
        body: JSON.stringify({ name, id: slug })
      });

      if (!res.ok) {
        const data = await res.json();
        throw new Error(data.detail || "Failed to create workspace.");
      }

      router.push("/");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  };

  if (isVerifying) {
    return (
      <div className="h-screen w-full flex items-center justify-center bg-[#1E1E1C]">
        <Loader2 className="w-8 h-8 animate-spin text-[#638A70]" />
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-[#1E1E1C] font-sans text-[#E6E1D8] selection:bg-[#638A70] selection:text-[#1E1E1C] items-center justify-center relative overflow-hidden">
      
      {/* Background elements */}
      <div className="absolute inset-0 z-0 bg-[radial-gradient(circle_at_center,rgba(255,255,255,0.03)_0%,transparent_100%)] pointer-events-none" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-[#638A70]/5 blur-[120px] rounded-full pointer-events-none" />

      <div className="w-full max-w-xl z-10 p-8">
        <div className="flex flex-col items-center justify-center mb-10 text-center">
          <div className="w-16 h-16 rounded-xl bg-[#2A2927] border border-[rgba(255,255,255,0.05)] flex items-center justify-center mb-6 shadow-[0_4px_12px_rgba(0,0,0,0.2)]">
            <Zap className="w-8 h-8 text-[#638A70]" />
          </div>
          <h1 className="text-4xl font-heading font-bold text-[#E6E1D8] mb-3 tracking-tight">
            Initialize Your Nexus
          </h1>
          <p className="text-[#E6E1D8]/60 text-base max-w-sm">
            Create your Axiom Workspace to begin orchestrating your data intelligence.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="bg-[#2A2927] p-10 rounded-2xl border border-[rgba(255,255,255,0.05)] shadow-[0_20px_50px_rgba(0,0,0,0.4)] space-y-8">
          {error && (
            <div className="p-4 bg-[rgba(194,109,92,0.08)] border-l-[4px] border-[#C26D5C] text-[#E6E1D8] text-sm rounded-r flex items-center shadow-inner animate-in fade-in zoom-in duration-200">
              {error}
            </div>
          )}
          
          <div className="space-y-6">
            <div className="space-y-2 group">
              <label className="text-[11px] font-mono text-[#E6E1D8]/40 uppercase tracking-widest block ml-1 transition-colors group-focus-within:text-[#638A70]">
                Workspace Name
              </label>
              <input
                className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-xl px-5 py-4 text-[#E6E1D8] text-lg focus:border-[#638A70]/50 outline-none transition-all shadow-inner placeholder:text-[#E6E1D8]/10"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Acme Corp"
                required
                autoFocus
              />
            </div>
            
            <div className="space-y-2 group">
              <label className="text-[11px] font-mono text-[#E6E1D8]/40 uppercase tracking-widest block ml-1 transition-colors group-focus-within:text-[#638A70]">
                Workspace ID (Slug)
              </label>
              <div className="relative">
                <input
                  className="w-full bg-[#1E1E1C]/50 border border-[rgba(255,255,255,0.05)] rounded-xl px-5 py-4 pl-14 text-[#E6E1D8] font-mono text-sm focus:border-[#638A70]/50 outline-none transition-all shadow-inner"
                  value={slug}
                  onChange={(e) => setSlug(e.target.value)}
                  placeholder="acme-corp"
                  required
                />
                <Shield className="w-4 h-4 absolute left-5 top-1/2 -translate-y-1/2 text-[#E6E1D8]/20" />
              </div>
              <p className="text-[10px] text-[#E6E1D8]/30 ml-1">
                Your workspace URL will be identified by this unique handle.
              </p>
            </div>
          </div>

          <div className="pt-4">
            <button
              type="submit"
              disabled={isLoading || !name}
              className="w-full flex items-center justify-center gap-3 bg-[#638A70] text-[#1E1E1C] px-6 py-4 rounded-xl font-bold text-base transition-all duration-300 hover:bg-[#729E81] hover:-translate-y-1 hover:shadow-[0_10px_30px_rgba(99,138,112,0.3)] active:translate-y-0 disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:-translate-y-0"
            >
              {isLoading ? (
                <>
                  <Loader2 className="w-5 h-5 animate-spin" />
                  Orchestrating Workspace...
                </>
              ) : (
                <>
                  Establish Workspace <ArrowRight className="w-5 h-5" />
                </>
              )}
            </button>
          </div>
        </form>
        
        <div className="mt-10 flex items-center justify-center gap-6 opacity-30 grayscale hover:opacity-100 hover:grayscale-0 transition-all duration-500">
           <div className="flex items-center gap-2 text-[10px] font-mono tracking-tighter">
             <Rocket className="w-3 h-3" /> MULTI-TENANT ENGINE
           </div>
           <div className="w-1 h-1 rounded-full bg-[#E6E1D8]/20" />
           <div className="flex items-center gap-2 text-[10px] font-mono tracking-tighter">
             <Shield className="w-3 h-3" /> END-TO-END ISOLATION
           </div>
        </div>
      </div>
    </div>
  );
}
