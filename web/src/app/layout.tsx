import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { AppStateProvider } from "@/components/AppState";
import { Shell } from "@/components/Shell";

import "./globals.css";

export const metadata: Metadata = {
  title: { default: "AlphaDots", template: "%s · AlphaDots" },
  description:
    "Point-in-time market sentiment research terminal. Simulated data; research only; not investment advice.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#0d0d0d" },
    { media: "(prefers-color-scheme: light)", color: "#f9f9f7" },
  ],
};

// Applies a saved light/dark choice before first paint, so there is no theme flash.
// A fixed string: nothing from the request or the data is interpolated into it.
const THEME_BOOTSTRAP = `try{var t=localStorage.getItem("alphadots-theme");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOTSTRAP }} />
      </head>
      <body>
        <AppStateProvider>
          <Shell>{children}</Shell>
        </AppStateProvider>
      </body>
    </html>
  );
}
