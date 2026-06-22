import type { Metadata } from "next";
import "./globals.css";

import { Toaster } from "@/components/ui/toaster";

export const metadata: Metadata = {
  title: "Revok Agent Framework Demo",
  description: "Real-time stale-memory detection — Agent Framework + Revok proxy",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-[#0f1117] text-slate-100 antialiased min-h-screen">
        {children}
        <Toaster />
      </body>
    </html>
  );
}
