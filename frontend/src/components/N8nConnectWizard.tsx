"use client";

import { useState, useEffect } from "react";
import { X, Check, Loader2, ExternalLink, ChevronRight } from "lucide-react";
import { getAuthHeaders, getApiUrl } from "@/lib/api";

// ── Types ──────────────────────────────────────────────────────────────────

interface CredentialField {
  key: string;
  label: string;
  placeholder: string;
  secret: boolean;
}

interface Service {
  id: string;
  label: string;
  icon: string;
  category: string;
  auth_type: "apikey" | "oauth2" | "basic" | "bearer";
  description: string;
  credential_fields: CredentialField[];
}

interface Props {
  tenantId: string;
  onDone: (sourceId: string, label: string) => void;
  onClose: () => void;
}

// ── Step components ────────────────────────────────────────────────────────

function ServiceCard({ svc, onClick }: { svc: Service; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-3 w-full px-4 py-3 rounded-lg border border-white/10 bg-white/5 hover:bg-white/10 hover:border-[#638A70]/50 transition-all text-left group"
    >
      <span className="text-2xl">{svc.icon}</span>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-semibold text-[#E6E1D8]">{svc.label}</p>
        <p className="text-xs text-[#9A9589] truncate">{svc.description}</p>
      </div>
      <ChevronRight className="w-4 h-4 text-[#9A9589] group-hover:text-[#638A70] shrink-0" />
    </button>
  );
}

// ── Main wizard ────────────────────────────────────────────────────────────

export function N8nConnectWizard({ tenantId, onDone, onClose }: Props) {
  const [step, setStep] = useState<"pick" | "configure" | "connecting" | "done">("pick");
  const [services, setServices] = useState<Service[]>([]);
  const [categories, setCategories] = useState<string[]>([]);
  const [selectedCategory, setSelectedCategory] = useState<string>("all");
  const [selected, setSelected] = useState<Service | null>(null);
  const [sourceName, setSourceName] = useState("");
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [oauthPending, setOauthPending] = useState(false);
  const [oauthCredentialId, setOauthCredentialId] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const headers = await getAuthHeaders();
        const res = await fetch(`${getApiUrl()}/api/n8n/services`, { headers });
        if (!res.ok) throw new Error("Failed to load services");
        const data = await res.json();
        setServices(data.services);
        setCategories(["all", ...data.categories]);
      } catch (e: any) {
        setError(e.message);
      }
    })();
  }, []);

  const filteredServices =
    selectedCategory === "all"
      ? services
      : services.filter((s) => s.category === selectedCategory);

  const handleSelect = (svc: Service) => {
    setSelected(svc);
    setSourceName(svc.label);
    setCredentials({});
    setError(null);
    setStep("configure");
  };

  const handleOAuth = async () => {
    if (!selected || !sourceName.trim()) return;
    setError(null);
    setOauthPending(true);
    try {
      const headers = await getAuthHeaders();
      const res = await fetch(`${getApiUrl()}/api/n8n/oauth/initiate`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({
          service_id: selected.id,
          source_name: sourceName,
          credentials,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || "OAuth initiation failed");
      const { credential_id, auth_url } = await res.json();
      if (!auth_url) throw new Error("n8n did not return an OAuth URL. Check that GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET are set in .env and that n8n is running.");
      setOauthCredentialId(credential_id);
      window.open(auth_url, "n8n-oauth", "width=600,height=700");
      // Listen for postMessage from /oauth/done page when auth completes
      const handler = (e: MessageEvent) => {
        if (e.data?.type === "n8n-oauth-complete") {
          window.removeEventListener("message", handler);
          if (e.data.credential_id) setOauthCredentialId(e.data.credential_id);
          setOauthPending(false);
        }
      };
      window.addEventListener("message", handler);
    } catch (e: any) {
      setError(e.message);
      setOauthPending(false);
    }
  };

  const handleProvision = async () => {
    if (!selected || !sourceName.trim()) return;
    setError(null);
    setStep("connecting");

    const sourceId = `n8n_${selected.id}_${Date.now()}`;
    const creds =
      selected.auth_type === "oauth2"
        ? { _id: oauthCredentialId }
        : credentials;

    try {
      const headers = await getAuthHeaders();
      const res = await fetch(`${getApiUrl()}/api/n8n/provision`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: JSON.stringify({
          service_id: selected.id,
          source_id: sourceId,
          source_name: sourceName,
          tenant_id: tenantId,
          credentials: creds,
        }),
      });
      if (!res.ok) throw new Error((await res.json()).detail || "Provision failed");
      setStep("done");
      setTimeout(() => onDone(sourceId, sourceName), 1200);
    } catch (e: any) {
      setError(e.message);
      setStep("configure");
    }
  };

  const canProvision =
    selected?.auth_type === "oauth2"
      ? !!oauthCredentialId
      : selected?.credential_fields.every((f) => !!credentials[f.key]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4">
      <div className="w-full max-w-lg bg-[#1E1E1C] border border-white/10 rounded-xl shadow-2xl flex flex-col max-h-[90vh]">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/10 shrink-0">
          <div>
            <h2 className="text-base font-semibold text-[#E6E1D8]">Connect a data source</h2>
            <p className="text-xs text-[#9A9589] mt-0.5">
              Powered by n8n — your credentials stay in n8n, never in Axiom
            </p>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-md hover:bg-white/10 transition-colors">
            <X className="w-4 h-4 text-[#9A9589]" />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4">

          {/* Step: Pick service */}
          {step === "pick" && (
            <div className="space-y-3">
              {/* Category filter */}
              <div className="flex gap-2 flex-wrap">
                {categories.map((cat) => (
                  <button
                    key={cat}
                    onClick={() => setSelectedCategory(cat)}
                    className={`px-3 py-1 text-xs rounded-full border transition-colors capitalize ${
                      selectedCategory === cat
                        ? "bg-[#638A70] border-[#638A70] text-[#1E1E1C] font-semibold"
                        : "border-white/10 text-[#9A9589] hover:border-white/30"
                    }`}
                  >
                    {cat}
                  </button>
                ))}
              </div>
              <div className="space-y-2">
                {filteredServices.map((svc) => (
                  <ServiceCard key={svc.id} svc={svc} onClick={() => handleSelect(svc)} />
                ))}
              </div>
            </div>
          )}

          {/* Step: Configure credentials */}
          {step === "configure" && selected && (
            <div className="space-y-4">
              <button
                onClick={() => { setStep("pick"); setSelected(null); }}
                className="text-xs text-[#9A9589] hover:text-[#E6E1D8] flex items-center gap-1"
              >
                ← Back
              </button>

              <div className="flex items-center gap-2">
                <span className="text-2xl">{selected.icon}</span>
                <span className="text-sm font-semibold text-[#E6E1D8]">{selected.label}</span>
                <span className="ml-auto text-xs px-2 py-0.5 rounded-full bg-white/10 text-[#9A9589]">
                  {selected.auth_type === "oauth2" ? "OAuth2" : "API Key"}
                </span>
              </div>

              {/* Source name */}
              <div>
                <label className="block text-xs text-[#9A9589] mb-1">Source name</label>
                <input
                  className="w-full bg-white/5 border border-white/10 rounded-md px-3 py-2 text-sm text-[#E6E1D8] placeholder-[#9A9589]/60 focus:outline-none focus:border-[#638A70]"
                  value={sourceName}
                  onChange={(e) => setSourceName(e.target.value)}
                  placeholder={`My ${selected.label}`}
                />
              </div>

              {/* OAuth2 path */}
              {selected.auth_type === "oauth2" && (
                <div className="space-y-3">
                  <p className="text-xs text-[#9A9589]">
                    Click below to authorize Axiom to read from {selected.label}.
                    You'll be redirected to authenticate — no credentials are stored in Axiom.
                  </p>
                  {!oauthCredentialId ? (
                    <button
                      onClick={handleOAuth}
                      disabled={oauthPending || !sourceName.trim()}
                      className="flex items-center gap-2 px-4 py-2.5 rounded-md bg-[#638A70] text-[#1E1E1C] font-semibold text-sm hover:bg-[#729E81] disabled:opacity-50 transition-colors"
                    >
                      {oauthPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <ExternalLink className="w-4 h-4" />}
                      {oauthPending ? "Waiting for authorization…" : `Authorize ${selected.label}`}
                    </button>
                  ) : (
                    <div className="flex items-center gap-2 text-sm text-[#638A70]">
                      <Check className="w-4 h-4" />
                      Authorization complete
                    </div>
                  )}
                </div>
              )}

              {/* API key / bearer path */}
              {selected.auth_type !== "oauth2" && selected.credential_fields.map((field) => (
                <div key={field.key}>
                  <label className="block text-xs text-[#9A9589] mb-1">{field.label}</label>
                  <input
                    type={field.secret ? "password" : "text"}
                    className="w-full bg-white/5 border border-white/10 rounded-md px-3 py-2 text-sm text-[#E6E1D8] placeholder-[#9A9589]/60 focus:outline-none focus:border-[#638A70] font-mono"
                    placeholder={field.placeholder}
                    value={credentials[field.key] || ""}
                    onChange={(e) => setCredentials((prev) => ({ ...prev, [field.key]: e.target.value }))}
                  />
                </div>
              ))}

              {error && <p className="text-xs text-red-400">{error}</p>}
            </div>
          )}

          {/* Step: Connecting */}
          {step === "connecting" && (
            <div className="flex flex-col items-center justify-center py-12 gap-3">
              <Loader2 className="w-8 h-8 text-[#638A70] animate-spin" />
              <p className="text-sm text-[#9A9589]">Provisioning n8n workflow…</p>
            </div>
          )}

          {/* Step: Done */}
          {step === "done" && (
            <div className="flex flex-col items-center justify-center py-12 gap-3">
              <div className="w-12 h-12 rounded-full bg-[#638A70]/20 flex items-center justify-center">
                <Check className="w-6 h-6 text-[#638A70]" />
              </div>
              <p className="text-sm font-semibold text-[#E6E1D8]">{sourceName} connected</p>
              <p className="text-xs text-[#9A9589]">Ingesting schema in the background…</p>
            </div>
          )}
        </div>

        {/* Footer */}
        {step === "configure" && (
          <div className="px-5 py-4 border-t border-white/10 shrink-0">
            <button
              onClick={handleProvision}
              disabled={!canProvision || !sourceName.trim()}
              className="w-full py-2.5 rounded-md bg-[#638A70] text-[#1E1E1C] font-semibold text-sm hover:bg-[#729E81] disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              Connect {selected?.label}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
