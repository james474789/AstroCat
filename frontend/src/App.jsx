import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import Layout from './components/layout/Layout';
import Dashboard from './pages/Dashboard';
import Search from './pages/Search';
import ImageDetail from './pages/ImageDetail';
import MetadataViewer from './pages/MetadataViewer';
import MetadataSearch from './pages/MetadataSearch';
import Catalogs from './pages/Catalogs';
import Stats from './pages/Stats';
import FitsStats from './pages/FitsStats';
import Admin from './pages/Admin';
import { AuthProvider } from './context/AuthContext';
import ProtectedRoute from './components/ProtectedRoute';
import Login from './pages/Login';
import Setup from './pages/Setup';
import { useAuth } from './context/AuthContext';
import ErrorBoundary from './components/common/ErrorBoundary';
import { Loader2 } from 'lucide-react';
import './index.css';


const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5 * 60 * 1000, // 5 minutes
      retry: 1,
    },
  },
});

const AppRoutes = () => {
  const { setupComplete, loading, isAuthenticated } = useAuth();

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', backgroundColor: '#0d1117' }}>
        <Loader2 className="spinner" size={48} color="#58a6ff" />
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
      <Route
        path="/*"
        element={
          !setupComplete ? <Navigate to="/setup" replace /> :
            <ProtectedRoute>
              <Layout>
                <Routes>
                  <Route path="/" element={<Dashboard />} />
                  <Route path="/search" element={<Search />} />
                  <Route path="/images/:id" element={<ImageDetail />} />
                  <Route path="/images/:id/metadata" element={<MetadataViewer />} />
                  <Route path="/metadata-search" element={<MetadataSearch />} />
                  <Route path="/catalogs" element={<Catalogs />} />
                  <Route path="/catalogs/:type/:designation" element={<Catalogs />} />
                  <Route path="/stats" element={<Stats />} />
                  <Route path="/stats/fits" element={<FitsStats />} />
                  <Route path="/admin" element={<ErrorBoundary><Admin /></ErrorBoundary>} />
                </Routes>
              </Layout>
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
          <AppRoutes />
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}


export default App;
