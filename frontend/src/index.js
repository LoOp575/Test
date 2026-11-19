import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import "./index.css";
import Layout from "./components/Layout";
import DashboardPage from "./pages/DashboardPage";
import TokenPage from "./pages/TokenPage";
import MethodologyPage from "./pages/MethodologyPage";
import CalibrationPage from "./pages/CalibrationPage";
import FokkerPlanckPage from "./pages/FokkerPlanckPage";
import FokkerPlanckV2Page from "./pages/FokkerPlanckV2Page";

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route element={<Layout />}>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/token/:symbol" element={<TokenPage />} />
          <Route path="/fokker-planck" element={<FokkerPlanckPage />} />
          <Route path="/fokker-planck/:symbol" element={<FokkerPlanckPage />} />
          <Route path="/fokker-planck-v2" element={<FokkerPlanckV2Page />} />
          <Route path="/fokker-planck-v2/:symbol" element={<FokkerPlanckV2Page />} />
          <Route path="/methodology" element={<MethodologyPage />} />
          <Route path="/calibration" element={<CalibrationPage />} />
          <Route path="*" element={<DashboardPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
