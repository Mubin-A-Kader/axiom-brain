import { createClient } from '@/lib/supabase/server'
import { redirect } from 'next/navigation'
import { headers } from 'next/headers'
import { Network, ArrowRight } from 'lucide-react'

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ message: string }>;
}) {
  const resolvedParams = await searchParams;

  const signIn = async (formData: FormData) => {
    'use server'

    const email = formData.get('email') as string
    const password = formData.get('password') as string
    let redirectPath = null;
    let errorMessage = null;
    
    try {
      const supabase = await createClient()
      const { error } = await supabase.auth.signInWithPassword({
        email,
        password,
      })

      if (error) {
        errorMessage = error.message;
      } else {
        redirectPath = '/';
      }
    } catch (err: any) {
      console.error('Login error:', err);
      if (err.message?.includes('fetch failed')) {
        errorMessage = 'System Connection Error: Ensure Supabase (Docker) is running and accessible at ' + process.env.NEXT_PUBLIC_SUPABASE_URL;
      } else {
        errorMessage = err.message || 'An unexpected error occurred.';
      }
    }

    if (errorMessage) {
      return redirect('/login?message=' + encodeURIComponent(errorMessage));
    }
    if (redirectPath) {
      return redirect(redirectPath);
    }
  }

  const signUp = async (formData: FormData) => {
    'use server'

    const email = formData.get('email') as string
    const password = formData.get('password') as string
    let message = null;
    
    try {
      const supabase = await createClient()
      const { error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          emailRedirectTo: `${process.env.NEXT_PUBLIC_SITE_URL || 'http://localhost:3000'}/auth/callback`,
        },
      })

      if (error) {
        message = error.message;
      } else {
        message = 'Registration successful. You can now authenticate.';
      }
    } catch (err: any) {
      message = 'Registration failed: ' + err.message;
    }

    if (message) {
      return redirect('/login?message=' + encodeURIComponent(message));
    }
  }

  return (
    <div className="flex h-screen bg-[#1E1E1C] font-sans text-[#E6E1D8] selection:bg-[#638A70] selection:text-[#1E1E1C] items-center justify-center relative overflow-hidden">
      
      {/* Background elements */}
      <div className="absolute inset-0 z-0 bg-[radial-gradient(circle_at_center,rgba(255,255,255,0.03)_0%,transparent_100%)] pointer-events-none" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[800px] bg-[#638A70]/5 blur-[120px] rounded-full pointer-events-none" />

      <div className="w-full max-w-md z-10 p-8">
        <div className="flex flex-col items-center justify-center mb-10">
          <div className="w-16 h-16 rounded-xl bg-[#2A2927] border border-[rgba(255,255,255,0.05)] flex items-center justify-center mb-6 shadow-[0_4px_12px_rgba(0,0,0,0.2)]">
            <Network className="w-8 h-8 text-[#638A70]" />
          </div>
          <h1 className="text-3xl font-heading font-semibold text-[#E6E1D8] mb-2 tracking-tight">
            Axiom Secure Access
          </h1>
          <p className="text-[#E6E1D8]/50 text-sm">
            Enter your credentials to access the control plane.
          </p>
        </div>

        <form className="bg-[#2A2927] p-8 rounded-xl border border-[rgba(255,255,255,0.05)] shadow-[0_8px_32px_rgba(0,0,0,0.3)]">
          {resolvedParams?.message && (
            <div className="mb-6 p-4 bg-[rgba(194,109,92,0.08)] border-l-[4px] border-[#C26D5C] text-[#E6E1D8] text-sm rounded-r flex items-center shadow-inner">
              {resolvedParams.message}
            </div>
          )}
          
          <div className="space-y-5">
            <div className="space-y-2">
              <label className="text-[11px] font-mono text-[#E6E1D8]/50 uppercase tracking-widest block ml-1" htmlFor="email">
                Work Email
              </label>
              <input
                className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg px-4 py-3 text-[#E6E1D8] focus:border-[#638A70]/50 outline-none transition-all shadow-inner"
                name="email"
                placeholder="you@company.com"
                required
              />
            </div>
            
            <div className="space-y-2">
              <label className="text-[11px] font-mono text-[#E6E1D8]/50 uppercase tracking-widest block ml-1" htmlFor="password">
                Password
              </label>
              <input
                className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg px-4 py-3 text-[#E6E1D8] focus:border-[#638A70]/50 outline-none transition-all shadow-inner"
                type="password"
                name="password"
                placeholder="••••••••"
                required
              />
            </div>
          </div>

          <div className="flex flex-col gap-3 mt-8">
            <button
              formAction={signIn}
              className="w-full flex items-center justify-center gap-2 bg-[#638A70] text-[#1E1E1C] px-5 py-3 rounded-md font-semibold text-sm transition-all duration-200 hover:bg-[#729E81] hover:-translate-y-[1px] hover:shadow-lg active:translate-y-0 shadow-[0_4px_12px_rgba(0,0,0,0.2)]"
            >
              Authenticate <ArrowRight className="w-4 h-4" />
            </button>
            <button
              formAction={signUp}
              className="w-full flex items-center justify-center gap-2 bg-transparent border border-[rgba(255,255,255,0.05)] text-[#E6E1D8]/70 px-5 py-3 rounded-md font-semibold text-sm transition-all duration-200 hover:bg-white/5 hover:text-[#E6E1D8]"
            >
              Initialize New Account
            </button>
          </div>
        </form>
        
        <div className="mt-8 text-center">
            <p className="text-[#E6E1D8]/30 text-xs font-mono">
              SYSTEM ENCRYPTED • UNAUTHORIZED ACCESS PROHIBITED
            </p>
        </div>
      </div>
    </div>
  )
}
