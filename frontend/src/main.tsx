import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { TimeProvider } from "./time/TimeContext";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Revalidation is the browser's job here: every response carries an ETag,
      // so a refetch that has not changed costs a 304 and no body.
      //
      // Focus-refetch is on because polling is not: react-query stops the
      // interval in a hidden tab, so without this you would come back to a
      // graph up to POLL_MS stale. It costs nothing on historical data, which
      // has staleTime Infinity and so is never refetched on focus anyway.
      refetchOnWindowFocus: true,
      retry: (failureCount, error) => {
        // A 404 is an answer ("nothing at that instant"), not a transient
        // failure; retrying it just delays the empty state.
        if (error instanceof Error && "status" in error) {
          const status = (error as { status: number }).status;
          if (status >= 400 && status < 500) return false;
        }
        return failureCount < 2;
      },
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <TimeProvider>
        <App />
      </TimeProvider>
    </QueryClientProvider>
  </StrictMode>,
);
