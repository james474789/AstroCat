import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { lazy, Suspense } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Layout from './components/layout/Layout';
import Dashboard from './pages/Dashboard';
import Search from './pages/Search';
import ImageDetail from './pages/ImageDetail';
import MetadataSearchRedirect from './pages/MetadataSearchRedirect';
import Catalogs from './pages/Catalogs';
import Tonight from './pages/Tonight';
import Targets from './pages/Targets';
import TargetDetail from './pages/TargetDetail';
import FitsStats from './pages/FitsStats';
import Admin from './pages/Admin';
import Equipment from './pages/Equipment';
import NightReport from './pages/NightReport';
import { AuthProvider } from './context/AuthContext';
import { QualityUnitsProvider } from './context/QualityUnitsContext';
import ProtectedRoute from './components/ProtectedRoute';
import Login from './pages/Login';
import Setup from './pages/Setup';
import { useAuth } from './context/AuthContext';
import ErrorBoundary from './components/common/ErrorBoundary';
import { ConfirmProvider, ToastProvider } from './components/ui';
import { Loader2 } from 'lucide-react';
import './index.css';

// V1: OpenSeadragon (~200 KB) is only downloaded when the full-resolution viewer is opened
const FullResViewer = lazy(() => import('./pages/FullResViewer'));


const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      retry: 1,
    },
  },
});

const AppRoutes = () => {
  const { setupComplete, loading, isAuthenticated, error } = useAuth();

  if (loading) {
    return (
      <div className="app-screen">
        <Loader2 className="icon-spin app-screen-spinner" size={48} />
      </div>
    );
  }

  if (error) {
    return (
      <div className="app-screen app-screen-col">
        <div className="app-error-card">
          <h2>Connection Error</h2>
          <p>{error}</p>
          <button className="btn btn-primary" onClick={() => window.location.reload()}>
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <Routes>
      <Route path="/setup" element={setupComplete ? <Navigate to="/" replace /> : <Setup />} />
      <Route
        path="/login"
        element={
          !setupComplete ? <Navigate to="/setup" replace /> :
            isAuthenticated ? <Navigate to="/" replace /> :
              <Login />
        }
      />
      {/* V1: full-screen viewer, outside Layout so it owns the whole viewport */}
      <Route
        path="/images/:id/view"
        element={
          !setupComplete ? <Navigate to="/setup" replace /> :
            <ProtectedRoute>
              <ErrorBoundary>
                <Suspense fallback={<div className="app-screen app-screen-viewer"><Loader2 className="icon-spin app-screen-spinner" size={48} /></div>}>
                  <FullResViewer />
                </Suspense>
              </ErrorBoundary>
            </ProtectedRoute>
        }
      />
      <Route
        path="/*"
        element={
          !setupComplete ? <Navigate to="/setup" replace /> :
            <ProtectedRoute>
              <QualityUnitsProvider>
              <Layout>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/search" element={<Search />} />
                  <Route path="/images/:id" element={<ImageDetail />} />
                  <Route path="/images/:id/metadata" element={<ImageDetail inspector />} />
                  <Route path="/metadata-search" element={<MetadataSearchRedirect />} />
                  <Route path="/catalogs" element={<Catalogs />} />
                  <Route path="/catalogs/:type/:designation" element={<Catalogs />} />
                  {/* R1: inserted before Targets per docs/design/R1-recommendation-engine.md §8 */}
                  <Route path="/tonight" element={<ErrorBoundary><Tonight /></ErrorBoundary>} />
                  <Route path="/targets" element={<Targets />} />
                  <Route path="/targets/:targetKey" element={<TargetDetail />} />
                  {/* Q1c: star quality through a night */}
                  <Route path="/nights" element={<ErrorBoundary><NightReport /></ErrorBoundary>} />
                  <Route path="/nights/:night" element={<ErrorBoundary><NightReport /></ErrorBoundary>} />
                  {/* R0: inserted after Targets per docs/design/README.md §4 */}
                  <Route path="/equipment" element={<ErrorBoundary><Equipment /></ErrorBoundary>} />
                  <Route path="/stats" element={<FitsStats />} />
                  <Route path="/stats/fits" element={<Navigate to="/stats" replace />} />
                  <Route path="/admin" element={<ErrorBoundary><Admin /></ErrorBoundary>} />
                </Routes>
              </Layout>
              </QualityUnitsProvider>
            </ProtectedRoute>
        }
      />
    </Routes>
  );
};

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <ToastProvider>
            <ConfirmProvider>
              <AppRoutes />
            </ConfirmProvider>
          </ToastProvider>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}


export default App;
