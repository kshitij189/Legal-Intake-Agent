import type { Metadata } from "next";

import { AuthProvider } from "@/components/AuthProvider";

import "./globals.css";

export const metadata: Metadata = {
  title: "Intake Review",
  description: "Human-review dashboard for the legal intake agent (fictional data).",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
