import * as Tooltip from "@radix-ui/react-tooltip";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { BrowserRouter, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { LeftNav, MajorBanner, TopBar } from "./components/Chrome";
import { Drawers } from "./components/Drawers";
import { AppProvider, useApp } from "./lib/store";
import { AuditPage, BeforePage, ImpactPage, NotFound } from "./pages/MiscPages";
import { FleetPage, OpsPage } from "./pages/OpsPage";
import { HomePage, StorePage, StoresIndex } from "./pages/StorePage";

const qc = new QueryClient({ defaultOptions: { queries: { staleTime: 10_000, refetchOnWindowFocus: false, retry: 1 } } });

function Shell() {
  const { resetDemo, persona } = useApp();
  const nav = useNavigate();
  const loc = useLocation();
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (e.key === "R" && e.shiftKey && tag !== "INPUT" && tag !== "TEXTAREA") { e.preventDefault(); resetDemo(); nav("/"); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [resetDemo, nav]);
  if (loc.pathname.startsWith("/before")) return <BeforePage />;
  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <MajorBanner />
      <div className="flex min-h-0 flex-1">
        <LeftNav />
        <main className="min-w-0 flex-1 overflow-auto scroll-thin" key={persona?.user_id ?? "anon"}>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/stores" element={<StoresIndex />} />
            <Route path="/stores/:id" element={<StorePage />} />
            <Route path="/ops" element={<OpsPage />} />
            <Route path="/fleet" element={<FleetPage />} />
            <Route path="/impact" element={<ImpactPage />} />
            <Route path="/audit" element={<AuditPage />} />
            <Route path="*" element={<NotFound />} />
          </Routes>
        </main>
      </div>
      <Drawers />
    </div>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <Tooltip.Provider>
        <BrowserRouter>
          <AppProvider><Shell /></AppProvider>
        </BrowserRouter>
      </Tooltip.Provider>
    </QueryClientProvider>
  );
}
