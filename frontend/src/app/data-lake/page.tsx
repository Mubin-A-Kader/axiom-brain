"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import {
  Database, Waves, Plus, Minus, Loader2, AlertCircle,
  Cpu, Trash2, FolderPlus
} from "lucide-react";
import { fetchSources, fetchLakes, createLake, deleteLake, fetchLakeSources, addSourceToLake, removeSourceFromLake } from "../../lib/api";
import { Source, LakeOut } from "../../types";
import { createClient } from "@/lib/supabase/client";

const DB_ICONS: Record<string, React.ReactNode> = {
  postgresql: <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/postgresql/postgresql-original.svg" className="w-4 h-4" alt="PostgreSQL" />,
  mysql:      <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/mysql/mysql-original.svg" className="w-4 h-4" alt="MySQL" />,
  mongodb:    <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/mongodb/mongodb-original.svg" className="w-4 h-4" alt="MongoDB" />,
  gmail:      <img src="https://cdn.jsdelivr.net/gh/devicons/devicon@latest/icons/google/google-original.svg" className="w-4 h-4" alt="Gmail" />,
  mcp:        <Cpu size={16} className="text-[#9B8ABF]" />,
};

const DB_COLORS: Record<string, string> = {
  postgresql: "border-[#638A70]/40 bg-[#638A70]/8",
  mysql:      "border-[#7B9FA8]/40 bg-[#7B9FA8]/8",
  mongodb:    "border-[#A8876B]/40 bg-[#A8876B]/8",
  gmail:      "border-[#EA4335]/40 bg-[#EA4335]/8",
  mcp:        "border-[#9B8ABF]/40 bg-[#9B8ABF]/8",
};

function SourceCard({ source, inLake, onAdd, onRemove, loading }: {
  source: Source;
  inLake: boolean;
  onAdd: () => void;
  onRemove: () => void;
  loading: boolean;
}) {
  const colorClass = DB_COLORS[source.db_type] || "border-white/10 bg-white/5";

  return (
    <div className={`relative rounded-xl border p-4 transition-all duration-300 ${colorClass} ${
      inLake ? "opacity-100" : "opacity-60 hover:opacity-80"
    }`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <div className="shrink-0 w-7 h-7 rounded-lg bg-black/30 flex items-center justify-center">
            {DB_ICONS[source.db_type] || <Database size={16} className="text-[#888]" />}
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-[#E6E1D8] truncate">{source.source_id}</p>
            <p className="text-xs text-[#888] capitalize">{source.db_type}</p>
          </div>
        </div>

        <button
          onClick={inLake ? onRemove : onAdd}
          disabled={loading}
          className={`shrink-0 w-7 h-7 rounded-full flex items-center justify-center transition-all ${
            inLake
              ? "bg-[#C26D5C]/20 hover:bg-[#C26D5C]/40 text-[#C26D5C]"
              : "bg-[#638A70]/20 hover:bg-[#638A70]/40 text-[#638A70]"
          } ${loading ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
        >
          {loading ? <Loader2 size={12} className="animate-spin" /> :
            inLake ? <Minus size={12} /> : <Plus size={12} />}
        </button>
      </div>

      {source.description && (
        <p className="mt-2 text-xs text-[#666] line-clamp-2">{source.description}</p>
      )}

      <div className="mt-3 flex items-center justify-between">
        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
          source.status === "active"
            ? "bg-[#638A70]/20 text-[#638A70]"
            : source.status === "syncing"
            ? "bg-yellow-500/20 text-yellow-400"
            : "bg-red-500/20 text-red-400"
        }`}>
          {source.status}
        </span>
        {inLake && (
          <span className="text-[10px] text-[#638A70] flex items-center gap-1">
            <Waves size={10} /> In Lake
          </span>
        )}
      </div>
    </div>
  );
}

export default function DataLakePage() {
  const router = useRouter();
  const [tenantId, setTenantId] = useState<string | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [lakes, setLakes] = useState<LakeOut[]>([]);
  const [selectedLakeId, setSelectedLakeId] = useState<string | null>(null);
  const [lakeSources, setLakeSources] = useState<string[]>([]);
  
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<Set<string>>(new Set());
  const [error, setError] = useState<string | null>(null);

  const [newLakeName, setNewLakeName] = useState("");
  const [isCreating, setIsCreating] = useState(false);

  useEffect(() => {
    const supabase = createClient();
    supabase.auth.getSession().then(async ({ data: { session } }) => {
      if (!session) { router.push("/login"); return; }
      try {
        const hostname = typeof window !== 'undefined' ? window.location.hostname : '127.0.0.1';
        const API_URL = process.env.NEXT_PUBLIC_API_URL || `http://${hostname}:8080`;
        const tenantRes = await fetch(`${API_URL}/api/tenant`, {
          headers: { Authorization: `Bearer ${session.access_token}` }
        });
        const tenantData = await tenantRes.json();
        const tid = tenantData?.id;
        if (!tid) return;
        setTenantId(tid);

        const [srcs, lks] = await Promise.all([
          fetchSources(tid),
          fetchLakes(tid)
        ]);

        setSources(srcs);
        setLakes(lks);
        
        if (lks.length > 0) {
          setSelectedLakeId(lks[0].id);
        }
      } catch (e: any) {
        console.error("Error in Data Lake page initialization:", e);
        setError(e.message);
      } finally {
        setLoading(false);
      }
    });
  }, [router]);

  useEffect(() => {
    if (selectedLakeId) {
      fetchLakeSources(selectedLakeId).then(data => {
        setLakeSources(data.sources.map((s: any) => s.source_id));
      }).catch(err => {
        console.error("Failed to fetch lake sources", err);
      });
    } else {
      setLakeSources([]);
    }
  }, [selectedLakeId]);

  const handleCreateLake = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tenantId || !newLakeName.trim()) return;
    setIsCreating(true);
    try {
      const lake = await createLake(tenantId, { name: newLakeName });
      setLakes(prev => [...prev, lake]);
      setSelectedLakeId(lake.id);
      setNewLakeName("");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setIsCreating(false);
    }
  };

  const handleDeleteLake = async (lakeId: string) => {
    if (!tenantId || !confirm("Delete this data lake? Connections will remain but the group will be removed.")) return;
    try {
      await deleteLake(tenantId, lakeId);
      setLakes(prev => prev.filter(l => l.id !== lakeId));
      if (selectedLakeId === lakeId) {
        setSelectedLakeId(lakes.find(l => l.id !== lakeId)?.id || null);
      }
    } catch (err: any) {
      setError(err.message);
    }
  };

  const handleAdd = async (rawSourceId: string) => {
    if (!selectedLakeId) return;
    const sourceId = decodeURIComponent(rawSourceId);
    setActionLoading(prev => new Set(prev).add(sourceId));
    try {
      await addSourceToLake(selectedLakeId, sourceId);
      setLakeSources(prev => [...prev, sourceId]);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setActionLoading(prev => { const n = new Set(prev); n.delete(sourceId); return n; });
    }
  };

  const handleRemove = async (rawSourceId: string) => {
    if (!selectedLakeId) return;
    const sourceId = decodeURIComponent(rawSourceId);
    setActionLoading(prev => new Set(prev).add(sourceId));
    try {
      await removeSourceFromLake(selectedLakeId, sourceId);
      setLakeSources(prev => prev.filter(id => id !== sourceId));
    } catch (e: any) {
      setError(e.message);
    } finally {
      setActionLoading(prev => { const n = new Set(prev); n.delete(sourceId); return n; });
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full min-h-screen bg-[#1E1E1C]">
        <Loader2 className="animate-spin text-[#638A70]" size={32} />
      </div>
    );
  }

  const activeLake = lakes.find(l => l.id === selectedLakeId);
  const lakeSet = new Set(lakeSources);
  const inLake = sources.filter(s => lakeSet.has(s.source_id));
  const available = sources.filter(s => !lakeSet.has(s.source_id));

  return (
    <div className="h-full flex flex-col relative overflow-hidden bg-[#1E1E1C]">
      <div className="flex-1 overflow-y-auto scroll-smooth custom-scrollbar p-6">
        <div className="max-w-6xl mx-auto pb-24">
          <div className="flex items-center justify-between mb-8">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-xl bg-[#638A70]/20 flex items-center justify-center">
                <Waves size={20} className="text-[#638A70]" />
              </div>
              <div>
                <h1 className="text-xl font-bold text-[#E6E1D8]">Intelligence Lakes</h1>
                <p className="text-xs text-[#666]">Curate multiple source pools for targeted analysis</p>
              </div>
            </div>

            <form onSubmit={handleCreateLake} className="flex items-center gap-2">
              <input 
                placeholder="New lake name..."
                className="bg-[#2A2927] border border-white/5 rounded-lg px-3 py-1.5 text-xs outline-none focus:border-[#638A70]/50"
                value={newLakeName}
                onChange={e => setNewLakeName(e.target.value)}
              />
              <button 
                type="submit"
                disabled={isCreating || !newLakeName.trim()}
                className="bg-[#638A70]/20 hover:bg-[#638A70]/30 text-[#638A70] px-3 py-1.5 rounded-lg text-xs font-bold flex items-center gap-2 transition-all disabled:opacity-50"
              >
                <FolderPlus size={14} /> Create Lake
              </button>
            </form>
          </div>

          {error && (
            <div className="mb-6 flex items-center gap-2 text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-4 py-2">
              <AlertCircle size={14} /> {error}
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-4 gap-8">
            {/* Sidebar: Lake List */}
            <div className="lg:col-span-1 space-y-2">
              <h2 className="text-[10px] font-mono text-[#E6E1D8]/30 uppercase tracking-widest mb-4 ml-1">Your Lakes</h2>
              {lakes.map(lake => (
                <div 
                  key={lake.id}
                  onClick={() => setSelectedLakeId(lake.id)}
                  className={`group flex items-center justify-between p-3 rounded-xl border transition-all cursor-pointer ${
                    selectedLakeId === lake.id
                      ? "bg-[#638A70]/10 border-[#638A70]/30 text-[#638A70]"
                      : "bg-white/[0.02] border-white/[0.05] text-[#888] hover:bg-white/[0.04] hover:text-[#E6E1D8]"
                  }`}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <Waves size={14} className={selectedLakeId === lake.id ? "text-[#638A70]" : "text-[#555]"} />
                    <span className="text-sm font-semibold truncate">{lake.name}</span>
                  </div>
                  <button 
                    onClick={(e) => { e.stopPropagation(); handleDeleteLake(lake.id); }}
                    className="opacity-0 group-hover:opacity-100 p-1 hover:text-red-400 transition-all"
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
              {lakes.length === 0 && (
                <p className="text-xs text-[#555] italic p-2 text-center">No lakes created yet</p>
              )}
            </div>

            {/* Main Content: Lake Editor */}
            <div className="lg:col-span-3 space-y-8">
              {!selectedLakeId ? (
                <div className="flex flex-col items-center justify-center h-[400px] border border-dashed border-white/5 rounded-2xl bg-white/[0.01]">
                  <Waves size={40} className="text-white/5 mb-4" />
                  <p className="text-[#555] text-sm font-medium">Select a lake to manage its source scope</p>
                </div>
              ) : (
                <>
                  {/* Stats bar */}
                  <div className="flex items-center gap-6 p-4 rounded-xl bg-white/[0.03] border border-white/[0.06]">
                    <div className="flex items-center gap-2">
                      <Database size={14} className="text-[#888]" />
                      <span className="text-xs text-[#888]">Total sources</span>
                      <span className="text-sm font-bold text-[#E6E1D8]">{sources.length}</span>
                    </div>
                    <div className="w-px h-4 bg-white/10" />
                    <div className="flex items-center gap-2">
                      <Waves size={14} className="text-[#638A70]" />
                      <span className="text-xs text-[#888]">In {activeLake?.name}</span>
                      <span className="text-sm font-bold text-[#638A70]">{lakeSources.length}</span>
                    </div>
                    <div className="ml-auto flex items-center gap-2">
                      <span className="text-[10px] text-[#666] italic">
                        {lakeSources.length === 0 ? "Defaulting to all active sources" : `Scoped to ${lakeSources.length} sources`}
                      </span>
                    </div>
                  </div>

                  <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                    {/* Active Lake Column */}
                    <div>
                      <div className="flex items-center gap-2 mb-4">
                        <Waves size={16} className="text-[#638A70]" />
                        <h2 className="text-sm font-semibold text-[#E6E1D8]">{activeLake?.name} Scope</h2>
                      </div>

                      <div className="relative rounded-2xl border border-[#638A70]/20 bg-[#638A70]/[0.04] min-h-[400px] p-4">
                        {inLake.length === 0 ? (
                          <div className="flex flex-col items-center justify-center h-64 text-center">
                            <Waves size={32} className="text-[#638A70]/30 mb-3" />
                            <p className="text-sm text-[#555]">Lake is empty</p>
                            <p className="text-xs text-[#444] mt-1">Add available sources to define this pool</p>
                          </div>
                        ) : (
                          <div className="grid grid-cols-1 gap-3 relative z-10">
                            {inLake.map(source => (
                              <SourceCard
                                key={source.source_id}
                                source={source}
                                inLake={true}
                                onAdd={() => handleAdd(source.source_id)}
                                onRemove={() => handleRemove(source.source_id)}
                                loading={actionLoading.has(source.source_id)}
                              />
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Available Sources Column */}
                    <div>
                      <div className="flex items-center gap-2 mb-4">
                        <Database size={16} className="text-[#888]" />
                        <h2 className="text-sm font-semibold text-[#E6E1D8]">Add Sources</h2>
                      </div>

                      <div className="rounded-2xl border border-white/[0.06] bg-white/[0.02] min-h-[400px] p-4">
                        {available.length === 0 ? (
                          <div className="flex flex-col items-center justify-center h-64 text-center">
                            <Database size={32} className="text-[#555]/30 mb-3" />
                            <p className="text-sm text-[#555]">All sources added</p>
                          </div>
                        ) : (
                          <div className="grid grid-cols-1 gap-3">
                            {available.map(source => (
                              <SourceCard
                                key={source.source_id}
                                source={source}
                                inLake={false}
                                onAdd={() => handleAdd(source.source_id)}
                                onRemove={() => handleRemove(source.source_id)}
                                loading={actionLoading.has(source.source_id)}
                              />
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
