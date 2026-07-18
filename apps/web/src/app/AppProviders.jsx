import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { NotificationProvider } from "./Notifications.jsx";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 15_000, retry: 1, refetchOnWindowFocus: false },
    mutations: { retry: 0 }
  }
});

export function AppProviders({ children }) {
  return <QueryClientProvider client={queryClient}><NotificationProvider>{children}</NotificationProvider></QueryClientProvider>;
}
