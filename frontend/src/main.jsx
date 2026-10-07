import React from 'react';
import ReactDOM from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import App from './App.jsx';
import { AuthProvider } from './hooks/useAuth.jsx';
// Phase S9 Part 2: inside AuthProvider, because it reads the session to know
// whose branding to fetch; outside App, because it themes every route.
import { BrandingProvider } from './hooks/useBranding.jsx';
import './index.css';

// Data fetching on React Query (cache + revalidate). Tuned for cross-account
// freshness: short stale window + refetch when the tab/window regains focus or
// the network reconnects, so switching back to a tab pulls fresh data.
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: true,
      refetchOnReconnect: true,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrandingProvider>
          <App />
        </BrandingProvider>
      </AuthProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
