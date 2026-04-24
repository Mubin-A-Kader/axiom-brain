"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { 
  Database, Plus, Loader2, AlertCircle, Check, 
  Terminal, Activity, Trash2, Globe, Server, Shield, Edit, RefreshCw, Code
} from "lucide-react";
import { fetchSources, onboardSource, deleteSource, updateSource, syncSource } from "../../lib/api";
import { Source, SourceIn } from "../../types";
import { createClient } from "@/lib/supabase/client";

// Reusable Tactile Button from page.tsx
function TactileButton({ children, onClick, className = "", disabled = false, variant = "primary", type = "button" }: any) {
  const baseStyles = "px-5 py-2.5 rounded-md font-semibold text-sm transition-all duration-200 ease-out flex items-center justify-center cursor-pointer select-none";
  const variants = {
    primary: "bg-[#638A70] text-[#1E1E1C] hover:bg-[#729E81] shadow-[0_4px_12px_rgba(0,0,0,0.2)]",
    destructive: "bg-[#C26D5C] text-white hover:bg-[#D47E6D] shadow-[0_4px_12px_rgba(0,0,0,0.2)]",
    outline: "bg-transparent border border-[rgba(255,255,255,0.05)] text-[#E6E1D8] hover:bg-white/5"
  };

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`${baseStyles} ${variants[variant as keyof typeof variants]} ${disabled ? 'opacity-50 cursor-not-allowed shadow-none' : 'hover:-translate-y-[1px] hover:shadow-lg active:translate-y-0 active:shadow-md'} ${className}`}
    >
      {children}
    </button>
  );
}

const initialFormData = {
  source_id: "",
  db_type: "postgresql",
  db_url: "",
  description: "",
  config_json: "",
  use_ssh: false,
  ssh_host: "",
  ssh_port: "22",
  ssh_user: "",
  ssh_key: "",
  metrics: [] as { name: string, formula: string, description: string }[]
};

export default function DataSourcesPage() {
  const [sources, setSources] = useState<Source[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isAdding, setIsAdding] = useState(false);
  const [isCodeView, setIsCodeView] = useState(false);
  const [localJson, setLocalJson] = useState("");
  const [editingSourceId, setEditingSourceId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [isVerifying, setIsVerifying] = useState(true);

  const router = useRouter();

  const [formData, setFormData] = useState(initialFormData);

  useEffect(() => {
    const checkTenant = async () => {
      const supabase = createClient();
      const { data: { session } } = await supabase.auth.getSession();
      
      if (!session) {
        router.push("/login");
        return;
      }

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
            setTenantId(tenant.id);
          } else {
            router.push("/onboard");
          }
        } else {
          router.push("/onboard");
        }
      } catch (err) {
        console.error("Auth check failed", err);
      } finally {
        setIsVerifying(false);
      }
    };

    checkTenant();
  }, [router]);

  useEffect(() => {
    if (tenantId) loadSources();
  }, [tenantId]);

  async function loadSources() {
    if (!tenantId) return;
    try {
      setIsLoading(true);
      const data = await fetchSources(tenantId);
      setSources(data);
    } catch (err: any) {
      setError("Failed to load data sources.");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!tenantId) return;
    
    setError(null);
    setSuccess(null);
    setIsLoading(true);

    try {
      let finalUrl = formData.db_url;
      if (formData.db_type === 'mcp' && !finalUrl.startsWith('mcp://')) {
        finalUrl = 'mcp://' + finalUrl;
      }

      // Prepare config
      let mcp_config = formData.config_json ? JSON.parse(formData.config_json) : {};
      
      if (formData.use_ssh) {
        mcp_config.ssh = {
          host: formData.ssh_host,
          port: parseInt(formData.ssh_port) || 22,
          username: formData.ssh_user,
          private_key: formData.ssh_key
        };
      }

      let custom_rules = formData.metrics.length > 0 ? formData.metrics : undefined;

      if (editingSourceId) {
        await updateSource(tenantId, editingSourceId, {
          description: formData.description,
          db_url: finalUrl,
          db_type: formData.db_type,
          mcp_config: mcp_config,
          custom_rules: custom_rules
        });
        setSuccess("Source updated successfully.");
      } else {
        const payload: SourceIn = {
          ...formData,
          db_url: finalUrl,
          tenant_id: tenantId,
          mcp_config: mcp_config,
          custom_rules: custom_rules
        };
        await onboardSource(payload);
        setSuccess("Ingestion started successfully. It may take a minute to process the schema.");
      }
      
      setIsAdding(false);
      setEditingSourceId(null);
      setFormData(initialFormData);
      // Refresh list
      loadSources();
    } catch (err: any) {
      setError(err.message || "Operation failed.");
    } finally {
      setIsLoading(false);
    }
  }

  async function handleDelete(sourceId: string) {
    if (!tenantId) return;
    if (!confirm("Are you sure you want to remove this data source? This will not delete your database, but Axiom will lose access to it.")) return;

    setError(null);
    setSuccess(null);
    try {
      await deleteSource(tenantId, sourceId);
      setSuccess("Source removed successfully.");
      loadSources();
    } catch (err: any) {
      setError(err.message || "Failed to delete source.");
    }
  }

  async function handleSync(sourceId: string) {
    if (!tenantId) return;
    setError(null);
    setSuccess(null);
    try {
      await syncSource(tenantId, sourceId);
      setSuccess("Sync initialized for source: " + sourceId);
      loadSources();
    } catch (err: any) {
      setError(err.message || "Failed to trigger sync.");
    }
  }

  function startEdit(source: Source) {
    const ssh = source.mcp_config?.ssh || {};
    
    let metrics = [];
    if (source.custom_rules) {
        try {
            metrics = Array.isArray(source.custom_rules) ? source.custom_rules : JSON.parse(source.custom_rules);
            if (!Array.isArray(metrics)) metrics = [];
        } catch(e) {
            metrics = [];
        }
    }

    setEditingSourceId(source.source_id);
    setFormData({
      source_id: source.source_id,
      db_type: source.db_type,
      db_url: "", // Security: don't populate URL as it's sensitive
      description: source.description || "",
      config_json: source.mcp_config ? JSON.stringify(source.mcp_config, null, 2) : "",
      use_ssh: !!source.mcp_config?.ssh,
      ssh_host: ssh.host || "",
      ssh_port: String(ssh.port || "22"),
      ssh_user: ssh.username || "",
      ssh_key: ssh.private_key || "",
      metrics: metrics
    });
    setIsCodeView(false);
    setIsAdding(true);
  }

  if (isVerifying) {
    return (
      <div className="h-full w-full flex items-center justify-center bg-[#1E1E1C]">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="w-8 h-8 animate-spin text-[#638A70]" />
          <p className="text-sm font-mono text-[#E6E1D8]/30 uppercase tracking-widest">Verifying Authorization...</p>
        </div>
      </div>
    );
  }

  if (!tenantId) return null;

  return (
    <div className="h-full flex flex-col relative overflow-hidden bg-[#1E1E1C]">
      <div className="flex-1 overflow-y-auto scroll-smooth custom-scrollbar">
        <div className="max-w-5xl mx-auto px-8 py-12 pb-40">
          <header className="mb-12 flex justify-between items-end">
            <div>
              <h1 className="text-3xl font-heading font-semibold text-[#E6E1D8] mb-2 tracking-tight">
                Data Connectors
              </h1>
              <p className="text-[#E6E1D8]/60 text-base max-w-xl">
                Onboard new database sources into the Axiom ecosystem. We support direct connections and universal MCP adapters.
              </p>
            </div>
            {!isAdding && (
              <TactileButton onClick={() => {
                setEditingSourceId(null);
                setFormData(initialFormData);
                setIsCodeView(false);
                setIsAdding(true);
              }}>
                <Plus className="w-4 h-4 mr-2" />
                Add Connector
              </TactileButton>
            )}
          </header>

          {error && (
            <div className="mb-8 bg-[rgba(194,109,92,0.08)] border-l-[4px] border-l-[#C26D5C] rounded-r-md p-4 flex items-start gap-3 shadow-[0_4px_12px_rgba(0,0,0,0.2)]">
              <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5 text-[#C26D5C]" />
              <div className="text-[#E6E1D8] font-medium">{error}</div>
            </div>
          )}

          {success && (
            <div className="mb-8 bg-[rgba(99,138,112,0.08)] border-l-[4px] border-l-[#638A70] rounded-r-md p-4 flex items-start gap-3 shadow-[0_4px_12px_rgba(0,0,0,0.2)]">
              <Check className="w-5 h-5 flex-shrink-0 mt-0.5 text-[#638A70]" />
              <div className="text-[#E6E1D8] font-medium">{success}</div>
            </div>
          )}

          {isAdding ? (
            <section className="bg-[#2A2927] border border-[rgba(255,255,255,0.05)] rounded-xl p-8 shadow-[0_8px_32px_rgba(0,0,0,0.3)] mb-12 animate-in fade-in slide-in-from-top-4 duration-300">
              <div className="flex items-center gap-3 mb-8 border-b border-[rgba(255,255,255,0.05)] pb-6">
                <div className="w-10 h-10 rounded-lg bg-[#1E1E1C] flex items-center justify-center text-[#638A70] border border-[rgba(255,255,255,0.05)]">
                  {editingSourceId ? <Edit className="w-5 h-5" /> : <Server className="w-5 h-5" />}
                </div>
                <div>
                  <h2 className="text-xl font-heading font-semibold text-[#E6E1D8]">
                    {editingSourceId ? `Edit Source: ${editingSourceId}` : "Configure New Source"}
                  </h2>
                  <p className="text-sm text-[#E6E1D8]/50">
                    {editingSourceId ? "Update connection details or description" : "Enter connection details for schema extraction"}
                  </p>
                </div>
              </div>

              <form onSubmit={handleSubmit} className="space-y-6">
                <div className="grid grid-cols-2 gap-6">
                  <div className="space-y-2">
                    <label className="text-[11px] font-mono text-[#E6E1D8]/50 uppercase tracking-widest block ml-1">
                      Source Identifier
                    </label>
                    <input 
                      required
                      disabled={!!editingSourceId}
                      placeholder="e.g. sales_db"
                      className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg px-4 py-3 text-[#E6E1D8] focus:border-[#638A70]/50 outline-none transition-all shadow-inner disabled:opacity-50 disabled:cursor-not-allowed"
                      value={formData.source_id}
                      onChange={e => setFormData({...formData, source_id: e.target.value})}
                    />
                  </div>
                  <div className="space-y-2">
                    <label className="text-[11px] font-mono text-[#E6E1D8]/50 uppercase tracking-widest block ml-1">
                      Database Type
                    </label>
                    <select 
                      className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg px-4 py-3 text-[#E6E1D8] focus:border-[#638A70]/50 outline-none transition-all shadow-inner appearance-none cursor-pointer"
                      value={formData.db_type}
                      onChange={e => setFormData({...formData, db_type: e.target.value})}
                    >
                      <option value="postgresql">PostgreSQL (Direct)</option>
                      <option value="mysql">MySQL (Direct)</option>
                      <option value="mcp">Universal MCP Adapter</option>
                    </select>
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-[11px] font-mono text-[#E6E1D8]/50 uppercase tracking-widest block ml-1">
                    Connection URL / Command
                  </label>
                  <div className="relative">
                    <input 
                      required
                      placeholder={formData.db_type === 'mcp' ? "npx -y @modelcontextprotocol/server-postgres" : "postgresql://user:pass@localhost:5432/dbname"}
                      className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg px-4 py-3 pl-11 text-[#E6E1D8] focus:border-[#638A70]/50 outline-none transition-all shadow-inner font-mono text-sm"
                      value={formData.db_url}
                      onChange={e => setFormData({...formData, db_url: e.target.value})}
                    />
                    <Terminal className="w-4 h-4 absolute left-4 top-1/2 -translate-y-1/2 text-[#638A70]/50" />
                  </div>
                </div>

                <div className="space-y-2">
                  <label className="text-[11px] font-mono text-[#E6E1D8]/50 uppercase tracking-widest block ml-1">
                    Description
                  </label>
                  <textarea 
                    placeholder="Describe the data within this source..."
                    className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg px-4 py-3 text-[#E6E1D8] focus:border-[#638A70]/50 outline-none transition-all shadow-inner h-24 resize-none"
                    value={formData.description}
                    onChange={e => setFormData({...formData, description: e.target.value})}
                  />
                </div>

                {/* SSH Tunneling Section */}
                {(formData.db_type === 'postgresql' || formData.db_type === 'mysql') && (
                  <div className="space-y-6 border-t border-[rgba(255,255,255,0.05)] pt-6">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Shield className="w-4 h-4 text-[#638A70]" />
                        <h3 className="text-sm font-semibold text-[#E6E1D8] uppercase tracking-wider">SSH Tunneling</h3>
                      </div>
                      <label className="relative inline-flex items-center cursor-pointer">
                        <input 
                          type="checkbox" 
                          className="sr-only peer"
                          checked={formData.use_ssh}
                          onChange={e => setFormData({...formData, use_ssh: e.target.checked})}
                        />
                        <div className="w-9 h-5 bg-[#1E1E1C] peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-[#E6E1D8]/20 after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[#638A70]"></div>
                        <span className="ml-3 text-xs font-mono text-[#E6E1D8]/50 uppercase">Enable Tunnel</span>
                      </label>
                    </div>

                    {formData.use_ssh && (
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 animate-in fade-in zoom-in-95 duration-200">
                        <div className="space-y-2">
                          <label className="text-[10px] font-mono text-[#E6E1D8]/30 uppercase tracking-widest block ml-1">Bastion Host</label>
                          <input 
                            placeholder="bastion.example.com"
                            className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg px-4 py-2 text-xs text-[#E6E1D8] focus:border-[#638A70]/50 outline-none"
                            value={formData.ssh_host}
                            onChange={e => setFormData({...formData, ssh_host: e.target.value})}
                          />
                        </div>
                        <div className="space-y-2">
                          <label className="text-[10px] font-mono text-[#E6E1D8]/30 uppercase tracking-widest block ml-1">SSH Port</label>
                          <input 
                            placeholder="22"
                            className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg px-4 py-2 text-xs text-[#E6E1D8] focus:border-[#638A70]/50 outline-none"
                            value={formData.ssh_port}
                            onChange={e => setFormData({...formData, ssh_port: e.target.value})}
                          />
                        </div>
                        <div className="space-y-2">
                          <label className="text-[10px] font-mono text-[#E6E1D8]/30 uppercase tracking-widest block ml-1">SSH User</label>
                          <input 
                            placeholder="ubuntu"
                            className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg px-4 py-2 text-xs text-[#E6E1D8] focus:border-[#638A70]/50 outline-none"
                            value={formData.ssh_user}
                            onChange={e => setFormData({...formData, ssh_user: e.target.value})}
                          />
                        </div>
                        <div className="col-span-full space-y-2">
                          <label className="text-[10px] font-mono text-[#E6E1D8]/30 uppercase tracking-widest block ml-1">Private Key</label>
                          <textarea 
                            placeholder="-----BEGIN RSA PRIVATE KEY-----"
                            className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg px-4 py-3 text-[10px] text-[#E6E1D8] focus:border-[#638A70]/50 outline-none h-24 font-mono resize-none"
                            value={formData.ssh_key}
                            onChange={e => setFormData({...formData, ssh_key: e.target.value})}
                          />
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {formData.db_type === 'mcp' && (
                  <div className="space-y-2">
                    <label className="text-[11px] font-mono text-[#E6E1D8]/50 uppercase tracking-widest block ml-1">
                      Advanced Configuration (JSON)
                    </label>
                    <textarea 
                      placeholder='{ "env": { "SNOWFLAKE_ACCOUNT": "..." } }'
                      className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg px-4 py-3 text-[#E6E1D8] focus:border-[#638A70]/50 outline-none transition-all shadow-inner h-32 font-mono text-xs resize-none"
                      value={formData.config_json}
                      onChange={e => setFormData({...formData, config_json: e.target.value})}
                    />
                    <p className="text-[10px] text-[#E6E1D8]/30 ml-1">
                      Optional JSON to pass to the MCP server (e.g., env variables, tool maps).
                    </p>
                  </div>
                )}

                {/* Business Glossary Section */}
                <div className="space-y-4 border-t border-[rgba(255,255,255,0.05)] pt-6">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <Database className="w-4 h-4 text-[#638A70]" />
                      <h3 className="text-sm font-semibold text-[#E6E1D8] uppercase tracking-wider">Business Glossary (Semantic Layer)</h3>
                    </div>
                    <div className="flex items-center gap-4">
                      <button
                        type="button"
                        onClick={() => {
                          const nextMode = !isCodeView;
                          if (nextMode) {
                            setLocalJson(
                              formData.metrics.length > 0
                                ? JSON.stringify(formData.metrics, null, 2)
                                : ""
                            );
                          }
                          setIsCodeView(nextMode);
                        }}
                        className={`text-[10px] font-mono flex items-center gap-1.5 px-2 py-1 rounded border transition-all ${
                          isCodeView ? 'bg-[#638A70]/20 border-[#638A70] text-[#638A70]' : 'bg-[#1E1E1C] border-white/5 text-[#E6E1D8]/40 hover:text-[#E6E1D8]'
                        }`}
                      >
                        <Code className="w-3 h-3" /> {isCodeView ? 'SWITCH TO BUILDER' : 'SWITCH TO CODE'}
                      </button>
                      {!isCodeView && (
                        <button
                          type="button"
                          onClick={() => setFormData({...formData, metrics: [...formData.metrics, {name: "", formula: "", description: ""}]})}
                          className="text-xs font-semibold text-[#638A70] hover:text-[#729E81] flex items-center gap-1 transition-all cursor-pointer"
                        >
                          <Plus className="w-3 h-3" /> Add Metric
                        </button>
                      )}
                    </div>
                  </div>
                  <p className="text-[10px] text-[#E6E1D8]/40">Define common metrics to ensure the agent calculates them accurately every time.</p>
                  
                  {isCodeView ? (
                    <div className="space-y-2">
                      <textarea
                        className="w-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg px-4 py-3 text-[#E6E1D8] font-mono text-xs focus:border-[#638A70]/50 outline-none transition-all shadow-inner h-64 resize-none"
                        value={localJson}
                        onChange={(e) => {
                          const val = e.target.value;
                          setLocalJson(val);
                          try {
                            const parsed = JSON.parse(val);
                            if (Array.isArray(parsed)) {
                              setFormData({ ...formData, metrics: parsed });
                            }
                          } catch (err) {
                            // Allow invalid JSON while typing, don't update state yet
                          }
                        }}
                        placeholder={`[\n  {\n    "name": "Revenue",\n    "formula": "SUM(amount) WHERE status = 'completed'",\n    "description": "Use when asked for total sales or income"\n  },\n  {\n    "name": "Active Users",\n    "formula": "COUNT(DISTINCT user_id) WHERE last_seen > NOW() - INTERVAL 30 DAY",\n    "description": "Users active in the last 30 days"\n  }\n]`}
                      />
                      <p className="text-[9px] text-[#E6E1D8]/20 font-mono">PASTE VALID JSON ARRAY OF METRICS ABOVE</p>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      {formData.metrics.map((metric, idx) => (
                        <div key={idx} className="bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] rounded-lg p-4 space-y-3 relative group animate-in fade-in duration-200">
                          <button 
                            type="button"
                            onClick={() => {
                              const newMetrics = [...formData.metrics];
                              newMetrics.splice(idx, 1);
                              setFormData({...formData, metrics: newMetrics});
                            }}
                            className="absolute top-2 right-2 text-[#E6E1D8]/20 hover:text-[#C26D5C] opacity-0 group-hover:opacity-100 transition-all cursor-pointer"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                          <div className="grid grid-cols-2 gap-4">
                            <div className="space-y-1">
                              <label className="text-[10px] font-mono text-[#E6E1D8]/30 uppercase tracking-widest block ml-1">Metric Name</label>
                              <input 
                                placeholder="e.g. Active Users"
                                className="w-full bg-[#2A2927] border border-[rgba(255,255,255,0.05)] rounded px-3 py-1.5 text-xs text-[#E6E1D8] focus:border-[#638A70]/50 outline-none shadow-inner"
                                value={metric.name}
                                onChange={e => {
                                  const newMetrics = [...formData.metrics];
                                  newMetrics[idx].name = e.target.value;
                                  setFormData({...formData, metrics: newMetrics});
                                }}
                              />
                            </div>
                            <div className="space-y-1">
                              <label className="text-[10px] font-mono text-[#E6E1D8]/30 uppercase tracking-widest block ml-1">SQL Formula / Logic</label>
                              <input 
                                placeholder="e.g. SUM(price * qty)"
                                className="w-full bg-[#2A2927] border border-[rgba(255,255,255,0.05)] rounded px-3 py-1.5 text-xs font-mono text-[#E6E1D8] focus:border-[#638A70]/50 outline-none shadow-inner"
                                value={metric.formula}
                                onChange={e => {
                                  const newMetrics = [...formData.metrics];
                                  newMetrics[idx].formula = e.target.value;
                                  setFormData({...formData, metrics: newMetrics});
                                }}
                              />
                            </div>
                            <div className="col-span-full space-y-1">
                              <label className="text-[10px] font-mono text-[#E6E1D8]/30 uppercase tracking-widest block ml-1">Description (When to use this)</label>
                              <input 
                                placeholder="e.g. Use this when asked for revenue, sales, or total income."
                                className="w-full bg-[#2A2927] border border-[rgba(255,255,255,0.05)] rounded px-3 py-1.5 text-xs text-[#E6E1D8] focus:border-[#638A70]/50 outline-none shadow-inner"
                                value={metric.description}
                                onChange={e => {
                                  const newMetrics = [...formData.metrics];
                                  newMetrics[idx].description = e.target.value;
                                  setFormData({...formData, metrics: newMetrics});
                                }}
                              />
                            </div>
                          </div>
                        </div>
                      ))}
                      {formData.metrics.length === 0 && (
                        <div className="border border-dashed border-white/5 rounded-lg p-5 space-y-3">
                          <p className="text-[10px] font-mono text-[#E6E1D8]/25 uppercase tracking-widest text-center mb-4">Expected Format</p>
                          <div className="bg-[#1E1E1C] border border-white/5 rounded-lg p-4 space-y-3 opacity-30 pointer-events-none select-none">
                            <div className="grid grid-cols-2 gap-4">
                              <div className="space-y-1">
                                <p className="text-[9px] font-mono text-[#638A70] uppercase tracking-widest">Metric Name</p>
                                <p className="text-xs text-[#E6E1D8]">Revenue</p>
                              </div>
                              <div className="space-y-1">
                                <p className="text-[9px] font-mono text-[#638A70] uppercase tracking-widest">SQL Formula / Logic</p>
                                <p className="text-xs font-mono text-[#E6E1D8]">{"SUM(amount) WHERE status = 'completed'"}</p>
                              </div>
                              <div className="col-span-full space-y-1">
                                <p className="text-[9px] font-mono text-[#638A70] uppercase tracking-widest">Description (When to use this)</p>
                                <p className="text-xs text-[#E6E1D8]">Use when asked for total sales, income, or revenue</p>
                              </div>
                            </div>
                          </div>
                          <p className="text-[10px] text-[#E6E1D8]/20 text-center pt-1">Click <span className="text-[#638A70]">+ Add Metric</span> to define your first business rule</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <div className="flex gap-4 pt-4">
                  <TactileButton type="submit" disabled={isLoading}>
                    {isLoading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Shield className="w-4 h-4 mr-2" />}
                    {editingSourceId ? "Update Configuration" : "Initialize Ingestion"}
                  </TactileButton>
                  <TactileButton variant="outline" onClick={() => {
                    setIsAdding(false);
                    setEditingSourceId(null);
                  }}>
                    Cancel
                  </TactileButton>
                </div>
              </form>
            </section>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {isLoading ? (
                Array(4).fill(0).map((_, i) => (
                  <div key={i} className="h-40 bg-[#2A2927] border border-[rgba(255,255,255,0.05)] rounded-xl animate-pulse" />
                ))
              ) : sources.length === 0 ? (
                <div className="col-span-full py-20 bg-[#2A2927] border border-[rgba(255,255,255,0.05)] rounded-xl text-center">
                  <div className="w-16 h-16 rounded-full bg-[#1E1E1C] border border-[rgba(255,255,255,0.05)] flex items-center justify-center mx-auto mb-6 text-[#638A70]/30">
                    <Database className="w-8 h-8" />
                  </div>
                  <h3 className="text-[#E6E1D8] font-semibold text-lg mb-2">No data sources found</h3>
                  <p className="text-[#E6E1D8]/50 mb-8">Start by connecting your first database or warehouse.</p>
                  <TactileButton variant="outline" onClick={() => setIsAdding(true)} className="mx-auto">
                    Add Your First Connector
                  </TactileButton>
                </div>
              ) : (
                sources.map((source) => (
                  <div key={source.source_id} className="group bg-[#2A2927] border border-[rgba(255,255,255,0.05)] rounded-xl p-6 hover:border-[#638A70]/30 transition-all shadow-lg hover:shadow-xl relative overflow-hidden">
                    <div className="absolute top-0 right-0 p-4 opacity-0 group-hover:opacity-100 transition-opacity flex gap-1">
                      <button 
                        onClick={() => handleSync(source.source_id)}
                        className="p-2 text-[#E6E1D8]/30 hover:text-[#638A70] transition-colors cursor-pointer"
                        title="Sync Schema Now"
                      >
                        <RefreshCw className={`w-4 h-4 ${source.status === 'syncing' ? 'animate-spin' : ''}`} />
                      </button>
                      <button 
                        onClick={() => startEdit(source)}
                        className="p-2 text-[#E6E1D8]/30 hover:text-[#638A70] transition-colors cursor-pointer"
                        title="Edit Source"
                      >
                        <Edit className="w-4 h-4" />
                      </button>
                      <button 
                        onClick={() => handleDelete(source.source_id)}
                        className="p-2 text-[#E6E1D8]/30 hover:text-[#C26D5C] transition-colors cursor-pointer"
                        title="Delete Source"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>

                    <div className="flex items-start gap-4 mb-6">
                      <div className="w-12 h-12 rounded-lg bg-[#1E1E1C] flex items-center justify-center text-[#638A70] border border-[rgba(255,255,255,0.05)] shadow-inner">
                        {source.db_type === 'mcp' ? <Globe className="w-6 h-6" /> : <Database className="w-6 h-6" />}
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center justify-between">
                          <h3 className="text-lg font-bold text-[#E6E1D8]">{source.name}</h3>
                          <div className="flex items-center gap-2">
                            {source.status === 'active' && <Check className="w-3.5 h-3.5 text-[#638A70]" />}
                            {source.status === 'syncing' && <Loader2 className="w-3.5 h-3.5 text-[#638A70] animate-spin" />}
                            {source.status === 'failed' && <AlertCircle className="w-3.5 h-3.5 text-[#C26D5C]" />}
                            <span className={`text-[10px] font-mono font-bold uppercase tracking-widest ${
                              source.status === 'active' ? 'text-[#638A70]' : 
                              source.status === 'failed' ? 'text-[#C26D5C]' : 'text-[#E6E1D8]/40'
                            }`}>
                              {source.status}
                            </span>
                          </div>
                        </div>
                        <div className="flex items-center gap-2 mt-1">
                          <span className="text-[10px] font-mono bg-[#1E1E1C] px-1.5 py-0.5 rounded text-[#E6E1D8]/40 uppercase tracking-tighter">
                            {source.db_type}
                          </span>
                        </div>
                      </div>
                    </div>

                    <p className="text-sm text-[#E6E1D8]/60 line-clamp-2 mb-4 h-10">
                      {source.description || "No description provided."}
                    </p>

                    {source.status === 'failed' && source.error_message && (
                      <div className="mb-6 p-3 bg-[rgba(194,109,92,0.05)] border border-[rgba(194,109,92,0.1)] rounded text-[11px] text-[#C26D5C] font-mono line-clamp-3">
                        ERROR: {source.error_message}
                      </div>
                    )}

                    <div className="flex items-center justify-between pt-4 border-t border-[rgba(255,255,255,0.05)]">
                      <span className="text-[11px] font-mono text-[#E6E1D8]/30">
                        ID: {source.source_id}
                      </span>
                      <button className="text-xs font-semibold text-[#638A70] hover:underline flex items-center gap-1 transition-all">
                        View Schema <Plus className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
