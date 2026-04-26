"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { 
  Database, Plus, Loader2, AlertCircle, Check, 
  Terminal, Trash2, Server, Shield, Edit, RefreshCw, Code, Mail
} from "lucide-react";
import { fetchSources, onboardSource, deleteSource, updateSource, syncSource, fetchOAuthUrl } from "../../lib/api";
import { Source, SourceIn } from "../../types";
import { createClient } from "@/lib/supabase/client";
import { N8nConnectWizard } from "@/components/N8nConnectWizard";

// Reusable Tactile Button
function TactileButton({ children, onClick, className = "", disabled = false, variant = "primary", type = "button" }: any) {
  const variants = {
    primary: "tactile-button",
    destructive: "bg-tactile-warning hover:bg-tactile-warning/90 text-white shadow-tactile font-semibold px-5 py-2.5 rounded-lg transition-all active:scale-[0.98]",
    outline: "tactile-button-outline"
  };

  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className={`${variants[variant as keyof typeof variants]} flex items-center justify-center cursor-pointer select-none ${className}`}
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
  const [showN8nWizard, setShowN8nWizard] = useState(false);
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
      if (formData.db_type === 'gmail') {
        finalUrl = formData.db_url || 'gmail://oauth';
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
      <div className="h-full w-full flex items-center justify-center bg-tactile-base">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="w-8 h-8 animate-spin text-tactile-primary" />
          <p className="text-sm font-mono text-tactile-text/30 uppercase tracking-widest">Verifying Authorization...</p>
        </div>
      </div>
    );
  }

  if (!tenantId) return null;

  return (
    <div className="h-full flex flex-col relative overflow-hidden bg-tactile-base">
      {showN8nWizard && tenantId && (
        <N8nConnectWizard
          tenantId={tenantId}
          onClose={() => setShowN8nWizard(false)}
          onDone={(sourceId, label) => {
            setShowN8nWizard(false);
            setSuccess(`${label} connected successfully. Ingesting schema in background…`);
            fetchSources(tenantId).then(setSources).catch(() => null);
          }}
        />
      )}
      <div className="flex-1 overflow-y-auto scroll-smooth custom-scrollbar">
        <div className="max-w-5xl mx-auto px-8 py-12 pb-40">
          <header className="mb-12 flex justify-between items-end">
            <div>
              <h1 className="text-4xl font-heading font-bold text-tactile-text mb-3 tracking-tight">
                Data Connectors
              </h1>
              <p className="text-tactile-text/60 text-lg max-w-xl">
                Onboard new database sources into the Axiom ecosystem. We support direct connections and universal MCP adapters.
              </p>
            </div>
            {!isAdding && (
              <div className="flex items-center gap-3">
                <TactileButton variant="outline" onClick={() => setShowN8nWizard(true)}>
                  <Plus className="w-4 h-4 mr-2" />
                  Connect App
                </TactileButton>
                <TactileButton onClick={() => {
                  setEditingSourceId(null);
                  setFormData(initialFormData);
                  setIsCodeView(false);
                  setIsAdding(true);
                }}>
                  <Plus className="w-4 h-4 mr-2" />
                  Add Connector
                </TactileButton>
              </div>
            )}
          </header>

          {error && (
            <div className="mb-8 bg-tactile-warning/10 border-l-[4px] border-l-tactile-warning rounded-r-lg p-4 flex items-start gap-3 shadow-tactile animate-in slide-in-from-left-2 duration-300">
              <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5 text-tactile-warning" />
              <div className="text-tactile-text font-medium">{error}</div>
            </div>
          )}

          {success && (
            <div className="mb-8 bg-tactile-primary/10 border-l-[4px] border-l-tactile-primary rounded-r-lg p-4 flex items-start gap-3 shadow-tactile animate-in slide-in-from-left-2 duration-300">
              <Check className="w-5 h-5 flex-shrink-0 mt-0.5 text-tactile-primary" />
              <div className="text-tactile-text font-medium">{success}</div>
            </div>
          )}

          {isAdding ? (
            <section className="tactile-card p-10 shadow-2xl mb-12 animate-in fade-in slide-in-from-top-4 duration-500">
              <div className="flex items-center gap-4 mb-10 border-b border-tactile-border pb-8">
                <div className="w-12 h-12 rounded-xl bg-tactile-base flex items-center justify-center text-tactile-primary border border-tactile-border shadow-tactile-inner">
                  {editingSourceId ? <Edit className="w-6 h-6" /> : <Server className="w-6 h-6" />}
                </div>
                <div>
                  <h2 className="text-2xl font-heading font-bold text-tactile-text">
                    {editingSourceId ? `Edit Source: ${editingSourceId}` : "Configure New Source"}
                  </h2>
                  <p className="text-sm text-tactile-text/50">
                    {editingSourceId ? "Update connection details or description" : "Enter connection details for schema extraction"}
                  </p>
                </div>
              </div>

              <form onSubmit={handleSubmit} className="space-y-8">
                <div className="space-y-8 mb-8">
                  <div>
                    <label className="text-[11px] font-mono text-tactile-text/40 uppercase tracking-[0.2em] block ml-1 mb-4">
                      Databases
                    </label>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                      {[
                        { id: "postgresql", name: "PostgreSQL", desc: "Direct DB Connection", iconUrl: "https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/postgresql/postgresql-original.svg" },
                        { id: "mysql", name: "MySQL", desc: "Direct DB Connection", iconUrl: "https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/mysql/mysql-original.svg" },
                        { id: "mongodb", name: "MongoDB", desc: "Document Database", iconUrl: "https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/mongodb/mongodb-original.svg", disabled: false }
                      ].map((type) => {
                        const Icon = (type as any).icon;
                        const isSelected = formData.db_type === type.id;
                        return (
                          <div
                            key={type.id}
                            onClick={() => !type.disabled && setFormData({...formData, db_type: type.id})}
                            className={`relative p-5 rounded-2xl border transition-all duration-300 flex flex-col items-center text-center gap-4 ${
                              type.disabled 
                                ? "opacity-40 cursor-not-allowed bg-tactile-base border-tactile-border" 
                                : isSelected
                                  ? "bg-tactile-primary/10 border-tactile-primary shadow-[inset_0_0_20px_rgba(125,163,139,0.1)] cursor-pointer scale-[1.02]"
                                  : "bg-tactile-base border-tactile-border hover:border-tactile-primary/50 cursor-pointer"
                            }`}
                          >
                            <div className={`w-14 h-14 rounded-2xl flex items-center justify-center transition-all ${
                              isSelected ? "bg-tactile-primary/20 text-tactile-primary shadow-tactile" : "bg-tactile-surface text-tactile-text/30"
                            }`}>
                              {type.iconUrl ? (
                                <img src={type.iconUrl} alt={type.name} className="w-8 h-8 object-contain" />
                              ) : Icon ? (
                                <Icon className="w-7 h-7" />
                              ) : null}
                            </div>
                            <div>
                              <div className={`font-bold tracking-tight ${isSelected ? "text-tactile-primary" : "text-tactile-text"}`}>
                                {type.name}
                              </div>
                              <div className="text-[10px] text-tactile-text/40 mt-1 uppercase tracking-wider font-mono">
                                {type.desc}
                              </div>
                            </div>
                            {isSelected && (
                              <div className="absolute top-4 right-4 text-tactile-primary">
                                <Check className="w-5 h-5" />
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>

                  <div>
                    <label className="text-[11px] font-mono text-tactile-text/40 uppercase tracking-[0.2em] block ml-1 mb-4">
                      App Adapters (MCP)
                    </label>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
                      {[
                        { id: "gmail", name: "Gmail Adapter", desc: "OAuth 2.0 Flow", iconUrl: "https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/google/google-original.svg", disabled: false },
                      ].map((type) => {
                        const Icon = (type as any).icon;
                        const isSelected = formData.db_type === type.id;
                        return (
                          <div
                            key={type.id}
                            onClick={() => !type.disabled && setFormData({...formData, db_type: type.id})}
                            className={`relative p-5 rounded-2xl border transition-all duration-300 flex flex-col items-center text-center gap-4 ${
                              type.disabled 
                                ? "opacity-40 cursor-not-allowed bg-tactile-base border-tactile-border" 
                                : isSelected
                                  ? "bg-tactile-primary/10 border-tactile-primary shadow-[inset_0_0_20px_rgba(125,163,139,0.1)] cursor-pointer scale-[1.02]"
                                  : "bg-tactile-base border-tactile-border hover:border-tactile-primary/50 cursor-pointer"
                            }`}
                          >
                            <div className={`w-14 h-14 rounded-2xl flex items-center justify-center transition-all ${
                              isSelected ? "bg-tactile-primary/20 text-tactile-primary shadow-tactile" : "bg-tactile-surface text-tactile-text/30"
                            }`}>
                              {type.iconUrl ? (
                                <img src={type.iconUrl} alt={type.name} className="w-7 h-7 object-contain" />
                              ) : Icon ? (
                                <Icon className="w-7 h-7" />
                              ) : null}
                            </div>
                            <div>
                              <div className={`font-bold tracking-tight ${isSelected ? "text-tactile-primary" : "text-tactile-text"}`}>
                                {type.name}
                              </div>
                              <div className="text-[10px] text-tactile-text/40 mt-1 uppercase tracking-wider font-mono">
                                {type.desc}
                              </div>
                            </div>
                            {isSelected && (
                              <div className="absolute top-4 right-4 text-tactile-primary">
                                <Check className="w-5 h-5" />
                              </div>
                            )}
                          </div>
                        )
                      })}
                    </div>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                  <div className="space-y-3">
                    <label className="text-[11px] font-mono text-tactile-text/40 uppercase tracking-[0.2em] block ml-1">
                      Source Identifier
                    </label>
                    <input 
                      required
                      disabled={!!editingSourceId}
                      placeholder={formData.db_type === 'gmail' ? "primary_gmail" : "sales_db"}
                      className="tactile-input w-full disabled:opacity-40"
                      value={formData.source_id}
                      onChange={e => setFormData({...formData, source_id: e.target.value})}
                    />
                  </div>
                  {formData.db_type !== 'gmail' ? (
                    <div className="space-y-3">
                      <label className="text-[11px] font-mono text-tactile-text/40 uppercase tracking-[0.2em] block ml-1">
                        Connection URL
                      </label>
                      <div className="relative">
                        <input 
                          required
                          placeholder="postgresql://user:pass@localhost:5432/dbname"
                          className="tactile-input w-full pl-12 font-mono text-sm"
                          value={formData.db_url}
                          onChange={e => setFormData({...formData, db_url: e.target.value})}
                        />
                        <Terminal className="w-5 h-5 absolute left-4 top-1/2 -translate-y-1/2 text-tactile-primary/40" />
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <label className="text-[11px] font-mono text-tactile-text/40 uppercase tracking-[0.2em] block ml-1">
                        Authentication
                      </label>
                      <div className="w-full bg-tactile-primary/5 border border-tactile-primary/20 rounded-xl px-5 py-3 flex items-center justify-between shadow-tactile-inner">
                        <span className="text-sm text-tactile-text/80 font-medium">Connect with Google Workspace</span>
                        <button 
                          type="button"
                          disabled={isLoading}
                          onClick={async () => {
                            if (!formData.source_id) {
                              setError("Please enter a Source Identifier before starting OAuth.");
                              return;
                            }
                            try {
                              setIsLoading(true);
                              setError(null);
                              const res = await fetchOAuthUrl({
                                connector: "gmail",
                                tenant_id: tenantId!,
                                source_id: formData.source_id
                              });
                              window.location.href = res.url;
                            } catch (err: any) {
                              setError(err.message || "Failed to generate OAuth URL.");
                              setIsLoading(false);
                            }
                          }}
                          className="bg-tactile-primary text-tactile-base text-xs font-bold px-4 py-2 rounded-lg cursor-pointer hover:bg-tactile-primary-hover transition-all shadow-tactile active:scale-95 disabled:opacity-50"
                        >
                          {isLoading ? "Starting..." : "OAuth Flow →"}
                        </button>
                      </div>
                    </div>
                  )}
                </div>

                <div className="space-y-3">
                  <label className="text-[11px] font-mono text-tactile-text/40 uppercase tracking-[0.2em] block ml-1">
                    Description
                  </label>
                  <textarea 
                    placeholder="Describe the data within this source..."
                    className="tactile-input w-full h-32 resize-none leading-relaxed"
                    value={formData.description}
                    onChange={e => setFormData({...formData, description: e.target.value})}
                  />
                </div>

                {/* SSH Tunneling Section */}
                {(formData.db_type === 'postgresql' || formData.db_type === 'mysql') && (
                  <div className="space-y-8 border-t border-tactile-border pt-8">
                    <div className="flex items-center justify-between bg-tactile-base/50 p-4 rounded-xl border border-tactile-border">
                      <div className="flex items-center gap-3">
                        <Shield className="w-5 h-5 text-tactile-primary" />
                        <h3 className="text-xs font-bold text-tactile-text uppercase tracking-widest">SSH Tunneling</h3>
                      </div>
                      <label className="relative inline-flex items-center cursor-pointer">
                        <input 
                          type="checkbox" 
                          className="sr-only peer"
                          checked={formData.use_ssh}
                          onChange={e => setFormData({...formData, use_ssh: e.target.checked})}
                        />
                        <div className="w-11 h-6 bg-tactile-surface border border-tactile-border peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-tactile-text/20 after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-tactile-primary peer-checked:after:bg-tactile-base"></div>
                        <span className="ml-4 text-[10px] font-mono text-tactile-text/40 uppercase tracking-widest">Enable Tunnel</span>
                      </label>
                    </div>

                    {formData.use_ssh && (
                      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 animate-in fade-in zoom-in-95 duration-300">
                        <div className="space-y-2">
                          <label className="text-[10px] font-mono text-tactile-text/30 uppercase tracking-[0.2em] block ml-1">Bastion Host</label>
                          <input 
                            placeholder="bastion.example.com"
                            className="tactile-input w-full py-2 text-xs"
                            value={formData.ssh_host}
                            onChange={e => setFormData({...formData, ssh_host: e.target.value})}
                          />
                        </div>
                        <div className="space-y-2">
                          <label className="text-[10px] font-mono text-tactile-text/30 uppercase tracking-[0.2em] block ml-1">SSH Port</label>
                          <input 
                            placeholder="22"
                            className="tactile-input w-full py-2 text-xs"
                            value={formData.ssh_port}
                            onChange={e => setFormData({...formData, ssh_port: e.target.value})}
                          />
                        </div>
                        <div className="space-y-2">
                          <label className="text-[10px] font-mono text-tactile-text/30 uppercase tracking-[0.2em] block ml-1">SSH User</label>
                          <input 
                            placeholder="ubuntu"
                            className="tactile-input w-full py-2 text-xs"
                            value={formData.ssh_user}
                            onChange={e => setFormData({...formData, ssh_user: e.target.value})}
                          />
                        </div>
                        <div className="col-span-full space-y-2">
                          <label className="text-[10px] font-mono text-tactile-text/30 uppercase tracking-[0.2em] block ml-1">Private Key</label>
                          <textarea 
                            placeholder="-----BEGIN RSA PRIVATE KEY-----"
                            className="tactile-input w-full h-32 font-mono text-[11px] leading-tight resize-none"
                            value={formData.ssh_key}
                            onChange={e => setFormData({...formData, ssh_key: e.target.value})}
                          />
                        </div>
                      </div>
                    )}
                  </div>
                )}

                {/* Business Glossary Section */}
                <div className="space-y-6 border-t border-tactile-border pt-8">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      <Database className="w-5 h-5 text-tactile-primary" />
                      <h3 className="text-sm font-bold text-tactile-text uppercase tracking-widest">Business Glossary (Semantic Layer)</h3>
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
                        className={`text-[10px] font-mono flex items-center gap-2 px-3 py-1.5 rounded-lg border transition-all ${
                          isCodeView ? 'bg-tactile-primary/20 border-tactile-primary text-tactile-primary' : 'bg-tactile-base border-tactile-border text-tactile-text/40 hover:text-tactile-text'
                        }`}
                      >
                        <Code className="w-3.5 h-3.5" /> {isCodeView ? 'SWITCH TO BUILDER' : 'SWITCH TO CODE'}
                      </button>
                      {!isCodeView && (
                        <button
                          type="button"
                          onClick={() => setFormData({...formData, metrics: [...formData.metrics, {name: "", formula: "", description: ""}]})}
                          className="text-xs font-bold text-tactile-primary hover:text-tactile-primary-hover flex items-center gap-2 transition-all cursor-pointer bg-tactile-primary/5 px-3 py-1.5 rounded-lg border border-tactile-primary/20 shadow-sm"
                        >
                          <Plus className="w-3.5 h-3.5" /> Add Metric
                        </button>
                      )}
                    </div>
                  </div>
                  <p className="text-[11px] text-tactile-text/40 leading-relaxed max-w-2xl">Define common metrics to ensure the agent calculates them accurately every time. This creates a bridge between business language and SQL.</p>
                  
                  {isCodeView ? (
                    <div className="space-y-3">
                      <textarea
                        className="tactile-input w-full h-80 font-mono text-xs leading-relaxed"
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
                            // Allow invalid JSON while typing
                          }
                        }}
                        placeholder={`[\n  {\n    "name": "Revenue",\n    "formula": "SUM(amount) WHERE status = 'completed'",\n    "description": "Use when asked for total sales or income"\n  }\n]`}
                      />
                      <p className="text-[9px] text-tactile-text/20 font-mono tracking-widest uppercase">PASTE VALID JSON ARRAY OF METRICS ABOVE</p>
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {formData.metrics.map((metric, idx) => (
                        <div key={idx} className="bg-tactile-base border border-tactile-border rounded-xl p-6 space-y-4 relative group animate-in fade-in duration-300 shadow-tactile-inner">
                          <button 
                            type="button"
                            onClick={() => {
                              const newMetrics = [...formData.metrics];
                              newMetrics.splice(idx, 1);
                              setFormData({...formData, metrics: newMetrics});
                            }}
                            className="absolute top-4 right-4 text-tactile-text/20 hover:text-tactile-warning opacity-0 group-hover:opacity-100 transition-all cursor-pointer p-1 rounded hover:bg-tactile-warning/5"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                          <div className="grid grid-cols-2 gap-6">
                            <div className="space-y-2">
                              <label className="text-[10px] font-mono text-tactile-text/30 uppercase tracking-[0.2em] block ml-1">Metric Name</label>
                              <input 
                                placeholder="e.g. Active Users"
                                className="tactile-input w-full py-2 text-xs bg-tactile-surface border-tactile-border/50"
                                value={metric.name}
                                onChange={e => {
                                  const newMetrics = [...formData.metrics];
                                  newMetrics[idx].name = e.target.value;
                                  setFormData({...formData, metrics: newMetrics});
                                }}
                              />
                            </div>
                            <div className="space-y-2">
                              <label className="text-[10px] font-mono text-tactile-text/30 uppercase tracking-[0.2em] block ml-1">SQL Formula / Logic</label>
                              <input 
                                placeholder="e.g. SUM(price * qty)"
                                className="tactile-input w-full py-2 text-xs font-mono bg-tactile-surface border-tactile-border/50"
                                value={metric.formula}
                                onChange={e => {
                                  const newMetrics = [...formData.metrics];
                                  newMetrics[idx].formula = e.target.value;
                                  setFormData({...formData, metrics: newMetrics});
                                }}
                              />
                            </div>
                            <div className="col-span-full space-y-2">
                              <label className="text-[10px] font-mono text-tactile-text/30 uppercase tracking-[0.2em] block ml-1">Description (When to use this)</label>
                              <input 
                                placeholder="e.g. Use this when asked for revenue, sales, or total income."
                                className="tactile-input w-full py-2 text-xs bg-tactile-surface border-tactile-border/50"
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
                        <div className="border border-dashed border-tactile-border rounded-2xl p-8 space-y-6 bg-tactile-base/30">
                          <p className="text-[10px] font-mono text-tactile-text/20 uppercase tracking-[0.3em] text-center">Reference Schema</p>
                          <div className="bg-tactile-base border border-tactile-border rounded-xl p-6 space-y-4 opacity-30 pointer-events-none select-none shadow-tactile-inner max-w-xl mx-auto">
                            <div className="grid grid-cols-2 gap-6">
                              <div className="space-y-1">
                                <p className="text-[9px] font-mono text-tactile-primary uppercase tracking-widest">Metric Name</p>
                                <p className="text-xs text-tactile-text font-bold">Revenue</p>
                              </div>
                              <div className="space-y-1">
                                <p className="text-[9px] font-mono text-tactile-primary uppercase tracking-widest">SQL Logic</p>
                                <p className="text-xs font-mono text-tactile-text">{"SUM(amount)"}</p>
                              </div>
                            </div>
                          </div>
                          <p className="text-[11px] text-tactile-text/25 text-center font-medium">Click <span className="text-tactile-primary">+ Add Metric</span> to define your first business rule</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>

                <div className="flex gap-4 pt-8 border-t border-tactile-border">
                  <TactileButton type="submit" disabled={isLoading} className="px-8 py-3.5">
                    {isLoading ? <Loader2 className="w-5 h-5 animate-spin mr-3" /> : <Shield className="w-5 h-5 mr-3" />}
                    {editingSourceId ? "Update Configuration" : "Initialize Ingestion"}
                  </TactileButton>
                  <TactileButton variant="outline" onClick={() => {
                    setIsAdding(false);
                    setEditingSourceId(null);
                  }} className="px-8 py-3.5">
                    Cancel
                  </TactileButton>
                </div>
              </form>
            </section>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
              {isLoading ? (
                Array(4).fill(0).map((_, i) => (
                  <div key={i} className="h-56 bg-tactile-surface border border-tactile-border rounded-2xl animate-pulse" />
                ))
              ) : sources.length === 0 ? (
                <div className="col-span-full py-24 bg-tactile-surface border border-tactile-border rounded-2xl text-center shadow-tactile">
                  <div className="w-20 h-20 rounded-2xl bg-tactile-base border border-tactile-border flex items-center justify-center mx-auto mb-8 text-tactile-primary/20 shadow-tactile-inner">
                    <Database className="w-10 h-10" />
                  </div>
                  <h3 className="text-tactile-text font-bold text-2xl mb-3 tracking-tight">No data sources found</h3>
                  <p className="text-tactile-text/50 mb-10 text-lg max-w-sm mx-auto">Start by connecting your first database or warehouse to begin analysis.</p>
                  <TactileButton variant="outline" onClick={() => setIsAdding(true)} className="mx-auto px-8 py-3.5">
                    Add Your First Connector
                  </TactileButton>
                </div>
              ) : (
                sources.map((source) => (
                  <div key={source.source_id} className="group tactile-card p-8 hover:border-tactile-primary/40 hover:scale-[1.01] hover:shadow-2xl relative overflow-hidden flex flex-col justify-between h-full">
                    <div className="absolute top-0 right-0 p-6 opacity-0 group-hover:opacity-100 transition-all flex gap-2 translate-y-2 group-hover:translate-y-0">
                      <button 
                        onClick={() => handleSync(source.source_id)}
                        className="p-2.5 bg-tactile-base border border-tactile-border rounded-lg text-tactile-text/40 hover:text-tactile-primary hover:border-tactile-primary/50 transition-all cursor-pointer shadow-tactile"
                        title="Sync Schema Now"
                      >
                        <RefreshCw className={`w-4 h-4 ${source.status === 'syncing' ? 'animate-spin' : ''}`} />
                      </button>
                      <button 
                        onClick={() => startEdit(source)}
                        className="p-2.5 bg-tactile-base border border-tactile-border rounded-lg text-tactile-text/40 hover:text-tactile-primary hover:border-tactile-primary/50 transition-all cursor-pointer shadow-tactile"
                        title="Edit Source"
                      >
                        <Edit className="w-4 h-4" />
                      </button>
                      <button 
                        onClick={() => handleDelete(source.source_id)}
                        className="p-2.5 bg-tactile-base border border-tactile-border rounded-lg text-tactile-text/40 hover:text-tactile-warning hover:border-tactile-warning/50 transition-all cursor-pointer shadow-tactile"
                        title="Delete Source"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>

                    <div className="flex items-start gap-5 mb-8">
                      <div className="w-14 h-14 rounded-2xl bg-tactile-base flex items-center justify-center text-tactile-primary border border-tactile-border shadow-tactile-inner flex-shrink-0 transition-transform group-hover:scale-110">
                        {source.db_type === 'postgresql' ? (
                          <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/postgresql/postgresql-original.svg" className="w-8 h-8" alt="PostgreSQL" />
                        ) : source.db_type === 'mysql' ? (
                          <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/mysql/mysql-original.svg" className="w-8 h-8" alt="MySQL" />
                        ) : source.db_type === 'mongodb' ? (
                          <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/mongodb/mongodb-original.svg" className="w-8 h-8" alt="MongoDB" />
                        ) : source.db_type === 'gmail' ? (
                          <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/google/google-original.svg" className="w-8 h-8" alt="Gmail" />
                        ) : (
                          <Database className="w-8 h-8" />
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center justify-between mb-1.5">
                          <h3 className="text-xl font-bold text-tactile-text truncate group-hover:text-tactile-primary transition-colors">{source.name}</h3>
                        </div>
                        <div className="flex items-center gap-3">
                          <div className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full border text-[10px] font-bold uppercase tracking-widest ${
                            source.status === 'active' ? 'bg-tactile-primary/10 border-tactile-primary/20 text-tactile-primary' : 
                            source.status === 'failed' ? 'bg-tactile-warning/10 border-tactile-warning/20 text-tactile-warning' : 'bg-tactile-base border-tactile-border text-tactile-text/40'
                          }`}>
                            {source.status === 'active' && <Check className="w-3 h-3" />}
                            {source.status === 'syncing' && <Loader2 className="w-3 h-3 animate-spin" />}
                            {source.status === 'failed' && <AlertCircle className="w-3 h-3" />}
                            {source.status}
                          </div>
                          <span className="text-[10px] font-mono text-tactile-text/30 uppercase tracking-tighter">
                            {source.db_type}
                          </span>
                        </div>
                      </div>
                    </div>

                    <p className="text-sm text-tactile-text/60 leading-relaxed line-clamp-2 mb-8 h-10 group-hover:text-tactile-text/80 transition-colors">
                      {source.description || "No description provided."}
                    </p>

                    {source.status === 'failed' && source.error_message && (
                      <div className="mb-8 p-4 bg-tactile-warning/5 border border-tactile-warning/10 rounded-xl text-[11px] text-tactile-warning font-mono leading-relaxed line-clamp-3 shadow-tactile-inner">
                        ERROR: {source.error_message}
                      </div>
                    )}

                    <div className="flex items-center justify-between pt-6 border-t border-tactile-border mt-auto">
                      <span className="text-[10px] font-mono text-tactile-text/20 uppercase tracking-widest">
                        REF: {source.source_id.slice(0, 12)}
                      </span>
                      <button className="text-xs font-bold text-tactile-primary hover:text-tactile-primary-hover flex items-center gap-2 transition-all group-hover:gap-3">
                        EXPLORE SCHEMA <Plus className="w-3.5 h-3.5" />
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
