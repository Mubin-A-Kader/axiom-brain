"use client";

import React, { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AlertCircle, ChevronDown, ChevronRight, Code2, Download, FileCode2, Loader2, Play } from "lucide-react";
import { downloadArtifact, fetchArtifact, rerunArtifact } from "../lib/api";
import { NotebookArtifact, NotebookCell, NotebookCellOutput, NotebookDocument } from "../types";

interface NotebookArtifactViewerProps {
  artifact: NotebookArtifact;
}

function sourceToText(source: string | string[] | undefined): string {
  if (Array.isArray(source)) return source.join("");
  return source || "";
}

function outputText(output: NotebookCellOutput): string {
  if (typeof output?.text === "string") return output.text;
  if (Array.isArray(output?.text)) return output.text.join("");
  const plain = output?.data?.["text/plain"];
  if (Array.isArray(plain)) return plain.join("");
  return typeof plain === "string" ? plain : "";
}

function outputHtml(output: NotebookCellOutput): string {
  const html = output?.html || output?.data?.["text/html"];
  if (Array.isArray(html)) return html.join("");
  return typeof html === "string" ? html : "";
}

function outputImage(output: NotebookCellOutput & { data_url?: string }): string {
  if (output.data_url) return output.data_url;
  const png = output?.data?.["image/png"];
  return typeof png === "string" ? `data:image/png;base64,${png}` : "";
}

function isInteractiveChart(html: string): boolean {
  const lower = html.toLowerCase();
  return lower.includes("plotly") || lower.includes("echarts") || lower.includes("vega") || lower.includes("bokeh");
}

// Auto-resizing iframe for interactive Plotly/ECharts charts
function ChartFrame({ html }: { html: string }) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState(420);

  useEffect(() => {
    const iframe = iframeRef.current;
    if (!iframe) return;
    const onLoad = () => {
      try {
        const body = iframe.contentDocument?.body;
        if (body) {
          const h = body.scrollHeight;
          if (h > 0) setHeight(Math.min(Math.max(h + 16, 300), 640));
        }
      } catch {
        // cross-origin or not ready — keep default height
      }
    };
    iframe.addEventListener("load", onLoad);
    return () => iframe.removeEventListener("load", onLoad);
  }, [html]);

  return (
    <iframe
      ref={iframeRef}
      title="Interactive chart"
      // allow-scripts is required for Plotly/ECharts interactivity
      sandbox="allow-scripts"
      srcDoc={`<!DOCTYPE html><html><head><meta charset="utf-8"><style>*{margin:0;padding:0;box-sizing:border-box}body{background:#1E1E1C;color:#E6E1D8;font:12px system-ui}</style></head><body>${html}</body></html>`}
      style={{ height }}
      className="w-full border-0"
      scrolling="no"
    />
  );
}

// Table/HTML output without scripts
function TableFrame({ html }: { html: string }) {
  return (
    <iframe
      title="Table output"
      sandbox=""
      srcDoc={`<!DOCTYPE html><html><head><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:#1E1E1C;color:#E6E1D8;font:12px system-ui;padding:12px}table{border-collapse:collapse;width:100%;font-size:11px}th{background:rgba(99,138,112,.12);color:#638A70;font-weight:600;text-align:left;padding:6px 10px;border-bottom:1px solid rgba(255,255,255,.08)}td{padding:5px 10px;border-bottom:1px solid rgba(255,255,255,.05);color:rgba(230,225,216,.75)}</style></head><body>${html}</body></html>`}
      className="w-full border-0 min-h-48"
      style={{ minHeight: 120 }}
    />
  );
}

function CellOutputs({ outputs }: { outputs: NotebookCellOutput[] }) {
  return (
    <>
      {outputs.map((output, i) => {
        const image = outputImage(output as NotebookCellOutput & { data_url?: string });
        const html = outputHtml(output);
        const text = outputText(output);

        if (output.output_type === "error") {
          return (
            <div key={i} className="rounded-lg border border-[#C26D5C]/20 bg-[#C26D5C]/5 p-3">
              <pre className="text-[11px] leading-relaxed text-[#C26D5C] overflow-x-auto">
                {output.ename}: {output.evalue}
              </pre>
            </div>
          );
        }

        if (image) {
          return (
            <div key={i} className="rounded-lg border border-white/5 overflow-hidden bg-white">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={image} alt={`Chart output ${i + 1}`} className="w-full" />
            </div>
          );
        }

        if (html) {
          return (
            <div key={i} className="rounded-lg border border-white/5 overflow-hidden">
              {isInteractiveChart(html) ? <ChartFrame html={html} /> : <TableFrame html={html} />}
            </div>
          );
        }

        if (text && text.trim()) {
          return (
            <div key={i} className="rounded-lg border border-white/5 bg-[#2A2927]/60 px-3 py-2">
              <pre className="text-[11px] leading-relaxed text-[#E6E1D8]/60 overflow-x-auto whitespace-pre-wrap">
                {text}
              </pre>
            </div>
          );
        }

        return null;
      })}
    </>
  );
}

function CodeCell({ cell, index, showCode }: { cell: NotebookCell; index: number; showCode: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const source = sourceToText(cell.source);
  const outputs = cell.outputs || [];
  const hasOutputs = outputs.length > 0;

  // only show if code toggle is on, or it has outputs
  if (!showCode && !hasOutputs) return null;

  return (
    <div className="space-y-2">
      {showCode && (
        <div>
          <button
            onClick={() => setExpanded((v) => !v)}
            className="flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest text-[#638A70]/50 hover:text-[#638A70]/80 transition-colors"
          >
            {expanded ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
            <Code2 className="w-3 h-3" />
            Cell {index + 1}
          </button>
          {expanded && (
            <pre className="mt-2 overflow-x-auto rounded-lg bg-black/30 border border-white/5 p-3 text-[11px] leading-relaxed text-[#E6E1D8]/55">
              <code>{source}</code>
            </pre>
          )}
        </div>
      )}
      {hasOutputs && <CellOutputs outputs={outputs} />}
    </div>
  );
}

export function NotebookArtifactViewer({ artifact }: NotebookArtifactViewerProps) {
  const [currentArtifact, setCurrentArtifact] = useState<NotebookArtifact>(artifact);
  const [notebook, setNotebook] = useState<NotebookDocument | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showCode, setShowCode] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function loadNotebook() {
      setError(null);
      setIsLoading(true);
      try {
        const data = await fetchArtifact(artifact.artifact_id);
        if (!cancelled) {
          setCurrentArtifact(data.artifact);
          setNotebook(data.notebook);
        }
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : "Failed to load notebook.";
        if (!cancelled) setError(message);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    loadNotebook();
    return () => { cancelled = true; };
  }, [artifact.artifact_id]);

  const displayArtifact =
    currentArtifact.artifact_id === artifact.artifact_id ? currentArtifact : artifact;
  const cells = useMemo<NotebookCell[]>(() => notebook?.cells || [], [notebook]);

  const handleRerun = async () => {
    setError(null);
    setIsLoading(true);
    try {
      const data = await rerunArtifact(displayArtifact.artifact_id);
      setCurrentArtifact(data.artifact);
      setNotebook(data.notebook);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to rerun notebook.");
    } finally {
      setIsLoading(false);
    }
  };

  const handleDownload = async () => {
    setError(null);
    try {
      await downloadArtifact(displayArtifact.artifact_id);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to download notebook.");
    }
  };

  return (
    <div className="bg-[#1E1E1C] border border-white/5 rounded-xl overflow-hidden shadow-xl">
      <div className="flex items-center justify-between gap-4 border-b border-white/5 px-4 py-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="h-8 w-8 rounded-lg bg-[#638A70]/10 flex items-center justify-center">
            <FileCode2 className="w-4 h-4 text-[#638A70]" />
          </div>
          <h3 className="text-xs font-bold uppercase tracking-[0.16em] text-[#E6E1D8]">Analysis Notebook</h3>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setShowCode((v) => !v)}
            className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md border text-[10px] font-bold uppercase tracking-wider transition-colors ${
              showCode
                ? "border-[#638A70]/40 bg-[#638A70]/10 text-[#638A70]"
                : "border-white/5 bg-[#2A2927] text-[#E6E1D8]/40 hover:text-[#E6E1D8]/70"
            }`}
            title="Toggle code visibility"
          >
            <Code2 className="w-3 h-3" />
            Code
          </button>
          <span className={`px-2 py-1 rounded-md text-[10px] font-bold uppercase tracking-wider ${
            displayArtifact.status === "completed"
              ? "bg-[#638A70]/10 text-[#638A70]"
              : "bg-[#C26D5C]/10 text-[#C26D5C]"
          }`}>
            {displayArtifact.status}
          </span>
          <button
            onClick={handleRerun}
            disabled={isLoading}
            className="h-8 w-8 rounded-md border border-white/5 bg-[#2A2927] text-[#E6E1D8]/50 hover:text-[#638A70] disabled:opacity-40 flex items-center justify-center"
            title="Rerun notebook"
          >
            {isLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          </button>
          <button
            onClick={handleDownload}
            className="h-8 w-8 rounded-md border border-white/5 bg-[#2A2927] text-[#E6E1D8]/50 hover:text-[#638A70] flex items-center justify-center"
            title="Download .ipynb"
          >
            <Download className="w-4 h-4" />
          </button>
        </div>
      </div>

      {(error || displayArtifact.execution_error) && (
        <div className="m-4 rounded-lg border border-[#C26D5C]/20 bg-[#C26D5C]/5 p-3 flex gap-3">
          <AlertCircle className="w-4 h-4 text-[#C26D5C] flex-shrink-0 mt-0.5" />
          <p className="text-xs leading-relaxed text-[#E6E1D8]/70">
            {error || displayArtifact.execution_error}
          </p>
        </div>
      )}

      <div className="divide-y divide-white/5">
        {isLoading && !notebook && (
          <div className="p-8 flex items-center justify-center gap-3 text-[#E6E1D8]/40 text-sm">
            <Loader2 className="w-4 h-4 animate-spin" />
            Loading notebook
          </div>
        )}

        {cells.map((cell, index) => {
          const source = sourceToText(cell.source);
          if (cell.cell_type === "markdown") {
            return (
              <div key={index} className="px-4 py-3">
                <div className="prose prose-sm prose-zinc prose-invert max-w-none text-[#E6E1D8]/80">
                  <ReactMarkdown remarkPlugins={[remarkGfm]}>{source}</ReactMarkdown>
                </div>
              </div>
            );
          }
          return (
            <div key={index} className="px-4 py-3">
              <CodeCell cell={cell} index={index} showCode={showCode} />
            </div>
          );
        })}
      </div>
    </div>
  );
}
