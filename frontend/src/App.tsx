import { Routes, Route, Navigate } from "react-router-dom";
import { NavHeader } from "./components/NavHeader";
import { SingleUrlPage } from "./pages/SingleUrlPage";
import { BatchFilePage } from "./pages/BatchFilePage";

export function App() {
  return (
    <div className="min-h-screen flex flex-col font-sans relative overflow-hidden">
      <NavHeader />
      <main className="flex-1 flex flex-col">
        <Routes>
          <Route path="/" element={<Navigate to="/single-url" replace />} />
          <Route path="/single-url" element={<SingleUrlPage />} />
          <Route path="/batch" element={<BatchFilePage />} />
        </Routes>
      </main>
    </div>
  );
}
