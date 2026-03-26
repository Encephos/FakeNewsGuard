"use client";

import { useState, useEffect, useCallback, useRef, useMemo } from "react";
import dynamic from "next/dynamic";
import { useI18n } from "../lib/i18n";
import {
  fetchGraphSearch,
  fetchGraphStats,
  fetchGraphNode,
} from "../lib/api";
import { GraphNode, GraphEdge, GraphStats, GraphNodeDetail } from "../lib/types";

// Dynamically import react-force-graph-2d to avoid SSR issues with canvas
const ForceGraph2D = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
  loading: () => (
    <div className="flex w-full h-full items-center justify-center">
      <div className="w-8 h-8 rounded-full border-2 border-border border-t-accent animate-spin" />
    </div>
  ),
});

// ── Node styling Configuration ──────────────────────────────────────
const NODE_COLORS: Record<string, string> = {
  CLAIM: "#c41e1e", // Red / Accent
  SOURCE: "#2563eb", // Blue
  ACTOR: "#10b981", // Green
};

const NODE_SIZES: Record<string, number> = {
  CLAIM: 5,
  SOURCE: 8,
  ACTOR: 6,
};

// ── Icons for node types ──────────────────────────────────────────
const TypeIcon = ({ type }: { type: string }) => {
  switch (type) {
    case "CLAIM": return <span className="text-accent">💬</span>;
    case "SOURCE": return <span className="text-blue-500">📰</span>;
    case "ACTOR": return <span className="text-emerald-500">👤</span>;
    default: return <span>•</span>;
  }
};

export default function NetworkPage() {
  const { t, ratings, claimRatings } = useI18n();
  const [stats, setStats] = useState<GraphStats | null>(null);
  const [graphData, setGraphData] = useState<{ nodes: any[]; links: any[] }>({ nodes: [], links: [] });
  const [loading, setLoading] = useState(true);
  
  // Search state
  const [searchQuery, setSearchQuery] = useState("");
  const [typeFilter, setTypeFilter] = useState<string>("");
  const [isSearching, setIsSearching] = useState(false);

  // Detail panel state
  const [selectedNode, setSelectedNode] = useState<GraphNodeDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Ref container for responsive graph sizing
  const containerRef = useRef<HTMLDivElement>(null);
  const [dimensions, setDimensions] = useState({ width: 800, height: 600 });
  const graphRef = useRef<any>(null);

  // Use Theme to determine background color dynamically across light/dark mode
  const [isDarkMode, setIsDarkMode] = useState(false);
  useEffect(() => {
    setIsDarkMode(document.documentElement.classList.contains("dark"));
    const observer = new MutationObserver((mutations) => {
      mutations.forEach((m) => {
        if (m.attributeName === "class") {
          setIsDarkMode(document.documentElement.classList.contains("dark"));
        }
      });
    });
    observer.observe(document.documentElement, { attributes: true });
    return () => observer.disconnect();
  }, []);

  // Set graph box dimensions
  useEffect(() => {
    if (containerRef.current) {
      setDimensions({
        width: containerRef.current.clientWidth,
        height: containerRef.current.clientHeight,
      });
    }
    const onResize = () => {
      if (containerRef.current) {
        setDimensions({
          width: containerRef.current.clientWidth,
          height: containerRef.current.clientHeight,
        });
      }
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  // Fetch initial graph stats
  useEffect(() => {
    fetchGraphStats()
      .then(setStats)
      .catch((err) => console.error("Failed to load graph stats:", err));
  }, []);

  // Load baseline graph nodes on mount or when search clears
  const loadGraph = useCallback(async (search = "", type = "") => {
    setIsSearching(true);
    try {
      const result = await fetchGraphSearch(type, search, 200);
      
      // Also fetch edges for these nodes if it's the default view, or just map them
      // Since fetchGraphSearch only returns nodes, we render them. For a more complete
      // initial graph, we'd need an endpoint that returns nodes AND edges. 
      // This is a simplified view of nodes.
      const nodes = result.nodes.map(n => ({ ...n, val: NODE_SIZES[n.type] || 4 }));
      
      // If we have selected a node, we might want to keep its specific edges,
      // but if doing a broad search, we will only show nodes as a scatter plot unless
      // we fetch random edges. For now we will just show the nodes.
      setGraphData({ nodes, links: [] });
      setSelectedNode(null);
    } catch (err) {
      console.error("Failed to fetch graph data:", err);
    } finally {
      setIsSearching(false);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadGraph();
  }, [loadGraph]);

  // Handle Search Input
  useEffect(() => {
    const timeout = setTimeout(() => {
      if (!loading && (searchQuery || typeFilter !== undefined)) {
        loadGraph(searchQuery, typeFilter);
      }
    }, 400);
    return () => clearTimeout(timeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, typeFilter]);

  // Handle Node Click -> Fetch Details -> Update Graph slightly to show connections
  const handleNodeClick = useCallback(async (node: any) => {
    if (!node.id) return;
    
    // Zoom to node
    if (graphRef.current) {
      graphRef.current.centerAt(node.x, node.y, 1000);
      graphRef.current.zoom(2.5, 1000);
    }

    setLoadingDetail(true);
    try {
      const detail = await fetchGraphNode(node.id);
      setSelectedNode(detail);
      
      // Enhance current graph data with this node's neighbors and edges
      setGraphData(prev => {
        const nodeMap = new Map(prev.nodes.map(n => [n.id, n]));
        
        // Add active node if missing
        if (!nodeMap.has(detail.node.id)) {
          nodeMap.set(detail.node.id, { ...detail.node, val: NODE_SIZES[detail.node.type] });
        }
        
        // Add neighbors
        detail.neighbors.forEach(n => {
          if (!nodeMap.has(n.id)) {
            nodeMap.set(n.id, { ...n, val: NODE_SIZES[n.type] });
          }
        });
        
        // Add links
        const linkMap = new Map(prev.links.map(l => [`${l.source.id || l.source}-${l.target.id || l.target}`, l]));
        detail.edges.forEach(e => {
          const key = `${e.source}-${e.target}`;
          if (!linkMap.has(key)) {
            linkMap.set(key, e);
          }
        });
        
        return {
          nodes: Array.from(nodeMap.values()),
          links: Array.from(linkMap.values())
        };
      });
      
    } catch (err) {
      console.error("Failed to load node details", err);
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  const closePanel = () => {
    setSelectedNode(null);
    if (graphRef.current) {
      graphRef.current.zoomToFit(400);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-64px)] relative overflow-hidden">
      
      {/* ── Top Bar: Search & Stats ─────────────────────────────────────────── */}
      <div className="relative z-10 px-4 pt-4 pb-2 flex flex-col sm:flex-row gap-3 items-center justify-between">
        
        <div className="flex bg-surface border border-border rounded-xl shadow-sm w-full sm:w-96 overflow-hidden focus-within:ring-2 focus-within:ring-accent/20 transition-shadow">
          <input
            type="text"
            placeholder={t("graph.searchPlaceholder")}
            className="flex-1 bg-transparent px-3 py-2 text-sm outline-none"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
          <select 
            className="bg-surface-hover border-l border-border px-3 py-2 text-sm font-mono text-text-secondary outline-none cursor-pointer"
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
          >
            <option value="">{t("graph.typeFilterFilter")}</option>
            <option value="CLAIM">{t("graph.nodeTypes.CLAIM")}</option>
            <option value="SOURCE">{t("graph.nodeTypes.SOURCE")}</option>
            <option value="ACTOR">{t("graph.nodeTypes.ACTOR")}</option>
          </select>
        </div>

        {stats && (
          <div className="flex gap-4 text-xs font-mono text-text-secondary bg-surface/80 backdrop-blur-md px-4 py-2 rounded-xl border border-border shadow-sm">
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-accent" />
              <span>{stats.nodes_by_type?.CLAIM || 0} {t("graph.stats.claims")}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-blue-500" />
              <span>{stats.nodes_by_type?.SOURCE || 0} {t("graph.stats.sources")}</span>
            </div>
            <div className="flex items-center gap-1.5">
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              <span>{stats.nodes_by_type?.ACTOR || 0} {t("graph.stats.actors")}</span>
            </div>
            <div className="pl-3 border-l border-border flex gap-3">
              <span>{stats.total_nodes.toLocaleString()} {t("graph.stats.nodes")}</span>
              <span>{stats.total_edges.toLocaleString()} {t("graph.stats.edges")}</span>
            </div>
          </div>
        )}
      </div>

      {/* ── Graph Canvas ────────────────────────────────────────────────────── */}
      <div ref={containerRef} className="flex-1 w-full bg-bg-primary relative" onClick={() => setSelectedNode(null)}>
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center">
            <span className="text-sm font-mono text-text-tertiary animate-pulse">{t("graph.loading")}</span>
          </div>
        ) : (
          <ForceGraph2D
            ref={graphRef}
            width={dimensions.width}
            height={dimensions.height}
            graphData={graphData}
            nodeLabel="label"
            nodeColor={(node: any) => 
               node.id === selectedNode?.node.id ? "#ffffff" : (NODE_COLORS[node.type] || "#888")
            }
            nodeRelSize={1.5}
            linkColor={() => isDarkMode ? "rgba(255,255,255,0.15)" : "rgba(0,0,0,0.1)"}
            linkWidth={(link: any) => 
               selectedNode?.edges.some(e => e.source === link.source.id && e.target === link.target.id) ? 2 : 1
            }
            onNodeClick={handleNodeClick}
            backgroundColor={isDarkMode ? "#111110" : "#f6f5f3"}
            nodeCanvasObject={(node: any, ctx, globalScale) => {
              const label = node.label;
              const fontSize = 12 / globalScale;
              ctx.font = `${fontSize}px Sans-Serif`;
              const textWidth = ctx.measureText(label).width;
              const bckgDimensions = [textWidth, fontSize].map(n => n + fontSize * 0.2); // some padding

              // Draw circle
              const isSelected = selectedNode?.node.id === node.id;
              ctx.beginPath();
              ctx.arc(node.x, node.y, (node.val || 4) + (isSelected ? 2 : 0), 0, 2 * Math.PI, false);
              ctx.fillStyle = isSelected ? (isDarkMode ? "#fff" : "#000") : (NODE_COLORS[node.type] || "#888");
              if (isSelected) {
                ctx.lineWidth = 2 / globalScale;
                ctx.strokeStyle = NODE_COLORS[node.type] || "#888";
                ctx.stroke();
              }
              ctx.fill();

              // Draw text label on zoom
              if (globalScale > 2) {
                ctx.fillStyle = isDarkMode ? "rgba(255, 255, 255, 0.9)" : "rgba(0, 0, 0, 0.8)";
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText(label, node.x, node.y + (node.val || 4) + fontSize);
              }
            }}
          />
        )}
        
        {/* Loading overlay for panel/search */}
        {(isSearching || loadingDetail) && !loading && (
          <div className="absolute top-4 left-4 z-10 glass-badge px-3 py-1 text-xs font-mono text-text-secondary animate-pulse shadow-sm">
            {t("admin.loading")}
          </div>
        )}
      </div>

      {/* ── Slide-in Detail Panel ───────────────────────────────────────────── */}
      <div 
        className={`absolute top-16 bottom-4 right-4 w-96 glass-panel rounded-2xl shadow-xl flex flex-col transition-transform duration-300 ease-in-out border border-border z-20 ${
          selectedNode ? "translate-x-0" : "translate-x-[110%]"
        }`}
        onClick={e => e.stopPropagation()}
      >
        {selectedNode && (
          <>
            <div className="px-5 py-4 border-b border-border flex justify-between items-start">
              <div>
                <div className="flex items-center gap-2 mb-1.5">
                  <TypeIcon type={selectedNode.node.type} />
                  <span className="text-xs font-mono font-medium opacity-70">
                    {t(`graph.nodeTypes.${selectedNode.node.type}`) || selectedNode.node.type}
                  </span>
                </div>
                <h3 className="font-semibold text-text-primary leading-tight line-clamp-3">
                  {selectedNode.node.label}
                </h3>
              </div>
              <button 
                onClick={closePanel}
                className="w-7 h-7 flex items-center justify-center rounded-lg hover:bg-surface-hover text-text-tertiary hover:text-text-primary transition-colors shrink-0"
              >
                ✕
              </button>
            </div>
            
            <div className="flex-1 overflow-y-auto p-5 scrollbar-thin">
              {/* Properties Box */}
              {selectedNode.node.properties && Object.keys(selectedNode.node.properties).length > 0 && (
                <div className="mb-6 space-y-3">
                  {Object.entries(selectedNode.node.properties).map(([key, value]) => {
                    // special handling for URL and Rating
                    if (key === "sample_url") {
                      return (
                        <div key={key}>
                          <a href={value} target="_blank" rel="noreferrer" className="text-sm text-blue-500 hover:underline break-all">
                            {value}
                          </a>
                        </div>
                      );
                    }
                    if (key === "rating" && value) {
                      return (
                        <div key={key} className="flex items-center gap-2">
                          <span className="text-xs text-text-tertiary w-24 shrink-0">{t("graph.detail.rating")}</span>
                          <span className={`text-xs px-2 py-0.5 rounded-md font-medium border
                            ${value === "TRUE" || value === "MOSTLY_TRUE" ? "bg-success/10 text-success border-success/20" : 
                              value === "FALSE" || value === "MOSTLY_FALSE" ? "bg-error/10 text-error border-error/20" : 
                              "bg-warning/10 text-warning border-warning/20"}`}
                          >
                           {claimRatings[value] || value}
                          </span>
                        </div>
                      );
                    }
                    if (key === "analysis_id") {
                      return (
                         <div key={key} className="mt-2 text-xs">
                           <a href={`/archiv/${value}`} className="text-accent hover:underline flex items-center gap-1">
                             {t("graph.detail.viewAnalysis")} ↘
                           </a>
                         </div>
                      );
                    }
                    
                    return (
                      <div key={key} className="flex gap-2">
                        <span className="text-xs text-text-tertiary w-24 shrink-0 capitalize">{key.replace('_', ' ')}</span>
                        <span className="text-sm font-medium">{value}</span>
                      </div>
                    );
                  })}
                </div>
              )}

              {/* Neighbors List */}
              <div>
                <h4 className="text-xs font-mono font-semibold text-text-secondary uppercase mb-3 pb-1 border-b border-border/50">
                  {t("graph.detail.neighbors").replace("{count}", selectedNode.neighbors.length.toString())}
                </h4>
                
                {selectedNode.neighbors.length === 0 ? (
                  <p className="text-sm text-text-tertiary italic">{t("graph.detail.noNeighbors")}</p>
                ) : (
                  <ul className="space-y-3">
                    {selectedNode.neighbors.map(neighbor => {
                      // Find relation label
                      const edge = selectedNode.edges.find(e => 
                        (e.source === selectedNode.node.id && e.target === neighbor.id) || 
                        (e.target === selectedNode.node.id && e.source === neighbor.id)
                      );
                      const tRelationKey = `graph.detail.relationLabels.${edge?.relation || 'unknown'}`;
                      const relationLabel = t(tRelationKey) !== tRelationKey ? t(tRelationKey) : edge?.relation;

                      return (
                        <li 
                          key={neighbor.id} 
                          className="p-3 bg-surface border border-border rounded-xl cursor-pointer hover:border-text-tertiary transition-colors"
                          onClick={() => handleNodeClick(neighbor)}
                        >
                          <div className="flex justify-between items-start mb-1.5 gap-2">
                            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-bg-tertiary text-text-secondary">
                              {relationLabel}
                            </span>
                            <TypeIcon type={neighbor.type} />
                          </div>
                          <p className="text-xs font-medium text-text-primary line-clamp-2">
                            {neighbor.label}
                          </p>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            </div>
          </>
        )}
      </div>

    </div>
  );
}
